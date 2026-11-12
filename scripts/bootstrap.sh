#!/usr/bin/env bash
# One-shot local setup for macOS / Linux / Git Bash on Windows.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[1/5] creating virtual environment"
python -m venv .venv
if [ -f .venv/Scripts/python.exe ]; then PY=".venv/Scripts/python.exe"; else PY=".venv/bin/python"; fi

echo "[2/5] installing optional dependencies"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -e ".[api,dev]"

echo "[3/5] preparing .env"
if [ ! -f .env ]; then cp .env.example .env; echo "created .env (mock mode, no key needed)"; fi

echo "[4/5] running tests and evaluation"
"$PY" -m pytest
"$PY" -m evals.run_eval --write-report

echo "[5/5] end-to-end smoke"
"$PY" -m shotplan.cli plan

echo "done. start the UI with: $PY -m shotplan.cli serve"
