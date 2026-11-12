import pytest

from shotplan.domain.errors import KnowledgeError
from shotplan.knowledge.loader import load_locations, load_styles
from shotplan.knowledge.tools import KnowledgeTools


def test_knowledge_base_loads_with_expected_ids(tools):
    assert "xian" in tools.city_ids()
    assert "city_wall" in tools.location_ids("xian")
    assert "tang" in tools.style_ids()


def test_locations_have_required_shape():
    data = load_locations()
    for city in data["cities"].values():
        for item in city["locations"]:
            assert isinstance(item["tags"], list) and item["tags"]
            assert item["min_minutes"] > 0
            assert item["cost_cny"] >= 0


def test_styles_have_required_shape():
    assert all(len(s["price_cny"]) == 2 for s in load_styles()["styles"])


def test_unknown_city_raises(tools):
    with pytest.raises(KnowledgeError):
        tools.city("atlantis")


def test_search_locations_respects_filters(tools):
    indoor = tools.search_locations("xian", indoor=True)
    assert indoor and all(x["indoor"] for x in indoor)
    cheap = tools.search_locations("xian", max_cost=0)
    assert cheap and all(x["cost_cny"] == 0 for x in cheap)
    excluded = tools.search_locations("xian", exclude=["city_wall"])
    assert all(x["id"] != "city_wall" for x in excluded)


def test_transfer_never_below_minimum(tools):
    from shotplan.domain.constraints import MIN_TRANSFER_MINUTES

    assert tools.estimate_transfer("xian", "city_wall", "city_wall") == 0
    assert tools.estimate_transfer("xian", "city_wall", "qinglong_temple") >= MIN_TRANSFER_MINUTES
    with pytest.raises(KnowledgeError):
        tools.estimate_transfer("xian", "city_wall", "nowhere")


@pytest.mark.parametrize(
    "date,expected",
    [("2026-01-10", "15:00"), ("2026-04-12", "16:00"),
     ("2026-07-05", "17:00"), ("2026-10-04", "16:00")],
)
def test_golden_window_follows_season(tools, date, expected):
    start, _end = tools.light_window(date)
    assert start == expected


def test_best_style_falls_back_when_budget_is_tight(tools):
    assert tools.best_style_for(["tang"], 1500)["id"] == "tang"
    assert tools.best_style_for(["tang"], 350)["price_cny"][0] <= 350


def test_context_for_is_compact_and_citable(tools):
    from shotplan.domain.models import PlanRequest

    context = tools.context_for(PlanRequest())
    assert context["candidate_locations"]
    assert context["golden_light"]["start"]
    assert all("id" in item for item in context["candidate_locations"])
    assert isinstance(KnowledgeTools(), KnowledgeTools)
