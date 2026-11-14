"""Freshness checks must catch stale, missing, malformed and future dates."""

from __future__ import annotations

import datetime as dt

from scripts.check_knowledge_freshness import Record, collect, evaluate

TODAY = dt.date(2026, 11, 14)


def test_collect_reads_locations_and_styles():
    locations = {
        "cities": {
            "xian": {"locations": [{"id": "city_wall", "source": "curation:seed", "last_verified_at": "2026-11-13"}]}
        }
    }
    styles = {"styles": [{"id": "tang", "source": "curation:seed", "last_verified_at": "2026-11-13"}]}
    records = collect(locations, styles)
    assert [r.label for r in records] == ["xian/city_wall", "style/tang"]


def test_stale_missing_invalid_and_future_are_all_flagged():
    records = [
        Record("fresh", "curation:seed", "2026-11-13"),
        Record("stale", "curation:seed", "2025-01-01"),
        Record("no-source", "", "2026-11-13"),
        Record("bad-date", "curation:seed", "not-a-date"),
        Record("future", "curation:seed", "2027-01-01"),
    ]
    verdict = evaluate(records, TODAY, 180)
    assert [row[0] for row in verdict.stale] == ["stale"]
    assert verdict.missing == ["no-source"]
    assert {label for label, _ in verdict.invalid} == {"bad-date", "future"}
    assert verdict.oldest[0] == "stale"


def test_exactly_at_the_threshold_is_not_stale():
    at_limit = (TODAY - dt.timedelta(days=180)).isoformat()
    verdict = evaluate([Record("edge", "curation:seed", at_limit)], TODAY, 180)
    assert verdict.stale == []
