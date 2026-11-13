"""The controlled experiment must be allowed to disagree with the previous report.

These pin the verdict logic with synthetic cells: a conclusion is only valid
when the phenomenon itself reproduces across runs, and the prose describing
what this means for v0.8.0 must follow the verdict instead of being hard-coded.
"""

from __future__ import annotations

import pytest
from evals.http_load import Result, Scenario
from evals.worker_lab import (
    Cell,
    check_problems,
    interpret,
    relation_text,
    summarize,
    verdict_text,
)

from shotplan.config import Settings, _env_bool
from shotplan.domain.errors import ConfigError

_BASE = {
    (True, 1): [100.0, 100.0],
    (True, 2): [100.0, 100.0],
    (True, 4): [80.0, 80.0],
    (False, 1): [300.0, 300.0],
    (False, 2): [300.0, 300.0],
    (False, 4): [400.0, 400.0],
}


def _cells(overrides: dict | None = None) -> dict[tuple[bool, int], Cell]:
    values = {**_BASE, **(overrides or {})}
    return {
        key: Cell(key[0], key[1], rps_values=list(vals), p95_values=[10.0, 10.0])
        for key, vals in values.items()
    }


def test_confirmed_when_removing_the_write_fixes_the_fall():
    result = interpret(_cells())
    assert result["verdict"] == "confirmed"
    assert result["persistence_is_major_cost"] is True
    assert result["write_lock_caused_fall"] is True
    assert result["on_4w_over_1w_per_run"] == [0.8, 0.8]
    assert "降级" not in relation_text(result)


def test_partially_confirmed_when_fall_survives_without_writes():
    result = interpret(_cells({(False, 4): [200.0, 200.0]}))
    assert result["verdict"] == "partially_confirmed"
    assert result["off_4w_over_1w"] < 1.0
    assert "不成立" in relation_text(result)


def test_inconclusive_when_runs_disagree_about_the_fall():
    result = interpret(_cells({(True, 4): [120.0, 80.0]}))
    assert result["verdict"] == "inconclusive"
    assert "降级为待验证假设" in relation_text(result)
    assert "连「是否回落」都不稳定" in verdict_text(result)


def test_not_reproduced_when_there_is_no_fall_at_all():
    result = interpret(_cells({(True, 4): [120.0, 120.0]}))
    assert result["verdict"] == "not_reproduced"
    assert "未复现" in verdict_text(result)


def test_refuted_when_persistence_is_not_a_major_cost():
    result = interpret(_cells({(False, 1): [110.0, 110.0]}))
    assert result["verdict"] == "refuted"
    assert result["persistence_is_major_cost"] is False


def test_summarize_uses_median_and_keeps_every_run():
    scenarios = []
    for rps in (100.0, 300.0):
        sc = Scenario(1, 8, 10, results=[Result("ok", 5.0)] * 10,
                      wall_s=10 / rps, persist=True)
        sc.runs_after = 10
        scenarios.append(sc)
    cells = summarize(scenarios)
    assert cells[(True, 1)].median_rps == 200.0
    assert cells[(True, 1)].rps_values == [100.0, 300.0]


def _arm(persist: bool, runs_after: int) -> Scenario:
    sc = Scenario(1, 4, 4, results=[Result("ok", 1.0)] * 4,
                  wall_s=1.0, persist=persist)
    sc.runs_before = 0
    sc.runs_after = runs_after
    return sc


def test_switch_semantics_gate():
    assert check_problems(_arm(True, 4), _arm(False, 0)) == []


def test_gate_flags_lost_writes_and_a_no_op_switch():
    assert any("writes were lost" in p
               for p in check_problems(_arm(True, 2), _arm(False, 0)))
    assert any("switch had no effect" in p
               for p in check_problems(_arm(True, 4), _arm(False, 2)))
    assert any("opposite persist" in p
               for p in check_problems(_arm(False, 0), _arm(False, 0)))


def test_persist_flag_is_parsed_from_environment(monkeypatch):
    monkeypatch.setenv("SHOTPLAN_PERSIST_RUNS", "0")
    assert _env_bool("SHOTPLAN_PERSIST_RUNS", True) is False
    assert Settings.from_env().persist_runs is False
    assert Settings.from_env().masked()["persist_runs"] is False
    monkeypatch.setenv("SHOTPLAN_PERSIST_RUNS", "yes")
    assert _env_bool("SHOTPLAN_PERSIST_RUNS", False) is True
    monkeypatch.setenv("SHOTPLAN_PERSIST_RUNS", "maybe")
    with pytest.raises(ConfigError):
        _env_bool("SHOTPLAN_PERSIST_RUNS", True)
