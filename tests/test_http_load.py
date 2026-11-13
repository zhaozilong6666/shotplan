"""Tests for the real-HTTP load lab.

The gate must stay machine-independent: throughput / latency rankings are
reported, never asserted. One test starts a real uvicorn subprocess to pin the
loopback/proxy regression (a proxy once rewrote every request into an
absolute-form URL and all endpoints except health returned 404).
"""

from __future__ import annotations

import socket

import httpx
from evals.http_load import (
    BRIEFS,
    MAX_P95_MS,
    Result,
    Scenario,
    build_report,
    contract_problems,
    free_port,
    one,
    server,
    snapshot,
    total_runs,
)

from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan


def _scenario(workers=1, concurrency=4, requests=10, *, errors=0,
              runs_after=None, latency_ms=10.0) -> Scenario:
    ok = requests - errors
    results = [Result("ok", latency_ms) for _ in range(ok)]
    results += [Result("http", latency_ms, "HTTP 500") for _ in range(errors)]
    sc = Scenario(workers, concurrency, requests, results=results, wall_s=1.0)
    sc.runs_before = 0
    sc.runs_after = ok if runs_after is None else runs_after
    return sc


def _full_matrix() -> list[Scenario]:
    return [
        _scenario(1, 1, 200),
        _scenario(1, 4, 200),
        _scenario(1, 8, 200),
        _scenario(2, 8, 200),
        _scenario(4, 8, 200),
    ]


def test_free_port_is_bindable():
    port = free_port()
    assert 1024 < port < 65536
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_clean_scenario_passes_the_gate():
    assert contract_problems(_scenario(), gate=True) == []


def test_gate_flags_failed_requests():
    problems = contract_problems(_scenario(errors=2), gate=True)
    assert any("request failed" in p for p in problems)


def test_gate_flags_lost_writes():
    problems = contract_problems(_scenario(runs_after=6), gate=True)
    assert any("not persisted" in p for p in problems)


def test_gate_flags_missing_responses():
    sc = _scenario()
    sc.results = sc.results[:5]
    assert any("responses collected" in p for p in contract_problems(sc, gate=True))


def test_latency_ceiling_is_only_a_gate_for_hung_services():
    slow = _scenario(latency_ms=MAX_P95_MS + 1000)
    assert contract_problems(slow, gate=False) == []
    assert any("exceeds the loose ceiling" in p
               for p in contract_problems(slow, gate=True))


def test_snapshot_is_explicitly_machine_dependent():
    payload = snapshot(_full_matrix(), 8)
    assert payload["machine_dependent"] is True
    assert len(payload["scenarios"]) == 5
    assert all("rps" in row and "lost_writes" in row
               for row in payload["scenarios"])


def test_report_states_that_throughput_is_not_gated():
    text = build_report(_full_matrix(), 8)
    assert "生成时间" not in text
    assert "不做断言" in text
    assert "worker 扩展曲线" in text
    assert "落库" in text


def test_every_load_brief_is_executable():
    for brief in BRIEFS:
        result = run_plan(PlanRequest.from_dict(dict(brief)))
        assert result.status == "ok", brief


def test_real_service_serves_plan_and_persists_it(tmp_path):
    """Regression: HTTP_PROXY rewrote loopback requests into absolute-form URLs."""
    with server(1, tmp_path / "runs.db") as (base_url, startup_s):
        assert startup_s > 0
        with httpx.Client(
            base_url=base_url, timeout=10.0, trust_env=False
        ) as client:
            before = total_runs(client)
            result = one(client, 0)
            assert result.kind == "ok", result.detail
            assert total_runs(client) == before + 1
