.PHONY: install lint type test cov metadata gates demo reproduce profile synthesize ablate build clean

install:
	python -m pip install -e ".[dev]"

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

gates: lint type cov metadata

demo:
	python -m ntx --frozen-time demo

reproduce:
	python -m ntx --frozen-time demo --db build/repro_a.db --out reports/repro_a
	python -m ntx --frozen-time demo --db build/repro_b.db --out reports/repro_b
	python scripts/compare_reports.py reports/repro_a reports/repro_b

profile:
	python scripts/profile_frame.py

synthesize:
	python -m ntx synthesize --out reports/synthesis.md

# Requires a hydrated local gold file; see gold/README.md.
ablate:
	python -m ntx ablate --corpus data/gold/corpus_v2.json --gold build/gold.full.jsonl

build:
	python -m build --wheel --outdir dist

clean:
	python -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('build', 'dist', 'reports', '.pytest_cache', '.mypy_cache', '.ruff_cache')]"
