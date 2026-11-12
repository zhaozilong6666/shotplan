"""The failure library must be generated from real validator verdicts."""

from __future__ import annotations

from evals.collect_failures import build_gallery, check_records, collect_failures


def test_failure_records_are_complete():
    records = collect_failures()
    assert records
    required = {
        "case_id", "group", "brief", "code", "severity", "message",
        "expected", "suggestion", "observed_slots", "score",
    }
    for record in records[:10]:
        assert required <= set(record)
        assert record["severity"] == "hard"
        assert record["expected"]
        assert record["suggestion"]
        assert isinstance(record["observed_slots"], list)
    codes = {r["code"] for r in records}
    assert {"BUDGET_EXCEEDED", "UNKNOWN_LOCATION", "TIME_OVERRUN"} <= codes


def test_gallery_documents_every_code():
    records = collect_failures()
    text = build_gallery(records)
    assert "失败案例库" in text
    assert "按问题码汇总" in text
    for code in {r["code"] for r in records}:
        assert f"`{code}`" in text
    assert "真实模型" in text


def test_check_records_passes_on_current_library():
    assert check_records(collect_failures()) == []


def test_check_records_flags_thin_or_incomplete_library():
    assert check_records([], min_records=1)
    thin = [{"case_id": "X", "code": "A", "expected": "", "suggestion": ""}]
    problems = check_records(thin, min_records=1, min_codes=1)
    assert any("missing expected/suggestion" in problem for problem in problems)
