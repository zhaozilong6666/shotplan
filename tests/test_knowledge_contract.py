"""The expansion gate must reject bad records, not just bless the current ones."""

from __future__ import annotations

import datetime as dt
import json

from scripts import validate_knowledge as vk

from shotplan.knowledge.tools import get_tools

# Must not be earlier than the newest last_verified_at in the knowledge base,
# otherwise fresh data is reported as "future". Bump when data is re-verified.
TODAY = dt.date(2026, 11, 16)


def test_current_knowledge_passes_every_rule():
    problems, stats = vk.validate(TODAY)
    assert problems == [], problems[:10]
    # Counts come from the data contract, not a remembered number: hard-coding
    # them means every city addition silently breaks this test.
    assert stats["cities"] == len(get_tools().city_ids())
    assert stats["locations"] == sum(
        len(get_tools().locations(city_id)) for city_id in get_tools().city_ids()
    )
    assert stats["styles"] == len(get_tools().styles())


def test_gate_detects_missing_source_bad_light_and_future_date(tmp_path, monkeypatch):
    payload = {
        "cities": {
            "fakecity": {
                "name": "示例", "center": [0, 0],
                "locations": [{
                    "id": "spot", "source": "", "last_verified_at": "2099-01-01",
                    "name": "景点", "district": "x", "tags": ["tang"],
                    "best_light": ["noon"], "indoor": "false", "min_minutes": 0,
                    "travel_min_from_center": -1, "cost_cny": -5,
                    "crowd": "unknown", "weather_risk": "unknown", "notes": "",
                }],
            }
        }
    }
    styles = {"styles": [{
        "id": "s", "source": "", "last_verified_at": "2099-01-01",
        "name": "风格", "era": "唐", "makeup_minutes": 0, "costume_minutes": 0,
        "price_cny": [900, 400], "suitable_tags": [], "avoid_tags": [], "notes": "",
    }]}
    monkeypatch.setattr(vk, "DATA", tmp_path)
    (tmp_path / "locations.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "styles.json").write_text(json.dumps(styles, ensure_ascii=False), encoding="utf-8")
    problems, _ = vk.validate(TODAY)
    joined = " ".join(problems)
    assert "empty source" in joined and "invalid/future" in joined
    assert "best_light" in joined and "indoor" in joined
    assert "price_cny" in joined and "makeup_minutes" in joined
