"""The stability report is only useful if it says where it was measured."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import evals.gate_stability as gs  # noqa: E402


def _row(module: str, passed: int, runs: int = 3) -> dict:
    return {
        "module": module,
        "args": "--check",
        "runs": runs,
        "passed": passed,
        "median": 1.0,
        "worst": 1.4,
        "failures": [] if passed == runs else ["boom"],
    }


def test_stable_gates_are_reported_as_stable_here():
    text = gs.render([_row("evals.benchmark", 3), _row("evals.scaling", 3)])
    assert "本机全部通过" in text
    assert "本次测量" in text, "the conclusion depends on the machine"


def test_an_unstable_gate_is_named():
    text = gs.render([_row("evals.benchmark", 3), _row("evals.worker_lab", 1)])
    assert "本机就不稳定" in text
    assert "evals.worker_lab" in text
    assert "3 次里失败 2 次" in text
