"""Reading numbers out of Chinese sentences, without a model.

These cases exist because the follow-up layer's first version could only add a
fixed step: "便宜 300" and "预算两千" both meant nothing to it. Each test below
is a shape a client actually typed during the measurement.
"""

from __future__ import annotations

import pytest

from shotplan.nlq import clock_times, cn_to_number, fmt_clock, numbers_in


@pytest.mark.parametrize(
    "text,expected",
    [
        ("两千", 2000),
        ("两千五", 2500),  # spoken elision: the last digit is hundreds
        ("一千二", 1200),
        ("三百五", 350),
        ("八百", 800),
        ("十五", 15),
        ("二十", 20),
        ("一万二", 12000),
        ("五", 5),
    ],
)
def test_chinese_numerals(text: str, expected: int):
    assert cn_to_number(text) == expected


def test_a_run_without_a_numerical_reading_is_rejected():
    """The caller must be able to tell "not a number" from zero."""
    assert cn_to_number("") is None
    assert cn_to_number("拍摄") is None


def test_hedges_are_not_quantities():
    """"预算降一点" must not become "budget 1"."""
    assert [q.value for q in numbers_in("预算降一点")] == []
    assert [q.value for q in numbers_in("再松一些")] == []


def test_both_scripts_are_read_in_position_order():
    found = numbers_in("预算两千，时间 4 个小时")
    assert [q.value for q in found] == [2000, 4]
    assert found == sorted(found, key=lambda q: q.start)


def test_a_clock_time_needs_its_period_word():
    """Bare "六点" is 06:00 or 18:00; the reader must refuse to pick."""
    assert clock_times("我六点要坐飞机") == []
    assert clock_times("下午五点的高铁") == [17 * 60]
    assert clock_times("下午四点半得走") == [16 * 60 + 30]
    assert clock_times("上午 9 点开始") == [9 * 60]


def test_noon_is_not_shifted_twice():
    assert clock_times("中午十二点") == [12 * 60]


def test_clock_formatting_round_trips():
    assert fmt_clock(17 * 60) == "17:00"
    assert fmt_clock(16 * 60 + 30) == "16:30"
