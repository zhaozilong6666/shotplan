"""Feasibility probes exist because a gate can fail without producing a plan.

Lower the minimum budget or the minimum hours, and an impossible brief becomes
"feasible" — no plan, no hard-rule violation, nothing for the eval corpus to catch
(E22). These tests pin the baseline decisions and the one-sided regression.
"""

from __future__ import annotations

from evals import probe_lab


def test_each_feasibility_group_has_cases_on_both_sides_of_the_gate():
    for group in probe_lab.FEASIBILITY_GROUPS:
        cases = probe_lab.read_group(group.key)
        assert any(not case["expect_feasible"] for case in cases), group.key
        assert any(case["expect_feasible"] for case in cases), group.key


def test_baseline_matches_the_declared_decisions():
    for group in probe_lab.FEASIBILITY_GROUPS:
        rows = probe_lab.run_feasibility_group(group)["baseline"]
        assert rows
        assert all(row["pass"] for row in rows), group.key


def test_zeroing_the_gate_flips_only_the_rejected_side():
    for group in probe_lab.FEASIBILITY_GROUPS:
        results = probe_lab.run_feasibility_group(group)
        wrong = [row for row in results["one-sided"] if not row["pass"]]
        assert wrong, f"{group.key}: 常量改成 0 之后没有任何 brief 被误判"
        assert all(not row["expect_feasible"] for row in wrong), group.key
        assert all(
            row["pass"] for row in results["one-sided"] if row["expect_feasible"]
        ), group.key


def test_the_whole_probe_set_passes_its_own_gate():
    all_results = {
        group.key: (
            probe_lab.run_feasibility_group(group)
            if group.kind == "feasibility"
            else probe_lab.run_group(group)
        )
        for group in probe_lab.GROUPS
    }
    all_results["time"] = probe_lab.run_time_group()
    assert probe_lab.check(all_results) == []
