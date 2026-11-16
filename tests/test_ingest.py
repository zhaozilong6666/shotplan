"""Extraction must be right on real pages, and silent when it cannot tell."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ingest_city  # noqa: E402

# Verbatim fragments from the pages in sources.json (2026-11-16).
WUHOUCI = """
票务 全价门票 ￥ 50 /人 半价门票 ￥ 25 /人 年 票 ￥ 100 /人/年
09:00开馆时间 18:00 闭馆时间 17:00 停止售票时间
"""

GUGONG = """
除法定节假日，故宫博物院全年实行周一闭馆 参观须提前订票，最早可于参观7日前20:00开始预订。
08:30 开放入馆时间 开放入馆 15:30 停止入馆时间 停止入馆 16:30 闭馆时间 闭馆
在线订票 咨询电话 400-950-1925 服务时间：08:00–20:00
关于2026年医务人员和志愿者主题免费开放日的公告
"""


def test_price_from_a_full_price_label():
    facts = ingest_city.extract(WUHOUCI)
    assert facts["cost_cny"] == 50
    assert "open_time" in facts


def test_opening_hours_survive_a_stop_time_in_between():
    facts = ingest_city.extract(GUGONG)
    assert facts["open_time"] == "08:30-16:30"
    assert facts["last_entry"] == "15:30"
    assert facts["closed_days"] == ["monday"]


def test_a_free_admission_notice_is_not_a_price():
    """「免费开放日的公告」 is a headline; reading it as 0 CNY is worse than nothing."""
    facts = ingest_city.extract(GUGONG)
    assert "cost_cny" not in facts


def test_the_support_line_is_not_opening_hours():
    facts = ingest_city.extract("服务时间：08:00–20:00 欢迎致电")
    assert "open_time" not in facts


def test_booking_window_is_captured():
    assert ingest_city.extract(GUGONG)["booking"] == "提前 7 天预约"


def test_text_extraction_drops_scripts():
    html = "<style>.x{color:red}</style><p>门票 50 元</p><script>var a=1</script>"
    assert ingest_city.to_text(html) == "门票 50 元"


def test_check_reports_a_price_difference(monkeypatch, tmp_path, capsys):
    """The whole point of `check`: a price that drifted must be visible."""
    monkeypatch.setattr(ingest_city, "fetch", lambda url: "<p>全价门票 ￥ 80 /人</p>")
    monkeypatch.setattr(ingest_city, "registered", lambda: {"chengdu/wuhouci": {"url": "u"}})
    monkeypatch.setattr(ingest_city, "DATA", ingest_city.DATA)

    ingest_city.check(None)
    out = capsys.readouterr().out
    assert "DIFF" in out and "80" in out
    assert "1 price difference" in out


def test_check_is_quiet_when_the_price_still_matches(monkeypatch, capsys):
    monkeypatch.setattr(ingest_city, "fetch", lambda url: "<p>全价门票 ￥ 50 /人</p>")
    monkeypatch.setattr(ingest_city, "registered", lambda: {"chengdu/wuhouci": {"url": "u"}})

    ingest_city.check(None)
    out = capsys.readouterr().out
    assert "DIFF" not in out
    assert "0 price difference" in out


def test_sources_registry_points_at_known_locations():
    sources = json.loads(ingest_city.SOURCES.read_text(encoding="utf-8"))
    data = json.loads(ingest_city.DATA.read_text(encoding="utf-8"))
    known = {
        f"{city_id}/{loc['id']}"
        for city_id, city in data["cities"].items()
        for loc in city["locations"]
    }
    assert sources, "sources.json should not be empty"
    for key in sources:
        assert key in known, f"{key} is registered but not in the knowledge base"
