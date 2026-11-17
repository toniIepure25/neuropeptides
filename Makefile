.PHONY: install lint type test cov metadata api-check links docs gates demo reproduce profile benchmark synthesize ablate build release-check clean

install:
	python -m pip install -e ".[dev,docs,release]"

lint:
	python -m ruff check .
	python -m ruff format --check .

type:
	python -m mypy src/ntx

test:
	python -m pytest

cov:
	python -m pytest --cov=ntx --cov-branch --cov-report=term-missing --cov-report=json:coverage.json
	python scripts/coverage_gate.py coverage.json --line 93 --branch 85 --combined 90

metadata:
	python scripts/check_release.py

api-check:
	python scripts/check_api_contract.py

links:
	python scripts/check_docs_links.py

docs:
	python -m mkdocs build --strict

gates: lint type cov metadata api-check links docs

demo:
	python -m ntx --frozen-time demo

reproduce:
	python -m ntx --frozen-time demo --db build/repro_a.db --out reports/repro_a
	python -m ntx --frozen-time demo --db build/repro_b.db --out reports/repro_b
	python scripts/compare_reports.py reports/repro_a reports/repro_b

profile:
	python scripts/profile_frame.py

benchmark:
	python scripts/benchmark.py --runs 5 --out build/benchmark.json

synthesize:
	python -m ntx synthesize --out reports/synthesis.md

# Requires a hydrated local gold file; see gold/README.md.
ablate:
	python -m ntx ablate --corpus data/gold/corpus_v2.json --gold build/gold.full.jsonl

build:
	python -m build --wheel --outdir dist

release-check:
	python scripts/check_release.py --tag v0.18.0
	python scripts/check_api_contract.py
	python scripts/check_docs_links.py
	python -m mkdocs build --strict

clean:
	python -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('build', 'dist', 'site', 'reports', '.pytest_cache', '.mypy_cache', '.ruff_cache')]"
