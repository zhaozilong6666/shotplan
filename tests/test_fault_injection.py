"""The fault-injection matrix is itself a gate, and must stay honest."""

from __future__ import annotations

from pathlib import Path

from evals.fault_injection import render, run_matrix


def test_every_scenario_passes():
    rows = run_matrix()
    assert len(rows) >= 12
    failures = [(r.name, r.detail) for r in rows if not r.ok]
    assert not failures, failures


def test_report_states_the_contracts_not_just_a_green_tick():
    text = render(run_matrix())
    assert "PERSISTENCE_FAILED" in text
    assert "hard_pass_rate" in text
    assert "502" in text and "503" in text
    assert "真实" in text and "离线" in text


def test_gate_count_matches_the_makefile():
    import re

    from scripts.check_gate_count import gate_commands

    commands = gate_commands()
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    declared = [int(v) for v in re.findall(r"(\d+)\s*道\s*CI\s*门禁", readme)]
    assert declared and all(v == len(commands) for v in declared), (len(commands), declared)
