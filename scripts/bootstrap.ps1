# One-shot local setup for Windows PowerShell.
# Usage: powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "[1/5] creating virtual environment"
python -m venv .venv
$py = Join-Path (Get-Location) ".venv\Scripts\python.exe"

Write-Host "[2/5] installing optional dependencies"
& $py -m pip install -q --upgrade pip
& $py -m pip install -q -e ".[api,dev]"

Write-Host "[3/5] preparing .env"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "created .env (mock mode)" }

Write-Host "[4/5] running tests and evaluation"
& $py -m pytest
if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
& $py -m evals.run_eval --write-report
if ($LASTEXITCODE -ne 0) { throw "evaluation failed" }

Write-Host "[5/5] end-to-end smoke"
& $py -m shotplan.cli plan

Write-Host "done. start the UI with: .\.venv\Scripts\python.exe -m shotplan.cli serve"
