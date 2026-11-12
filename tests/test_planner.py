import pytest

from shotplan.agents.planner import extract_json, parse_plan
from shotplan.domain.errors import ParseError
from shotplan.domain.models import PlanRequest


def test_extract_json_handles_markdown_fence():
    raw = '这是方案：\n```json\n{"slots": []}\n```\n希望喜欢'
    assert extract_json(raw) == {"slots": []}


def test_extract_json_handles_surrounding_prose():
    assert extract_json('Sure! {"slots": []} done') == {"slots": []}


def test_extract_json_rejects_garbage():
    with pytest.raises(ParseError):
        extract_json("no json here at all")


def test_parse_plan_normalizes_and_sorts():
    payload = {
        "slots": [
            {"start": "16:00", "end": "17:00", "activity": "SHOOT",
             "location_id": "city_wall", "label": "拍摄", "cost_cny": 100},
            {"start": "14:00", "end": "15:00", "activity": "makeup",
             "location_id": "indoor_studio", "label": "妆造"},
        ],
        "styling": {"style_id": "tang"},
    }
    plan = parse_plan(payload, PlanRequest(), "mock", "v1")
    assert [s.start for s in plan.slots] == ["14:00", "16:00"]
    assert plan.slots[1].activity == "shoot"
    assert plan.total_cost_cny == 100


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"slots": []}, "empty"),
        ({"slots": [{"start": "09:00"}]}, "missing fields"),
        ({"slots": [{"start": "09:00", "end": "10:00", "activity": "fly",
                     "location_id": "x"}]}, "unknown activity"),
        ({"slots": [{"start": "10:00", "end": "09:00", "activity": "shoot",
                     "location_id": "x"}]}, "reversed time"),
        ({"slots": [{"start": "bad", "end": "10:00", "activity": "shoot",
                     "location_id": "x"}]}, "bad time"),
        ({"slots": [{"start": "09:00", "end": "10:00", "activity": "shoot",
                     "location_id": "x"}], "styling": "not-a-dict"}, "bad styling"),
    ],
)
def test_parse_plan_rejects_bad_output(payload, reason):
    with pytest.raises(ParseError, match=".*"):
        parse_plan(payload, PlanRequest(), "mock", "v1")


def test_parse_plan_preserves_traceability_fields():
    payload = {
        "slots": [{"start": "09:00", "end": "10:00", "activity": "shoot",
                   "location_id": "city_wall", "source_refs": ["locations.json#city_wall"]}],
        "source_refs": ["locations.json#city_wall", "styles.json#tang"],
        "alternatives": [{"trigger": "rain"}],
        "assumptions": ["假设一"],
    }
    plan = parse_plan(payload, PlanRequest(), "openai_compatible", "planner-v2")
    assert plan.provider == "openai_compatible"
    assert plan.prompt_version == "planner-v2"
    assert len(plan.source_refs) == 2
    assert plan.slots[0].source_refs == ["locations.json#city_wall"]
