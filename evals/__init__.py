"""Evaluation harness. Each case is a brief plus an expectation, not a fixture."""

import sys
from pathlib import Path

CASES_DIR = Path(__file__).resolve().parent / "cases"
OUT_DIR = Path(__file__).resolve().parent / "out"

# Allow `python -m evals.run_eval` from the repo root without installing the package.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
