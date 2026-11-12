"""The gate-sensitivity lab must use the REAL gate and catch every mutation."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from evals import mutation_lab, run_eval
from evals.scorers import (
    CONTROL_RULE_MIN_COUNTS,
    MAX_CONTROL_AVG_SCORE,
    control_group_check,
    eval_gate_problems,
)


@pytest.fixture(scope="module")
def lab_result():
    return mutation_lab.run_lab(mutation_lab.load_cases(None))


def _naive(avg: float, hard_pass: float, counts: dict[str, int] | None = None) -> dict:
    return {
        "avg_score": avg,
        "hard_pass_rate": hard_pass,
        "issue_frequency": dict(counts or {}),
    }


def _good(avg: float = 0.9788, hard_pass: float = 1.0, failed: int = 0) -> dict:
    return {
        "avg_score": avg,
        "hard_pass_rate": hard_pass,
        "cases_failed": failed,
    }


def test_run_eval_uses_the_same_gate_function():
    assert run_eval.eval_gate_problems is eval_gate_problems


def test_control_group_check_accepts_a_healthy_baseline():
    counts = {code: floor * 2 for code, floor in CONTROL_RULE_MIN_COUNTS.items()}
    assert control_group_check(_naive(0.30, 0.14, counts)) == []


@pytest.mark.parametrize(
    "naive, expected_fragment",
    [
        (_naive(MAX_CONTROL_AVG_SCORE + 0.01, 0.10, {}), "exceeds"),
        (_naive(0.30, 0.31, {}), "invalid plans are being accepted"),
        (_naive(0.30, 0.10, {}), "a validator rule appears to be disabled"),
    ],
)
def test_control_group_check_rejects_weakened_judges(naive, expected_fragment):
    problems = control_group_check(naive)
    assert any(expected_fragment in problem for problem in problems)


def test_eval_gate_problems_rejects_control_group_scoring_at_or_above_planner():
    counts = {code: floor * 2 for code, floor in CONTROL_RULE_MIN_COUNTS.items()}
    problems = eval_gate_problems(_good(), _naive(1.0, 1.0, counts), 0.80)
    assert any("measurement is broken" in problem for problem in problems)
    assert any("control delta" in problem for problem in problems)


def test_eval_gate_problems_accepts_current_baseline():
    counts = {code: floor * 2 for code, floor in CONTROL_RULE_MIN_COUNTS.items()}
    assert eval_gate_problems(_good(), _naive(0.3014, 0.1375, counts), 0.80) == []


def test_mutants_are_unique_and_cover_both_sides():
    keys = [mutant.key for mutant in mutation_lab.MUTANTS]
    assert len(keys) == len(set(keys))
    assert {mutant.side for mutant in mutation_lab.MUTANTS} == {"planner", "validator"}


def test_every_mutation_is_caught_and_rolled_back(lab_result):
    assert lab_result["baseline_gate_problems"] == []
    assert lab_result["case_count"] == 80
    assert lab_result["mutant_count"] == len(mutation_lab.MUTANTS)
    assert lab_result["caught"] == lab_result["mutant_count"]
    assert lab_result["survivors"] == 0
    assert lab_result["restored"] is True
    assert lab_result["restored_avg"] == lab_result["baseline"]["good_avg"]
    assert len(lab_result["mutants"]) == lab_result["mutant_count"]


def test_legacy_gate_had_a_real_blind_spot(lab_result):
    """This is the finding that motivated ADR 0009, not a decorative number."""
    assert lab_result["caught_by_legacy_gate"] < lab_result["caught"]
    validator_rows = [row for row in lab_result["mutants"] if row["side"] == "validator"]
    assert any(not row["caught_by_legacy_gate"] for row in validator_rows)
    assert all(row["caught"] for row in validator_rows)


def test_report_and_snapshot_are_deterministic(lab_result):
    document = mutation_lab.build_document(lab_result)
    assert document == mutation_lab.build_document(lab_result)
    snapshot = json.loads(mutation_lab.build_snapshot(lab_result))
    assert "generated_at" not in snapshot
    assert snapshot["caught"] == lab_result["mutant_count"]
    assert "python -m evals.mutation_lab --check" in document


def test_lab_problems_flags_a_surviving_mutant(lab_result):
    broken = dict(lab_result)
    broken["mutants"] = [dict(row) for row in lab_result["mutants"]]
    broken["mutants"][0]["caught"] = False
    problems = mutation_lab.lab_problems(broken)
    assert any("survived the gate" in problem for problem in problems)


def test_check_mode_requires_both_artifacts(tmp_path, lab_result):
    """A missing or stale artifact must fail the gate."""
    document = mutation_lab.build_document(lab_result)
    snapshot = mutation_lab.build_snapshot(lab_result)
    with patch.object(mutation_lab, "run_lab", return_value=lab_result):
        assert mutation_lab.main(["--check", "--report-path", str(tmp_path / "r.md"),
                                  "--out", str(tmp_path / "d.json")]) == 1
        (tmp_path / "r.md").write_text(document, encoding="utf-8")
        (tmp_path / "d.json").write_text(snapshot, encoding="utf-8")
        assert mutation_lab.main(["--check", "--report-path", str(tmp_path / "r.md"),
                                  "--out", str(tmp_path / "d.json")]) == 0
        (tmp_path / "d.json").write_text(snapshot + "\n", encoding="utf-8")
        assert mutation_lab.main(["--check", "--report-path", str(tmp_path / "r.md"),
                                  "--out", str(tmp_path / "d.json")]) == 1
