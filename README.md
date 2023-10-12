# nightvac

Vacuuming is an important part of maintaining your PostgreSQL database.
Generally Postgres' autovacuum feature takes care of this for you.
However, autovacuum is triggered after a table has a certain number of rows updated,
making it more likely to run during your busiest times, when the frequency of writes is highest.
Autovacuum increases CPU and disk usage—During your busiest times, when resource usage is already at peak.
This can last for many minutes or even hours, depending on the size of the table being vacuumed.

To be clear, Postgres can and should be configured to limit the impact of autovacuum.
It comes preconfigured with sane defaults that should work for most databases.
But why not vacuum when usage is lowest and impact will be the least?
Enter nightvac.

Nightvac will find the tables in the database that are nearing an autovacuum and preemptively run a manual vacuum.
It can be run as a cron job during off-hours and has a configurable timeout so it won't run long.

## Getting Started

Nightvac is written in Python.
The recommended installation method is using [pipx](https://pypa.github.io/pipx/installation/).

```
pipx install https://github.com/luhn/nightvac/archive/refs/tags/v0.1.1.tar.gz
```

Alternatively, you can install pip.  It is recommended to install into a virtualenv.

```
python -m venv .nightvac
.nightvac/bin/pip install https://github.com/luhn/nightvac/archive/refs/tags/v0.1.1.tar.gz
```

You can also download `nightvac.py` and run that directly—The only dependency is psycopg.

To run nightvac, you need only to pass in a [connection string](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING).

```
nightvac postgresql://user:pass@host/mydb
```

## What Gets Vacuumed

By default, Postgres' autovacuum will trigger on any table that has either:

- Max freeze age (`age(pg_class.relfrozenxid)`) greater than 200 million.
- Dead tuples (`pg_stat_all_tables.n_dead_tups`) greater than 20% of total tuples (`pgclass.reltuples`) plus 50.

By default, nightvac will preemptively vacuum any table that has either:

- Max freeze age (`age(pg_class.relfrozenxid)`) greater than 150 million.
- Dead tuples (`pg_stat_all_tables.n_dead_tups`) greater than 5% of total tuples (`pgclass.reltuples`) plus 50.

Tables exceeding max freeze age threshold will be vacuumed first, in descending order of max freeze age.
Next, tables exceeding dead tuple threshold, in descending order of dead tuple/total tuple ratio.

nightvac will exit once all qualifying tables have been vacuum or the 20 minute timeout is hit.
The timeout will not interrupt a running vacuum; nightvac will finish the vacuum before exiting.

## Configuration

nightvac can be configured with the following CLI arguments:

- `-t [int]`, `--timeout [int]`:
  The runtime in seconds for the script.
  Defaults to 1200 (20 minutes). After each vacuum this is checked and the script exits if exceeded.
  This will not abort the script mid-vacuum.
- `--cost-delay [int]`:
  Set the `vacuum_cost_delay` config. Defaults to "2". See https://www.postgresql.org/docs/current/runtime-config-resource.html#GUC-VACUUM-COST-DELAY
- `--cost-limit [int]`:
  Set the `vacuum_cost_limit` config. Defaults to "200". See https://www.postgresql.org/docs/current/runtime-config-resource.html#GUC-VACUUM-COST-LIMIT
- `--threshold [int]`:
  The minimum number of deleted or updated tuples to trigger a vacuum.
  Defaults to "50".
  Equivalent to Postgres' `autovacuum_vacuum_threshold`.
  See https://www.postgresql.org/docs/12/runtime-config-autovacuum.html#GUC-AUTOVACUUM-VACUUM-THRESHOLD
- `--scale-factor [float]`:
  The fraction of table size to add to the threshold when deciding to vacuum.
  Defaults to "0.05" (5%).
  Equivalent to Postgres' `autovacuum_vacuum_scale_factor`.
  See https://www.postgresql.org/docs/12/runtime-config-autovacuum.html#GUC-AUTOVACUUM-VACUUM-SCALE-FACTOR
-	`--freeze-max-age [int]`:
  The maximum age (in millions) of a table's `relfrozenxid` before triggering a vacuum.
  Defaults to "150" (150 million).
  Equivalent to Postgres' `autovacuum_freeze_max_age`.
  See https://www.postgresql.org/docs/12/runtime-config-autovacuum.html#GUC-AUTOVACUUM-FREEZE-MAX-AGE

## Prior Art

nightvac is heavily inspired by [flexible-freeze](https://github.com/pgexperts/flexible-freeze).
flexible-freeze differs from nightvac in a few ways:

- flexible-freeze always runs `VACUUM FREEZE`, which is unnecessarily aggressive.
- flexible-freeze vacuums based on max freeze age by default, or dead tuples.  nightvac vacuums based on both.
