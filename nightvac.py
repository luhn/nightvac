import argparse
import logging
from dataclasses import dataclass
from time import time as unix_timestamp

import psycopg


@dataclass(frozen=True)
class Args:
    conninfo: str
    timeout: int = 20 * 60
    cost_delay: int = 2
    cost_limit: int = 200
    threshold: int = 50
    scale_factor: float = 0.05
    insert_threshold: int = 1000
    insert_scale_factor: float = 0.1
    freeze_max_age: int = 150


DEAD_TUPLE_QUERY = """
SELECT
    ns.nspname,
    pg_class.relname,
    (stat.n_dead_tup - %(threshold)s) / nullif(pg_class.reltuples, 0)
FROM pg_class
JOIN pg_namespace ns ON pg_class.relnamespace = ns.oid
JOIN pg_stat_all_tables stat ON pg_class.oid = stat.relid
WHERE
    pg_class.relkind = ANY(ARRAY['r', 't'])
    AND stat.n_dead_tup > pg_class.reltuples * %(scale_factor)s + %(threshold)s
    AND (stat.last_autovacuum IS NULL OR NOW() - stat.last_autovacuum > '1 hour'::INTERVAL)
ORDER BY (stat.n_dead_tup - %(threshold)s) / nullif(pg_class.reltuples, 0) DESC NULLS LAST;
"""  # noqa: E501


INSERTED_TUPLE_QUERY = """
SELECT
    ns.nspname,
    pg_class.relname,
    (stat.n_ins_since_vacuum - %(threshold)s) / nullif(pg_class.reltuples, 0)
FROM pg_class
JOIN pg_namespace ns ON pg_class.relnamespace = ns.oid
JOIN pg_stat_all_tables stat ON pg_class.oid = stat.relid
WHERE
    pg_class.relkind = ANY(ARRAY['r', 't'])
    AND stat.n_ins_since_vacuum > pg_class.reltuples * %(scale_factor)s + %(threshold)s
    AND (stat.last_autovacuum IS NULL OR NOW() - stat.last_autovacuum > '1 hour'::INTERVAL)
ORDER BY (stat.n_dead_tup - %(threshold)s) / nullif(pg_class.reltuples, 0) DESC NULLS LAST;
"""  # noqa: E501


FREEZE_AGE_QUERY = """
SELECT
    ns.nspname,
    pg_class.relname,
    age(pg_class.relfrozenxid)
FROM pg_class
JOIN pg_namespace ns ON pg_class.relnamespace = ns.oid
WHERE
    pg_class.relkind = any(array['r', 't'])
    AND age(pg_class.relfrozenxid) > %(freeze_max_age)s * 1000000
ORDER BY age(pg_class.relfrozenxid) DESC;
"""


def run(args):
    with psycopg.connect(args.conninfo, autocommit=True) as db:
        _run(db, args)


def _run(db, args):
    start = unix_timestamp()
    version = db.execute("SHOW server_version;").fetchone()[0]
    logging.debut(f"Postgres version: {version}")
    major_version = int(version.partition(".")[0])
    logging.debug(
        "SELECT set_config('vacuum_cost_delay', %s, false);", args.cost_delay
    )
    db.execute(
        "SELECT set_config('vacuum_cost_delay', %s, false);",
        (str(args.cost_delay),),
    )
    logging.debug(
        "SELECT set_config('vacuum_cost_limit', %s, false);", args.cost_limit
    )
    db.execute(
        "SELECT set_config('vacuum_cost_limit', %s, false);",
        (str(args.cost_limit),),
    )

    by_freeze = db.execute(
        FREEZE_AGE_QUERY,
        {
            "freeze_max_age": args.freeze_max_age,
        },
    ).fetchall()
    logging.debug("To vacuum due to frozen XID:")
    for namespace, name, maxage in by_freeze:
        logging.debug(f"    {namespace}.{name}: {maxage}")
    by_dead = db.execute(
        DEAD_TUPLE_QUERY,
        {
            "threshold": args.threshold,
            "scale_factor": args.scale_factor,
        },
    ).fetchall()
    logging.debug("To vacuum due to dead tuples:")
    for namespace, name, dead in by_dead:
        logging.debug(f"    {namespace}.{name}: {dead}")

    if major_version >= 13:
        by_inserted = db.execute(
            INSERTED_TUPLE_QUERY,
            {
                "threshold": args.insert_threshold,
                "scale_factor": args.insert_scale_factor,
            },
        ).fetchall()
        logging.debug("To vacuum due to inserted tuples:")
        for namespace, name, inserted in by_inserted:
            logging.debug(f"    {namespace}.{name}: {inserted}")
    else:
        by_inserted = []

    for namespace, name, _ in by_freeze + by_dead + by_inserted:
        logging.info(f"Vacuuming {namespace}.{name}")
        db.execute(f'VACUUM "{namespace}"."{name}"')
        if unix_timestamp() > start + args.timeout:
            logging.info("Exceeded timeout, finishing...")
            break


def cli():
    parser = argparse.ArgumentParser(
        prog="nightvac",
        description=(
            "Preemptively vacuum your PostgreSQL database during off-hours"
        ),
    )
    parser.add_argument(
        "conninfo",
        help=(
            "The connection string to connect to the database.  See "
            "https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING"
            " for more details"
        ),
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=20 * 60,
        help=(
            "The runtime in seconds for the script.  Defaults to 1200 (20 "
            "minutes).  After each vacuum this is checked and the script "
            "exits if exceeded.  This will not abort the script mid-vacuum."
        ),
    )
    parser.add_argument(
        "--cost-delay",
        type=int,
        default=2,
        help=(
            'Set the `vacuum_cost_delay` config.  Defaults to "2".  See '
            "https://www.postgresql.org/docs/current/runtime-config-resource.html#GUC-VACUUM-COST-DELAY"
        ),
    )
    parser.add_argument(
        "--cost-limit",
        type=int,
        default=200,
        help=(
            'Set the `vacuum_cost_limit` config.  Defaults to "200".  See '
            "https://www.postgresql.org/docs/current/runtime-config-resource.html#GUC-VACUUM-COST-LIMIT"
        ),
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=50,
        help=(
            "The minimum number of deleted or updated tuples to trigger a "
            'vacuum.  Defaults to "50".  Equivalent to Postgres\' '
            "`autovacuum_vacuum_threshold`.  See "
            "https://www.postgresql.org/docs/12/runtime-config-autovacuum.html#GUC-AUTOVACUUM-VACUUM-THRESHOLD"
        ),
    )
    parser.add_argument(
        "--scale-factor",
        type=float,
        default=0.05,
        help=(
            "The fraction of table size to add to the threshold when deciding "
            'to vacuum.  Defaults to "0.05" (5%%).  Equivalent to  Postgres\' '
            "`autovacuum_vacuum_scale_factor`.  See "
            "https://www.postgresql.org/docs/12/runtime-config-autovacuum.html#GUC-AUTOVACUUM-VACUUM-SCALE-FACTOR"
        ),
    )
    parser.add_argument(
        "--insert-threshold",
        type=int,
        default=1000,
        help=(
            "The minimum number of inserted tuples to trigger a vacuum.  "
            'Defaults to "1000".  Equivalent to Postgres\' '
            "`autovacuum_vacuum_insert_threshold`.  See "
            "https://www.postgresql.org/docs/12/runtime-config-autovacuum.html#GUC-AUTOVACUUM-VACUUM-THRESHOLD"
        ),
    )
    parser.add_argument(
        "--insert-scale-factor",
        type=float,
        default=0.1,
        help=(
            "The fraction of table size to add to the threshold when deciding "
            'to vacuum.  Defaults to "0.1" (10%%).  Equivalent to  Postgres\' '
            "`autovacuum_vacuum_insert_scale_factor`.  See "
            "https://www.postgresql.org/docs/12/runtime-config-autovacuum.html#GUC-AUTOVACUUM-VACUUM-SCALE-FACTOR"
        ),
    )
    parser.add_argument(
        "--freeze-max-age",
        type=int,
        default=150,
        help=(
            "The maximum age (in millions) of a table's `relfrozenxid` before "
            'triggering a vacuum.  Defaults to "150" (150 million).  '
            "Equivalent to Postgres' `autovacuum_freeze_max_age`.  See "
            "https://www.postgresql.org/docs/12/runtime-config-autovacuum.html#GUC-AUTOVACUUM-FREEZE-MAX-AGE"
        ),
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()

    if args.verbose == 1:
        logging.basicConfig(level=logging.INFO)
    elif args.verbose >= 2:
        logging.basicConfig(level=logging.DEBUG)

    run(
        Args(
            conninfo=args.conninfo,
            timeout=args.timeout,
            cost_delay=args.cost_delay,
            cost_limit=args.cost_limit,
            threshold=args.threshold,
            scale_factor=args.scale_factor,
            insert_threshold=args.insert_threshold,
            insert_scale_factor=args.insert_scale_factor,
            freeze_max_age=args.freeze_max_age,
        )
    )


if __name__ == "__main__":
    cli()
