"""The repeat-mode aggregation and the report it produces.

These are deliberately unit tests, not another live run: the arithmetic of
"what would a single run have said" is where a quiet bug would silently
manufacture confidence, and it must be checkable without spending API credit.
"""

from __future__ import annotations

from types import SimpleNamespace

from evals.real_model_lab import bootstrap_ci, build_report, summarise_repeats
from evals.scorers import CaseOutcome


def _outcome(case_id: str, index: int, hard_pass: bool, score: float, repairs: int = 0):
    return (
        case_id,
        index,
        CaseOutcome(
            case_id=case_id,
            group="normal",
            description="",
            provider="fake",
            hard_pass=hard_pass,
            score=score,
            repair_count=repairs,
            duration_ms=100.0,
            status="ok",
        ),
    )


def _settings():
    return SimpleNamespace(
        llm_model="fake-model",
        llm_base_url="https://example.invalid/v1",
        prompt_version="",
    )


def test_per_case_pass_rate_and_run_spread_are_computed_separately():
    runs = []
    # C01 always passes; C02 passes only on repeat 1 (0 of 2 elsewhere).
    for index in range(2):
        runs.append(_outcome("C01", index, True, 0.9))
        runs.append(_outcome("C02", index, index == 1, 0.6))
    summary = summarise_repeats(runs, _settings(), repeat=2)

    by_id = {row["id"]: row for row in summary["per_case"]}
    assert by_id["C01"]["pass_rate"] == 1.0
    assert by_id["C02"]["pass_rate"] == 0.5

    # Case-averaged rate: (1.0 + 0.5) / 2, not the per-call rate (3/4).
    assert summary["hard_pass_rate"] == 0.75
    # A single run would have reported 1/2 on repeat 1 and 2/2 on repeat 2.
    assert summary["run_pass_rate"] == [0.5, 1.0]
    assert summary["run_pass_spread_pp"] == 50.0


def test_repeat_report_states_the_run_to_run_spread():
    runs = [
        _outcome("C01", 0, True, 0.9),
        _outcome("C02", 0, True, 0.8),
        _outcome("C01", 1, True, 0.9),
        _outcome("C02", 1, False, 0.5),
    ]
    summary = summarise_repeats(runs, _settings(), repeat=2)
    summary["concurrency"] = 2
    report = build_report(summary, baseline=None)

    assert "重复测量（2 轮）" in report
    assert "单轮之间的通过率极差" in report
    assert "50.0 pp" in report
    # The distinction between the two intervals must survive editing.
    assert "换一批用例会不会翻" in report
    assert "换一轮会不会翻" in report


def test_bootstrap_is_seeded_and_brackets_the_mean():
    values = [0.2, 0.5, 0.8, 0.9, 1.0]
    assert bootstrap_ci(values) == bootstrap_ci(values)
    low, high = bootstrap_ci(values)
    mean = sum(values) / len(values)
    assert low <= mean <= high
