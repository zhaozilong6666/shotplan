"""The trust boundary, attacked on purpose.

Model output is hostile input. These cases are the ones that actually break
parsers in production, not the toy "missing brace" example:

* a closing brace *inside a string value* (kills find/rfind scanning),
* prose after the JSON that contains a brace (same),
* an example object printed before the real answer (silently returns the wrong one),
* trailing commas and full-width punctuation from Chinese output,
* a number that is not a number ("免费", "大约三百", True),
* 1 MB of junk, and 1000-level nesting (must be ParseError, never RecursionError).

The contract under test: recovery is allowed and recorded; guessing a number is
not; nothing escapes as a bare ValueError/TypeError, because the pipeline only
catches ShotplanError and anything else becomes a 500.
"""

from __future__ import annotations

import json

import pytest

from shotplan.agents.planner import extract_json, extract_json_detailed, parse_plan
from shotplan.domain.errors import ParseError
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan


def _slot(**overrides):
    base = {
        "start": "09:00",
        "end": "10:00",
        "activity": "shoot",
        "location_id": "city_wall",
        "label": "城墙拍摄",
        "cost_cny": 120,
        "source_refs": ["locations.json#city_wall"],
        "notes": "",
    }
    base.update(overrides)
    return base


def _payload(*slots, **extra):
    body = {"slots": list(slots) or [_slot()], "styling": {"style_id": "tang"}}
    body.update(extra)
    return body


# --- 1. braces inside strings: the find/rfind killer --------------------------


def test_braces_inside_string_values_survive():
    text = json.dumps(_payload(_slot(notes="遇到 } 就补妆；再遇到 }}} 就换点")))
    plan = parse_plan(text, PlanRequest(), "mock", "v1")
    assert plan.slots[0].notes.endswith("就换点")


def test_prose_after_json_containing_a_brace_is_ignored():
    text = (
        "这是方案：\n"
        + json.dumps(_payload(), ensure_ascii=False)
        + "\n如果下雨，把活动改成 } 里备注的室内棚。"
    )
    plan = parse_plan(text, PlanRequest(), "mock", "v1")
    assert plan.slots


# --- 2. several objects: pick the answer, not the example --------------------


def test_example_object_before_the_answer_is_skipped_and_recorded():
    example = {"slots": [{"start": "00:00", "end": "00:00", "activity": "shoot"}]}
    text = (
        "格式示例（不要照抄）：\n"
        + json.dumps(example)
        + "\n实际方案：\n"
        + json.dumps(_payload(), ensure_ascii=False)
    )
    notes: list[str] = []
    plan = parse_plan(text, PlanRequest(), "mock", "v1", notes=notes)
    assert isinstance(plan.slots[0], object)
    assert plan.slots[0].start == "09:00"  # the real one, not the 00:00 example
    assert any("skipped" in note for note in notes)


def test_first_valid_object_wins_when_both_fit():
    first = _payload(_slot(start="09:00", end="10:00"))
    second = _payload(_slot(start="15:00", end="16:00"))
    text = json.dumps(first) + "\n" + json.dumps(second)
    plan = parse_plan(text, PlanRequest(), "mock", "v1")
    assert plan.slots[0].start == "09:00"


# --- 3. punctuation damage: repaired, but only when unambiguous --------------


def test_trailing_comma_is_repaired_and_recorded():
    text = '{"slots": [{"start": "09:00", "end": "10:00", "activity": "shoot",' \
           ' "location_id": "city_wall", "cost_cny": 100,},],}'
    notes: list[str] = []
    plan = parse_plan(text, PlanRequest(), "mock", "v1", notes=notes)
    assert plan.slots[0].cost_cny == 100
    assert any("trailing comma" in note for note in notes)


def test_full_width_quotes_are_repaired_only_when_balanced():
    text = (
        "\uff5b"  # full-width brace: not JSON at all, must not be salvaged blindly
        "\u201cslots\u201d: []"
    )
    with pytest.raises(ParseError):
        extract_json(text)


def test_python_literals_are_rejected_not_guessed():
    text = "{'slots': [{'start': '09:00', 'end': '10:00'}]}"
    with pytest.raises(ParseError) as excinfo:
        extract_json(text)
    assert "valid JSON" in str(excinfo.value)


# --- 4. numbers: refuse to invent one ---------------------------------------


@pytest.mark.parametrize(
    ("cost", "expected"),
    [(300, 300), (300.7, 300), ("300", 300), ("¥300", 300), ("1,200", 1200), ("免费", 0)],
)
def test_numeric_forms_that_are_unambiguously_a_price(cost, expected):
    plan = parse_plan(
        _payload(_slot(cost_cny=cost)), PlanRequest(), "mock", "v1"
    )
    assert plan.slots[0].cost_cny == expected


@pytest.mark.parametrize("cost", ["大约三百", "很贵", True, [300], {"amount": 300}])
def test_unreadable_cost_is_a_parse_error_not_a_crash(cost):
    with pytest.raises(ParseError) as excinfo:
        parse_plan(_payload(_slot(cost_cny=cost)), PlanRequest(), "mock", "v1")
    assert "cost_cny" in str(excinfo.value)
    # The exact regression: this used to be ValueError from int("免费").
    assert excinfo.type is ParseError


def test_a_bad_slot_number_surfaces_as_error_status_not_an_exception():
    """End to end: one hostile number must not take the API down."""

    class HostileProvider:
        name = "hostile"
        prompt_version = "hostile-v1"

        def generate(self, request, context, repair_hint=None):
            from shotplan.agents.base import GenerationResult

            return GenerationResult(
                payload=_payload(_slot(cost_cny="很贵")),
                tokens_in=1,
                tokens_out=1,
                cost_cny=0.0,
                duration_ms=0.5,
                model="hostile",
            )

    result = run_plan(PlanRequest(), provider=HostileProvider(), max_repairs=0)
    assert result.status == "error"
    assert result.validation.hard_pass is False
    assert result.validation.issues[0].code == "PIPELINE_ERROR"
    json.dumps(result.to_dict())  # must stay serialisable


# --- 5. volume and depth: hostile shapes at scale ---------------------------


def test_one_megabyte_of_junk_is_a_parse_error_with_a_bounded_message():
    junk = "这不是 JSON，" * 100_000
    with pytest.raises(ParseError) as excinfo:
        extract_json(junk)
    assert len(str(excinfo.value)) < 400


def test_deep_nesting_never_escapes_as_recursion_error():
    """3000 levels is deep enough to trip CPython's limit on some builds.

    Whether json.loads parses it or raises is platform-dependent (the C scanner
    and the pure-Python one differ), so the assertion must not assume either.
    What must hold everywhere: the outcome is a dict or a ParseError, never a
    RecursionError escaping the trust boundary.
    """
    text = '{"a":' * 3000 + "1" + "}" * 3000
    try:
        result = extract_json(text)
    except ParseError:
        return
    assert isinstance(result, dict)


def test_a_recursion_error_from_the_scanner_becomes_parse_error(monkeypatch):
    """Deterministic coverage of the guard, independent of the real limit."""
    import json as json_module

    def explode(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(json_module, "loads", explode)
    with pytest.raises(ParseError):
        extract_json('{"slots": []}')


def test_empty_bytes_none_and_list_are_rejected_cleanly():
    for payload in ("", "   \n\t ", b"", None, [], 42):
        with pytest.raises(ParseError):
            extract_json(payload)  # type: ignore[arg-type]


def test_utf8_bom_and_zero_width_characters_are_stripped():
    text = "\ufeff\u200b" + json.dumps(_payload())
    assert extract_json_detailed(text)[0]["slots"]


def test_notes_are_empty_for_a_clean_payload():
    obj, notes = extract_json_detailed(json.dumps(_payload()))
    assert obj["slots"] and notes == []


def test_mutated_payloads_only_ever_raise_parse_error():
    """Deterministic mutation fuzzing of the trust boundary.

    Not a coverage exercise: the assertion is that *no* input shape can escape
    as anything other than ParseError. A ValueError here is an HTTP 500.
    """
    import random

    rng = random.Random(20261113)
    base = json.dumps(
        _payload(_slot(), _slot(start="10:00", end="11:00", cost_cny="¥0")),
        ensure_ascii=False,
    )
    alphabet = '{}[]",:0123456789abc\x00中文化免费 '

    for _ in range(300):
        chars = list(base)
        for _ in range(rng.randint(1, 4)):
            position = rng.randrange(len(chars))
            roll = rng.random()
            if roll < 0.45:
                chars[position] = rng.choice(alphabet)
            elif roll < 0.75:
                chars.insert(position, rng.choice(alphabet))
            elif len(chars) > 2:
                chars.pop(position)
        try:
            parse_plan("".join(chars), PlanRequest(), "mock", "v1")
        except ParseError:
            continue
        except RecursionError as exc:  # pragma: no cover - the failure we hunt
            raise AssertionError("recursion escaped the trust boundary") from exc
