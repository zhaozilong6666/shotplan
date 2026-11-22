"""Exports are read-only views of the stored payload; they must not re-run rules."""

from __future__ import annotations

from shotplan.export import plan_to_ics, plan_to_markdown, safe_filename

PAYLOAD = {
    "status": "ok",
    "request": {"city": "xian", "date": "2026-10-04", "budget_cny": 1500},
    "plan": {
        "city": "xian",
        "date": "2026-10-04",
        "total_cost_cny": 570,
        "slots": [
            {
                "start": "16:26",
                "end": "18:26",
                "activity": "shoot",
                "location_id": "city_wall",
                "label": "城墙拍摄",
                "cost_cny": 120,
                "notes": "黄金光线, 含补妆; 风大",
                "source_refs": ["locations.json#city_wall"],
            }
        ],
        "styling": {"name": "唐制", "notes": "含发型"},
        "alternatives": [{"trigger": "rain", "description": "改室内棚", "location_id": "indoor"}],
        "assumptions": ["按秋季黄金光线估算"],
    },
    "validation": {"hard_pass": True, "score": 0.95, "issues": []},
    "trace": {"run_id": "run_test"},
}


def test_markdown_is_readable_and_escapes_table_pipes():
    text = plan_to_markdown(PAYLOAD)
    assert text.startswith("# 西安 · 2026-10-04 拍摄行程")
    assert "| 16:26–18:26 | 城墙拍摄 |" in text
    assert "¥570" in text
    assert "含发型" in text
    assert "改室内棚" in text


def test_ics_has_one_event_per_slot_and_shanghai_time():
    text = plan_to_ics(PAYLOAD)
    assert text.startswith("BEGIN:VCALENDAR")
    assert text.count("BEGIN:VEVENT") == 1
    assert "DTSTART;TZID=Asia/Shanghai:20261004T162600" in text
    assert "DTEND;TZID=Asia/Shanghai:20261004T182600" in text
    assert "SUMMARY:城墙拍摄（西安）" in text
    assert "黄金光线\\, 含补妆\\; 风大" in text
    assert text.rstrip().endswith("END:VCALENDAR")


def test_filename_is_boring():
    assert safe_filename("run/../../etc") == "run-etc"
    assert safe_filename("") == "shotplan"
