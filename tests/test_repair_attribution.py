"""The attribution report must handle both snapshot shapes and stay honest."""

from __future__ import annotations

from evals.repair_attribution import _passed, _repairs_of, build


def test_both_snapshot_shapes_are_read_the_same_way():
    assert _repairs_of({"repairs": 2}) == 2
    assert _repairs_of({"max_repairs": 1}) == 1
    assert _repairs_of({}) == 0
    assert _passed({"hard_pass": True})
    assert not _passed({"hard_pass": False})
    assert _passed({"pass_rate": 1.0})
    assert not _passed({"pass_rate": 0.5})


def test_report_uses_real_committed_data_and_states_its_limits():
    text = build()
    assert "按初始失败类型" in text
    assert "按修复轮次" in text
    assert "MISSING_REQUIRED_LOCATION" in text
    assert "样本量小于 3 的类型一律不下结论" in text
    assert "不等于修复是唯一原因" in text
