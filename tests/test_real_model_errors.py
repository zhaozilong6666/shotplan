"""An outage must be visible in the report, not hidden inside 'errors: 68'."""

from __future__ import annotations

from types import SimpleNamespace

from evals.real_model_lab import build_report, summarise_repeats
from evals.scorers import CaseOutcome


def _settings():
    return SimpleNamespace(
        llm_model="fake-model",
        llm_base_url="https://example.invalid/v1",
        prompt_version="",
    )


def _bad(case_id: str, index: int, message: str):
    outcome = CaseOutcome(
        case_id=case_id,
        group="normal",
        description="",
        provider="fake",
        hard_pass=False,
        score=0.0,
        status="error",
        error=message,
    )
    return (case_id, index, outcome)


def test_error_messages_are_counted_per_message():
    runs = [
        _bad("C01", 0, "ProviderError: 403 INSUFFICIENT_BALANCE"),
        _bad("C02", 0, "ProviderError: 403 INSUFFICIENT_BALANCE"),
        _bad("C03", 0, "ParseError: slot 1 missing fields"),
    ]
    summary = summarise_repeats(runs, _settings(), repeat=1)
    assert summary["errors"] == 3
    assert summary["error_messages"]["ProviderError: 403 INSUFFICIENT_BALANCE"] == 2


def test_report_prints_error_distribution_and_how_to_read_it():
    runs = [
        _bad("C01", 0, "ProviderError: 403 INSUFFICIENT_BALANCE"),
        _bad("C02", 0, "ProviderError: 403 INSUFFICIENT_BALANCE"),
    ]
    summary = summarise_repeats(runs, _settings(), repeat=1)
    summary["concurrency"] = 2
    report = build_report(summary, baseline=None)
    assert "## 异常分布" in report
    assert "INSUFFICIENT_BALANCE" in report
    assert "上游故障与模型失败" in report
