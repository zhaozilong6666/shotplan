"""Probes that cannot be expressed as a hard-pass count.

Lower a feasibility floor and an impossible brief becomes "feasible"; shorten a
shoot block below the floor and a **soft** rule fires without changing `hard_pass`.
Neither shows up in the plan-level probe groups, so these tests pin the direct
assertions: the gate decision, the issue, and the score.
"""

from __future__ import annotations

import pytest
from evals import probe_lab


@pytest.fixture(scope="module")
def shoot_rows():
    return probe_lab.run_shoot_floor_group()


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
    all_results["shoot-floor"] = probe_lab.run_shoot_floor_group()
    assert probe_lab.check(all_results) == []


def test_shoot_floor_baseline_hits_only_below_the_floor(shoot_rows):
    baseline = shoot_rows["baseline"]
    assert all(row["pass"] for row in baseline)
    assert sum(row["hit"] for row in baseline) == len(baseline) // 2


def test_disabling_the_shoot_floor_hides_the_below_case(shoot_rows):
    disabled = shoot_rows["disabled"]
    flipped = [row for row in disabled if row["expect_issue"] and not row["pass"]]
    assert flipped, "关掉门槛下限后，低于门槛的断言必须失守"
    # Only the 1.5h tier changes (floor 30 -> 22); 2h/3h are still above 29/44.
    assert {row["id"] for row in flipped} == {"SF01-below"}
    assert all(row["pass"] for row in disabled if not row["expect_issue"])


def test_the_soft_score_drops_for_a_below_floor_block(shoot_rows):
    baseline = shoot_rows["baseline"]
    at_floor = {row["id"].replace("-at-floor", ""): row["score"] for row in baseline if not row["expect_issue"]}
    for row in baseline:
        if row["expect_issue"]:
            assert row["score"] < at_floor[row["id"].replace("-below", "")]
