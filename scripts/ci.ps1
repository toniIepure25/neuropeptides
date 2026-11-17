"""Run the same gates as CI, locally (Windows-friendly)."""

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist"
$tmp = Join-Path $env:TEMP ("ntx-ci-" + [guid]::NewGuid().ToString("N"))
$oldFrozen = $env:NTX_FROZEN_TIME
$oldRoot = $env:NTX_ROOT
$oldPythonPath = $env:PYTHONPATH

Push-Location $root
try {
    python -m ruff check .
    python -m ruff format --check .
    python -m mypy src/ntx
    python scripts/check_release.py
    python -m pytest --cov=ntx --cov-branch --cov-report=term-missing --cov-report=json:coverage.json
    python scripts/coverage_gate.py coverage.json --line 93 --branch 85 --combined 90

    if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
    $env:SOURCE_DATE_EPOCH = "1794680589"
    python -m build --sdist --wheel --outdir $dist
    $wheel = Get-ChildItem (Join-Path $dist "*.whl") | Select-Object -First 1
    $sdist = Get-ChildItem (Join-Path $dist "*.tar.gz") | Select-Object -First 1
    if (-not $wheel -or -not $sdist) { throw "build did not produce wheel and sdist" }

    New-Item -ItemType Directory -Path $tmp | Out-Null
    # Some Windows Python builds emit a harmless stderr warning about a redirected
    # environment location. Windows PowerShell turns native stderr into an error
    # record, so redirect it inside cmd and check the exit code instead.
    $venvPath = Join-Path $tmp "venv"
    cmd /c "python -m venv `"$venvPath`" 2>nul"
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    $venvPython = Join-Path $venvPath "Scripts/python.exe"
    $venvNtx = Join-Path $venvPath "Scripts/ntx.exe"
    & $venvPython -m pip install --quiet --upgrade pip
    & $venvPython -m pip install --force-reinstall --no-deps $wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw "wheel install failed" }
    if (-not (Test-Path $venvNtx)) {
        Get-ChildItem (Join-Path $tmp "venv/Scripts") | Select-Object -ExpandProperty Name
        throw "installed wheel did not create the ntx entry point"
    }
    & $venvNtx --version
    if ($LASTEXITCODE -ne 0) { throw "installed ntx entry point failed" }

    $env:NTX_FROZEN_TIME = "2026-11-13T20:30:00+00:00"
    $env:NTX_ROOT = ""
    $env:PYTHONPATH = ""
    Push-Location $tmp
    try {
        & $venvNtx demo --db (Join-Path $tmp "a.db") --out (Join-Path $tmp "reports")
        if ($LASTEXITCODE -ne 0) { throw "demo failed in clean environment" }
        & $venvNtx demo --db (Join-Path $tmp "b.db") --out (Join-Path $tmp "reports2")
        if ($LASTEXITCODE -ne 0) { throw "second demo failed in clean environment" }
        & $venvNtx check --db (Join-Path $tmp "a.db")
        if ($LASTEXITCODE -ne 0) { throw "check failed on demo database" }
        & $venvNtx quality --db (Join-Path $tmp "a.db")
        if ($LASTEXITCODE -ne 0) { throw "quality gate failed on demo database" }
        & $venvNtx doctor --db (Join-Path $tmp "a.db")
        if ($LASTEXITCODE -ne 0) { throw "doctor failed on demo database" }
        & $venvNtx db backup --db (Join-Path $tmp "a.db") --out (Join-Path $tmp "snapshot.db")
        if ($LASTEXITCODE -ne 0) { throw "database backup failed" }
        & $venvNtx db verify --db (Join-Path $tmp "snapshot.db")
        if ($LASTEXITCODE -ne 0) { throw "database backup verification failed" }
        & $venvNtx db restore --backup (Join-Path $tmp "snapshot.db") --db (Join-Path $tmp "restored.db")
        if ($LASTEXITCODE -ne 0) { throw "database restore failed" }
        & $venvNtx verify --db (Join-Path $tmp "a.db") --offline --limit 1
        if ($LASTEXITCODE -ne 0) { throw "source verification failed in clean environment" }
        & $venvNtx fulltext fetch --db (Join-Path $tmp "a.db") --offline --limit 1
        if ($LASTEXITCODE -ne 0) { throw "full-text fetch failed in clean environment" }
        & $venvNtx fulltext extract --db (Join-Path $tmp "a.db")
        if ($LASTEXITCODE -ne 0) { throw "full-text extraction failed in clean environment" }
        & $venvNtx fulltext report --db (Join-Path $tmp "a.db") --out (Join-Path $tmp "fulltext.json") --fail-on high
        if ($LASTEXITCODE -ne 0) { throw "full-text report failed in clean environment" }
        & $venvNtx fulltext report --db (Join-Path $tmp "a.db") --entity "NP:OXT" --fail-on high
        if ($LASTEXITCODE -ne 0) { throw "entity-filtered full-text report failed in clean environment" }
        & $venvNtx report --db (Join-Path $tmp "a.db") --out (Join-Path $tmp "reports-fulltext") --with-fulltext
        if ($LASTEXITCODE -ne 0) { throw "full-text-aware brief failed in clean environment" }
        $checkpoints = Join-Path $tmp "checkpoints"
        & $venvNtx ledger checkpoint --db (Join-Path $tmp "a.db") --out (Join-Path $checkpoints "0001.json")
        if ($LASTEXITCODE -ne 0) { throw "checkpoint write failed in clean environment" }
        & $venvNtx ledger verify-checkpoints --db (Join-Path $tmp "a.db") --dir $checkpoints
        if ($LASTEXITCODE -ne 0) { throw "checkpoint verification failed in clean environment" }
        python (Join-Path $root "scripts/compare_reports.py") (Join-Path $tmp "reports") (Join-Path $tmp "reports2")
        if ($LASTEXITCODE -ne 0) { throw "frozen demo reports differ" }
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
    $env:NTX_FROZEN_TIME = $oldFrozen
    $env:NTX_ROOT = $oldRoot
    $env:PYTHONPATH = $oldPythonPath
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
}

Write-Host "All local quality gates passed."
exit 0
