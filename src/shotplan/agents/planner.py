"""Turns model output into a trusted Plan object.

The parser is the trust boundary: anything that cannot be parsed or references
an unknown location is rejected or normalised here, before the validator runs.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..domain.errors import ParseError
from ..domain.models import Plan, PlanRequest, TimeSlot, to_minutes

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_REQUIRED_SLOT_FIELDS = {"start", "end", "activity", "location_id"}
_VALID_ACTIVITIES = {"makeup", "travel", "shoot", "rest", "backup"}


def extract_json(payload: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        raise ParseError(f"unsupported model payload type: {type(payload).__name__}")
    text = payload.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    else:
        # Tolerate a leading/trailing explanation around a JSON object.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"model did not return valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ParseError("model JSON must be an object")
    return obj


def parse_plan(
    payload: dict[str, Any] | str,
    request: PlanRequest,
    provider: str,
    prompt_version: str,
) -> Plan:
    raw = extract_json(payload)
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
        refs = item.get("source_refs") or []
        slots.append(
            TimeSlot(
                start=str(item["start"]),
                end=str(item["end"]),
                activity=activity,
                location_id=str(item["location_id"]),
                label=str(item.get("label") or activity),
                cost_cny=int(item.get("cost_cny") or 0),
                source_refs=[str(r) for r in refs] if isinstance(refs, list) else [],
                notes=str(item.get("notes") or ""),
            )
        )

    slots.sort(key=lambda s: to_minutes(s.start))
    computed_cost = sum(s.cost_cny for s in slots)
    total = raw.get("total_cost_cny")
    total_cost = int(total) if total is not None else computed_cost

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
