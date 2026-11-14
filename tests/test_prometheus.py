"""Prometheus output must be scrapeable, honest about being a window, and safe."""

from __future__ import annotations

import pytest

from shotplan.observability import render_prometheus

DASHBOARD = {
    "total_runs": 3,
    "window": 200,
    "by_status": {"ok": 2, "error": 1},
    "overall": {
        "runs": 3,
        "hard_pass_rate": 0.6667,
        "avg_score": 0.81,
        "p50_duration_ms": 12.5,
        "p95_duration_ms": 98.0,
        "total_cost_cny": 0.42,
        "total_repairs": 2,
    },
    "groups": [
        {
            "provider": "mock",
            "prompt_version": "planner-v2",
            "runs": 3,
            "hard_pass_rate": 0.6667,
            "p95_duration_ms": 98.0,
        }
    ],
}


def test_core_metrics_and_window_labels():
    text = render_prometheus(DASHBOARD)
    assert "# TYPE shotplan_runs gauge" in text
    assert 'shotplan_runs{window="200"} 3' in text
    assert 'shotplan_hard_pass_rate{window="200"} 0.6667' in text
    assert 'shotplan_duration_ms{window="200",quantile="0.95"} 98.0' in text
    assert 'shotplan_runs_by_status{status="error"} 1' in text
    assert 'shotplan_group_runs{provider="mock",prompt_version="planner-v2"} 3' in text


def test_gauge_not_counter_because_it_is_a_sliding_window():
    text = render_prometheus(DASHBOARD)
    assert "TYPE shotplan_runs counter" not in text
    assert "TYPE shotplan_repairs_total counter" not in text


def test_label_values_are_escaped():
    dirty = {**DASHBOARD, "by_status": {'we"ird\nstatus': 1}}
    text = render_prometheus(dirty)
    assert 'status="we\\"ird\\nstatus"' in text


def test_empty_dashboard_still_scrapes():
    text = render_prometheus({"window": 200, "overall": {}, "by_status": {}, "groups": []})
    assert 'shotplan_runs{window="200"} 0' in text
    assert 'shotplan_hard_pass_rate{window="200"} 0.0' in text
    assert text.endswith("\n")


def test_endpoint_returns_plain_text(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from shotplan.api import app

    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "metrics.db"))
    monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE shotplan_runs gauge" in response.text
