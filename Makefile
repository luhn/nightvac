
lint-dep:
	pip install -q "ruff~=0.5.6"

lint: lint-dep
	ruff check nightvac.py
	ruff format --check nightvac.py

format: lint-dep
	ruff format nightvac.py
	ruff check --fix nightvac.py
