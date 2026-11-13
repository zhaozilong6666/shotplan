"""Turns model output into a trusted Plan object.

The parser is the trust boundary: anything that cannot be parsed or references
an unknown location is rejected or normalised here, before the validator runs.

"Model output is sometimes malformed JSON" undersells it. In practice it also
arrives wrapped in prose, fenced, with trailing commas, with full-width
punctuation, as two objects (an example plus the answer), or with the right
shape and the wrong types ("免费" where an int belongs). Every one of those is
a case here, and none of them may escape as a bare ValueError: an unhandled
exception at the trust boundary becomes a 500, not a structured refusal.
Two rules follow, and they are tested in tests/test_parser_adversarial.py:

1. Recover what is unambiguously recoverable (fences, prose, a second object,
   trailing commas) and *say* so in the trace, never silently.
2. Never guess a number. A cost that cannot be read is a ParseError, because
   coercing it to 0 would make an over-budget plan look affordable.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..domain.errors import ParseError
from ..domain.models import Plan, PlanRequest, TimeSlot, to_minutes

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_LEADING_NUMBER = re.compile(r"^[¥￥]?\s*(-?\d+(?:\.\d+)?)")
# Zero-width / BOM characters survive copy-paste and make json.loads fail with a
# message that points at column 1, which is useless for debugging.
_INVISIBLE = dict.fromkeys(map(ord, "\ufeff\u200b\u200c\u200d\u2060"), None)
_SMART_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})
_FREE_MARKERS = {"免费", "0元", "free", "n/a", "-", ""}
_MAX_CANDIDATES = 8

_REQUIRED_SLOT_FIELDS = {"start", "end", "activity", "location_id"}
_VALID_ACTIVITIES = {"makeup", "travel", "shoot", "rest", "backup"}


def _balanced_objects(text: str) -> list[str]:
    """Every top-level ``{...}`` span, aware of strings and escapes.

    The previous implementation used ``text.find("{")`` and ``text.rfind("}")``.
    That truncates as soon as the prose *after* the JSON contains a closing
    brace, and merges two objects into one unparseable blob. One state-machine
    pass fixes both, and it is what makes the "model printed an example first"
    case recoverable at all.
    """
    spans: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                spans.append(text[start : index + 1])
                start = -1
    return spans


def _load_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _repair(text: str) -> tuple[str, list[str]]:
    """Conservative, recorded repairs. Applied only after a strict parse failed."""
    notes: list[str] = []
    fixed = text
    if _TRAILING_COMMA.search(fixed):
        fixed = _TRAILING_COMMA.sub(r"\1", fixed)
        notes.append("removed trailing comma before a closing bracket")
    if fixed.count("\u201c") and fixed.count("\u201c") == fixed.count("\u201d"):
        fixed = fixed.translate(_SMART_QUOTES)
        notes.append("normalised full-width quotation marks")
    return fixed, notes


def iter_json_candidates(
    payload: dict[str, Any] | str | bytes,
) -> list[tuple[dict[str, Any], list[str]]]:
    """Every object the payload yields, in the order it appeared.

    ``required_key``-style heuristics are not enough on their own: a model that
    prints a *schema example* first also contains the key ``slots``, so "first
    object with slots" returns the example. The caller has to be able to try
    each candidate against the real builder and keep the first that works.
    """
    if isinstance(payload, dict):
        return [(payload, [])]
    if isinstance(payload, (bytes, bytearray)):
        payload = bytes(payload).decode("utf-8", errors="replace")
    if not isinstance(payload, str):
        raise ParseError(f"unsupported model payload type: {type(payload).__name__}")

    text = payload.translate(_INVISIBLE).strip()
    if not text:
        raise ParseError("model returned an empty payload")

    candidates: list[str] = []
    for fenced in _JSON_FENCE.findall(text):
        candidate = fenced.strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for span in _balanced_objects(text):
        if span not in candidates:
            candidates.append(span)
    candidates = candidates[:_MAX_CANDIDATES]
    if not candidates:
        raise ParseError(
            f"no JSON object in model payload (first 160 chars: {text[:160]!r})"
        )

    parsed: list[tuple[dict[str, Any], list[str]]] = []
    first_failure: str | None = None
    for candidate in candidates:
        obj = _load_object(candidate)
        if obj is not None:
            parsed.append((obj, []))
            continue
        repaired, repair_notes = _repair(candidate)
        obj = _load_object(repaired)
        if obj is not None:
            parsed.append((obj, repair_notes))
            continue
        if first_failure is None:
            try:
                json.loads(candidate)
            except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                first_failure = str(exc)

    if not parsed:
        raise ParseError(f"model did not return valid JSON: {first_failure}")
    return parsed


def extract_json_detailed(
    payload: dict[str, Any] | str | bytes,
    *,
    required_key: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Parse model output and report *how* it was recovered.

    Returning the notes (rather than logging) keeps this function pure and lets
    the pipeline put the salvage evidence in the trace.
    """
    parsed = iter_json_candidates(payload)
    index, (obj, notes) = 0, parsed[0]
    if required_key is not None:
        index, (obj, notes) = next(
            ((i, item) for i, item in enumerate(parsed) if required_key in item[0]),
            (0, parsed[0]),
        )
    notes = list(notes)
    if index:
        notes.append(f"skipped {index} earlier JSON object(s) that did not fit")
    if notes:
        notes.append("payload was recovered, not returned clean")
    return obj, notes


def extract_json(payload: dict[str, Any] | str | bytes) -> dict[str, Any]:
    """Backwards-compatible wrapper: the object only, no salvage notes."""
    return extract_json_detailed(payload)[0]


def _as_int(value: Any, *, field: str) -> int:
    """Strict-ish number coercion that refuses to invent a value.

    Accepts ``300``, ``"300"``, ``"¥300"`` and the free markers; rejects
    everything else. Coercing an unreadable cost to 0 would be the worst
    possible failure mode here: the budget rule would then pass on a plan that
    is wildly over budget.
    """
    if isinstance(value, bool):
        raise ParseError(f"{field} must be a number, got a boolean")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("\uff0c", "")
        if text.lower() in _FREE_MARKERS:
            return 0
        match = _LEADING_NUMBER.match(text)
        if match:
            return int(float(match.group(1)))
    raise ParseError(f"{field} must be a number, got {value!r}")


def _build_plan(
    raw: dict[str, Any],
    request: PlanRequest,
    provider: str,
    prompt_version: str,
    notes: list[str] | None,
) -> Plan:
    raw_slots = raw.get("slots")
    if not isinstance(raw_slots, list) or not raw_slots:
        raise ParseError("plan must contain a non-empty 'slots' list")

    slots: list[TimeSlot] = []
    for index, item in enumerate(raw_slots):
        if not isinstance(item, dict):
            raise ParseError(f"slot {index} is not an object")
        missing = _REQUIRED_SLOT_FIELDS - set(item)
        if missing:
            raise ParseError(f"slot {index} missing fields: {sorted(missing)}")
        activity = str(item["activity"]).strip().lower()
        if activity not in _VALID_ACTIVITIES:
            raise ParseError(f"slot {index} has unknown activity {activity!r}")
        try:
            start, end = to_minutes(str(item["start"])), to_minutes(str(item["end"]))
        except ValueError as exc:
            raise ParseError(f"slot {index} has an invalid time: {exc}") from exc
        if end <= start:
            raise ParseError(f"slot {index} ends before it starts")

        raw_cost = item.get("cost_cny")
        refs = item.get("source_refs") or []
        slots.append(
            TimeSlot(
                start=str(item["start"]),
                end=str(item["end"]),
                activity=activity,
                location_id=str(item["location_id"]),
                label=str(item.get("label") or activity),
                cost_cny=_as_int(raw_cost, field=f"slot {index}.cost_cny")
                if raw_cost is not None
                else 0,
                source_refs=[str(r) for r in refs] if isinstance(refs, list) else [],
                notes=str(item.get("notes") or ""),
            )
        )

    slots.sort(key=lambda s: to_minutes(s.start))
    computed_cost = sum(s.cost_cny for s in slots)
    total = raw.get("total_cost_cny")
    total_cost = _as_int(total, field="total_cost_cny") if total is not None else computed_cost

    styling = raw.get("styling") or {}
    if not isinstance(styling, dict):
        raise ParseError("'styling' must be an object")
    alternatives = raw.get("alternatives") or []
    assumptions = raw.get("assumptions") or []
    refs = raw.get("source_refs") or []
    if not isinstance(alternatives, list) or not isinstance(assumptions, list):
        raise ParseError("'alternatives' and 'assumptions' must be lists")
    if not isinstance(refs, list):
        raise ParseError("'source_refs' must be a list")

    return Plan(
        request_id=request.request_id,
        city=request.city,
        date=request.date,
        slots=slots,
        styling=styling,
        total_cost_cny=total_cost,
        alternatives=[a for a in alternatives if isinstance(a, dict)],
        assumptions=[str(a) for a in assumptions],
        source_refs=[str(r) for r in refs],
        provider=provider,
        prompt_version=prompt_version,
    )


def parse_plan(
    payload: dict[str, Any] | str | bytes,
    request: PlanRequest,
    provider: str,
    prompt_version: str,
    notes: list[str] | None = None,
) -> Plan:
    """Parse model output into a Plan, or raise ParseError.

    Every candidate object is tried against the real builder, so a payload
    containing a schema example before the answer resolves to the answer
    rather than to whichever object merely *looks* like a plan.

    The builder call is wrapped: json.loads is not the only thing that can
    throw on hostile input. ``int("免费")`` used to escape as ValueError, which
    the pipeline does not catch because it only handles ShotplanError, so a
    single bad number in one slot took down the request with a 500.
    """
    candidates = iter_json_candidates(payload)
    failures: list[str] = []
    for index, (raw, candidate_notes) in enumerate(candidates):
        collected = list(candidate_notes)
        try:
            plan = _build_plan(raw, request, provider, prompt_version, collected)
        except ParseError as exc:
            failures.append(str(exc))
            continue
        except (TypeError, ValueError, KeyError, AttributeError, OverflowError) as exc:
            failures.append(f"unusable field types: {exc}")
            continue
        if notes is not None:
            notes.extend(collected)
            if index:
                notes.append(f"skipped {index} JSON object(s) that could not build a plan")
        return plan

    detail = failures[0] if len(failures) == 1 else (
        f"none of {len(failures)} JSON objects could build a plan; first: {failures[0]}"
    )
    raise ParseError(detail)
