"""The multi-turn lab is itself a measurement, so it needs its own checks.

A scoring script that silently stops scoring is worse than no script: the
number stays on the page while the thing it measured has changed. These tests
guard the harness, not the rules -- whether the rules are good enough is what
`evals.multi_turn_lab` reports, and whether the report is up to date is what the
`--check` gate enforces.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

import multi_turn_lab as mtl  # noqa: E402


def test_cases_are_loadable_and_identifiable():
    cases = mtl.load_cases(mtl.DEFAULT_CASES)
    assert len(cases) >= 12
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids)), "duplicate case id"


def test_every_case_asserts_something():
    """A case with an empty expectation passes for free and is not a test."""
    for case in mtl.load_cases(mtl.DEFAULT_CASES):
        assert case["expect"], f"{case['id']} expects nothing"
        assert len(case["turns"]) >= 2, f"{case['id']} has no follow-up turn"


def test_refusal_stays_available_as_an_answer():
    """M09 exists so the rules cannot buy points by guessing at vague input."""
    case = next(c for c in mtl.load_cases(mtl.DEFAULT_CASES) if c["id"] == "M09")
    result = mtl.run_case(case)
    assert result["pass"], result["mismatches"]
    assert result["said"][-1].endswith("（未识别）")


def test_the_report_does_not_leak_a_regenerated_id():
    """request_id changes on every normalization, which would fail --check forever."""
    case = mtl.load_cases(mtl.DEFAULT_CASES)[0]
    assert "request_id" not in mtl.run_case(case)["final"]


def test_rendering_twice_gives_the_same_text():
    """The gate compares the report against a fresh render, so it must be stable."""
    results = [mtl.run_case(case) for case in mtl.load_cases(mtl.DEFAULT_CASES)]
    first = mtl.render(results, silent=True)
    second = mtl.render(results, silent=True)
    assert first == second
