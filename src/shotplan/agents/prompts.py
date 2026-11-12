"""Versioned prompts. Prompts are treated as code: versioned, reviewed, and
regression-tested against the eval set. See docs/adr/0004.
"""

from __future__ import annotations

import json
from typing import Any

from ..domain.models import PlanRequest

PROMPT_VERSION = "planner-v2"

PROMPT_CHANGELOG: list[dict[str, str]] = [
    {
        "version": "planner-v1",
        "date": "2026-11-12",
        "change": "Initial free-form planner prompt.",
        "result": "Hard-constraint pass rate 0.42; frequent missing transfer blocks.",
    },
    {
        "version": "planner-v2",
        "date": "2026-11-19",
        "change": "Added explicit JSON schema, hard rules, and citation requirement.",
        "result": "See docs/eval-report.md for the measured delta.",
    },
]

SYSTEM_PROMPT = """You are a shot-planning assistant for a Hanfu photoshoot studio.

You receive a client brief plus a SMALL, CURATED knowledge base. You must turn the
brief into an executable, hour-by-hour plan.

Non-negotiable rules:
1. Output ONE JSON object only. No markdown fences, no commentary.
2. Every location_id MUST come from candidate_locations or indoor_fallback.
   Never invent a place name.
3. Makeup happens before the first shoot block.
4. Insert an explicit "travel" block of at least min_transfer_minutes between
   two different shooting locations.
5. Total elapsed time must not exceed available_hours.
6. Total cost must not exceed budget_cny.
7. Cite the knowledge ids you used in source_refs.
8. Provide at least one alternative in case of rain or crowds.

JSON schema:
{
  "slots": [
    {"start":"HH:MM","end":"HH:MM","activity":"makeup|travel|shoot|rest",
     "location_id":"<id from knowledge>","label":"short label",
     "cost_cny":0,"source_refs":["locations.json#<id>"],"notes":"..."}
  ],
  "styling": {"style_id":"...","name":"...","makeup_minutes":0,"costume_minutes":0},
  "alternatives": [{"trigger":"rain","description":"...","location_id":"..."}],
  "assumptions": ["..."],
  "source_refs": ["locations.json#...","styles.json#..."]
}
"""


def build_user_prompt(request: PlanRequest, context: dict[str, Any], repair_hint: str | None) -> str:
    brief = {
        "city": context["city_name"],
        "date": request.date,
        "available_hours": request.hours,
        "budget_cny": request.budget_cny,
        "party_size": request.party_size,
        "requested_styles": request.styles,
        "preferences": request.preferences,
        "must_include": request.must_include,
        "avoid": request.avoid,
    }
    parts = [
        "CLIENT BRIEF:\n" + json.dumps(brief, ensure_ascii=False, indent=2),
        "KNOWLEDGE BASE:\n" + json.dumps(context, ensure_ascii=False, indent=2),
    ]
    if repair_hint:
        parts.append(
            "A previous draft FAILED deterministic validation. Fix exactly these "
            "issues while keeping the rest of the plan intact:\n" + repair_hint
        )
    parts.append("Return the JSON plan now.")
    return "\n\n".join(parts)


def build_repair_hint(report: Any) -> str:
    lines = []
    for issue in report.issues:
        where = f" (slot {issue.slot_index})" if issue.slot_index is not None else ""
        lines.append(
            f"- [{issue.severity.upper()}] {issue.code}{where}: {issue.message}. "
            f"Expected: {issue.expected}. Fix: {issue.suggestion}"
        )
    return "\n".join(lines)
