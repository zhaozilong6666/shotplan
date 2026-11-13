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


def test_knowledge_metadata_and_cross_references():
    locations, styles = load_locations(), load_styles()
    assert locations["version"] and locations["updated_at"]
    assert styles["version"] and styles["updated_at"]

    all_tags = {
        tag
        for city in locations["cities"].values()
        for item in city["locations"]
        for tag in item["tags"]
    }
    for style in styles["styles"]:
        unknown = set(style["suitable_tags"]) - all_tags
        assert not unknown, f"style {style['id']} references unknown tags: {unknown}"

    for city_id, city in locations["cities"].items():
        assert any(item["indoor"] for item in city["locations"]), (
            f"city {city_id} has no indoor fallback"
        )


def test_every_entry_records_its_provenance():
    """Prices and opening facts age; an entry without a review date cannot."""
    from datetime import date

    locations, styles = load_locations(), load_styles()
    assert locations["provenance_note"] and styles["provenance_note"]

    today = date.today()
    verified: list[date] = []
    entries = [
        ("location", item)
        for city in locations["cities"].values()
        for item in city["locations"]
    ] + [("style", item) for item in styles["styles"]]
    for kind, item in entries:
        assert item["source"].strip(), f"{kind} {item['id']} has no source"
        stamp = date.fromisoformat(item["last_verified_at"])
        assert stamp <= today, f"{kind} {item['id']} is verified in the future"
        verified.append(stamp)

    newest = max(verified)
    assert (today - newest).days <= 365, (
        "the knowledge base has not been reviewed for over a year; "
        "re-check prices/opening facts and bump last_verified_at"
    )


def test_loader_rejects_a_blank_source(tmp_path):
    import json

    from shotplan.knowledge.loader import DATA_DIR

    data = json.loads((DATA_DIR / "locations.json").read_text(encoding="utf-8"))
    data["cities"]["xian"]["locations"][0]["source"] = "  "
    path = tmp_path / "locations.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(KnowledgeError, match="empty source"):
        load_locations(path)


def test_loader_rejects_an_invalid_verification_date(tmp_path):
    import json

    from shotplan.knowledge.loader import DATA_DIR

    data = json.loads((DATA_DIR / "locations.json").read_text(encoding="utf-8"))
    data["cities"]["xian"]["locations"][0]["last_verified_at"] = "last spring"
    path = tmp_path / "locations.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(KnowledgeError, match="invalid last_verified_at"):
        load_locations(path)


def test_case_requests_only_reference_known_locations():
    """A typo in a case's must_include/avoid would silently change its meaning."""
    import json
    from pathlib import Path

    locations = load_locations()
    known = {
        city_id: {item["id"] for item in city["locations"]}
        for city_id, city in locations["cities"].items()
    }
    root = Path(__file__).resolve().parents[1]
    paths = list((root / "evals" / "cases").glob("*.jsonl"))
    paths.append(root / "evals" / "challenge" / "cases.jsonl")
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            request = case["request"]
            city = request.get("city")
            for key in ("must_include", "avoid"):
                unknown = set(request.get(key, [])) - known.get(city, set())
                assert not unknown, f"{case.get('id')}: unknown {key} ids {unknown}"


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
