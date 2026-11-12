"""The failure library must be generated from real validator verdicts."""

from __future__ import annotations

from evals.collect_failures import build_gallery, collect_failures


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
