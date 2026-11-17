"""The solar approximation must be deterministic and geographically sane."""

from __future__ import annotations

from datetime import date

from shotplan.solar import golden_window, sunset_minutes


def test_sunset_matches_known_values_for_xian():
    assert sunset_minutes(date(2026, 10, 4), 34.34, 108.94) == 18 * 60 + 26
    assert sunset_minutes(date(2026, 1, 10), 34.34, 108.94) == 17 * 60 + 51
    assert sunset_minutes(date(2026, 7, 10), 34.34, 108.94) == 19 * 60 + 59


def test_east_and_north_mean_earlier_sunset_in_october():
    # Same date: Beijing is east and north of Chengdu, so its sun sets earlier.
    beijing = sunset_minutes(date(2026, 10, 4), 39.90, 116.40)
    chengdu = sunset_minutes(date(2026, 10, 4), 30.57, 104.07)
    assert beijing < chengdu


def test_golden_window_is_two_hours_before_sunset():
    start, end = golden_window(date(2026, 10, 4), 34.34, 108.94)
    assert end - start == 120
    assert end == sunset_minutes(date(2026, 10, 4), 34.34, 108.94)
