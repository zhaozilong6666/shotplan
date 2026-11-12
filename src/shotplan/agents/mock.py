"""Offline, deterministic provider.

Two modes exist on purpose:

* ``good``  - a rule-based planner that respects every hard constraint. This is
  what makes the whole repository runnable with zero credentials.
* ``naive`` - a deliberately flawed planner that ignores time, transfers,
  budget and grounding. It is the regression baseline: if the eval harness
  cannot tell ``naive`` from ``good``, the eval harness is broken.

The naive mode is not a toy. It is the control group that proves the
validator and scorers measure something real.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..domain.constraints import MIN_TRANSFER_COST_CNY, MIN_TRANSFER_MINUTES
from ..domain.models import PlanRequest, fmt_minutes
from ..domain.models import to_minutes as _to_minutes
from ..knowledge.tools import get_tools
from .base import GenerationResult

PHOTOGRAPHY_BASE_CNY = 300
TRANSFER_COST_CNY = MIN_TRANSFER_COST_CNY
EARLIEST_START = 9 * 60
MIN_SHOOT_MINUTES = 60


class MockProvider:
    name = "mock"

    def __init__(self, mode: str = "good", prompt_version: str | None = None) -> None:
        if mode not in ("good", "naive"):
            raise ValueError(f"unknown mock mode: {mode}")
        self.mode = mode
        # Offline providers do not consume prompts, but the version is recorded
        # in every trace so v1/v2 eval runs stay comparable (see ADR 0004).
        self.prompt_version = prompt_version or f"mock-{mode}-v1"

    def generate(
        self,
        request: PlanRequest,
        context: dict[str, Any],
        repair_hint: str | None = None,
    ) -> GenerationResult:
        started = time.perf_counter()
        kb = get_tools()
        req = request.normalized()
        payload = (
            self._naive(req, context)
            if self.mode == "naive"
            else self._good(req, context, kb, repair_hint)
        )
        elapsed = (time.perf_counter() - started) * 1000
        text = json.dumps(payload, ensure_ascii=False)
        return GenerationResult(
            payload=payload,
            tokens_in=len(json.dumps(context, ensure_ascii=False)) // 4,
            tokens_out=len(text) // 4,
            cost_cny=0.0,
            duration_ms=elapsed,
            model=f"mock-{self.mode}",
            metadata={"mode": self.mode, "repair_hint_applied": bool(repair_hint)},
        )

    # ------------------------------------------------------------------ good ---
    def _good(
        self,
        req: PlanRequest,
        context: dict[str, Any],
        kb: Any,
        repair_hint: str | None,
    ) -> dict[str, Any]:
        style = kb.best_style_for(req.styles, req.budget_cny)
        style_total = style["makeup_minutes"] + style["costume_minutes"]
        total_minutes = int(req.hours * 60)

        light_start, light_end = kb.light_window(req.date)
        cursor = max(EARLIEST_START, _to_minutes(light_end) - total_minutes)
        end_limit = cursor + total_minutes

        # Shorten the styling block when the booking is short, and say so.
        makeup_minutes = min(style_total, max(60, int(total_minutes * 0.35)))
        assumptions: list[str] = []
        if req.styles and style["id"] not in req.styles:
            assumptions.append(
                f"预算 {req.budget_cny} 元需预留转场费用，妆造由 "
                f"{req.styles[0]} 降级为{style['name']}"
            )
        if makeup_minutes < style_total:
            assumptions.append(
                f"预约时长仅 {req.hours} 小时，妆造压缩为 {makeup_minutes} 分钟"
                f"（标准 {style_total} 分钟）"
            )

        outdoor = [
            item
            for item in context["candidate_locations"]
            if not item["indoor"] and item["id"] not in req.avoid
        ]
        if not outdoor:
            outdoor = context["indoor_fallback"] or kb.locations(req.city)[:1]

        remaining = total_minutes - makeup_minutes
        count = 2 if remaining >= 2 * (MIN_TRANSFER_MINUTES + MIN_SHOOT_MINUTES) else 1
        golden = [x for x in outdoor if "golden" in x["best_light"]]
        plain = [x for x in outdoor if "golden" not in x["best_light"]]
        # The last shoot block should land in golden light; pick one plain
        # scene first and one golden-friendly scene last whenever possible.
        if count >= 2 and golden:
            chosen_ids = list(dict.fromkeys([(plain or golden)[0]["id"], golden[-1]["id"]]))
            points = [next(x for x in outdoor if x["id"] == pid) for pid in chosen_ids]
        else:
            points = (golden or outdoor)[:count]

        # Budget-aware selection: never keep a location combination whose total
        # cost exceeds what is left after styling and transfer. Eval case C24
        # (budget 400) exposed the old behaviour of picking a paid scenic spot
        # and then failing BUDGET_EXCEEDED.
        styling_cost = int(style["price_cny"][0])
        location_budget = max(0, req.budget_cny - styling_cost - TRANSFER_COST_CNY)
        while points and sum(int(p["cost_cny"]) for p in points) > location_budget:
            priciest = max(range(len(points)), key=lambda i: int(points[i]["cost_cny"]))
            points.pop(priciest)
        if not points:
            free = [x for x in outdoor if int(x["cost_cny"]) == 0]
            points = free[:1] or [outdoor[0]]

        shoot_total = remaining - len(points) * MIN_TRANSFER_MINUTES
        per_shoot = max(30, shoot_total // max(1, len(points)))

        loc_costs = sum(int(p["cost_cny"]) for p in points)
        photography = min(
            PHOTOGRAPHY_BASE_CNY,
            max(0, req.budget_cny - styling_cost - loc_costs - TRANSFER_COST_CNY),
        )
        if photography < PHOTOGRAPHY_BASE_CNY:
            assumptions.append(
                f"预算 {req.budget_cny} 元内采用基础摄影档位（{photography} 元）"
            )

        slots: list[dict[str, Any]] = []
        style_ref = f"styles.json#{style['id']}"
        # Makeup happens at the current city's indoor fallback, not a hard-coded id.
        makeup_location_id = (context["indoor_fallback"] or [{}])[0].get(
            "id", "indoor_studio"
        )

        def push(
            start: int,
            end: int,
            activity: str,
            location_id: str,
            label: str,
            cost: int,
            refs: list[str],
            notes: str,
        ) -> None:
            slots.append(
                {
                    "start": fmt_minutes(start),
                    "end": fmt_minutes(end),
                    "activity": activity,
                    "location_id": location_id,
                    "label": label,
                    "cost_cny": cost,
                    "source_refs": refs,
                    "notes": notes,
                }
            )

        push(
            cursor, cursor + makeup_minutes, "makeup", makeup_location_id,
            f"{style['name']}妆造", styling_cost, [style_ref],
            f"含发型 {style['makeup_minutes']} 分钟、着装 {style['costume_minutes']} 分钟；{style['notes']}",
        )
        cursor += makeup_minutes

        for index, point in enumerate(points):
            ref = f"locations.json#{point['id']}"
            transfer_cost = TRANSFER_COST_CNY if index == 0 else 0
            push(
                cursor, cursor + MIN_TRANSFER_MINUTES, "travel", point["id"],
                f"转场至{point['name']}", transfer_cost, [ref],
                f"按知识库估算至少 {MIN_TRANSFER_MINUTES} 分钟，含补妆与器材搬运",
            )
            cursor += MIN_TRANSFER_MINUTES

            duration = per_shoot
            if index == len(points) - 1:
                duration = max(duration, end_limit - cursor)
                duration = max(30, duration)
            shoot_cost = int(point["cost_cny"]) + (photography if index == len(points) - 1 else 0)
            push(
                cursor, min(cursor + duration, end_limit), "shoot", point["id"],
                f"{point['name']}拍摄", shoot_cost, [ref],
                f"{point['notes']}；建议光线 {light_start}-{light_end}",
            )
            cursor = min(cursor + duration, end_limit)

        fallback = (context["indoor_fallback"] or [{}])[0]
        fallback_id = fallback.get("id", "indoor_studio")
        # Alternatives must point at the CURRENT city, never a hard-coded id.
        quiet = next(
            (
                item
                for item in context["candidate_locations"]
                if item.get("crowd") == "low" and not item["indoor"]
            ),
            None,
        )
        crowd_id = quiet["id"] if quiet else fallback_id
        crowd_name = quiet["name"] if quiet else fallback.get("name", "室内影棚")
        total_cost = (
            styling_cost + photography + loc_costs + TRANSFER_COST_CNY
        )
        refs = [style_ref] + [f"locations.json#{p['id']}" for p in points]
        assumptions.append(f"黄金光线按 {req.date} 所在季节估算为 {light_start}-{light_end}")
        if repair_hint:
            assumptions.append("本次为校验失败后的修复轮次")

        return {
            "slots": slots,
            "styling": {
                "style_id": style["id"],
                "name": style["name"],
                "makeup_minutes": makeup_minutes,
                "costume_minutes": style["costume_minutes"],
            },
            "total_cost_cny": total_cost,
            "alternatives": [
                {
                    "trigger": "rain",
                    "description": f"遇雨或强风改至{fallback.get('name', '室内影棚')}，光线可控",
                    "location_id": fallback_id,
                },
                {
                    "trigger": "crowd",
                    "description": f"客流超预期时改去{crowd_name}，客流低且更从容",
                    "location_id": crowd_id,
                },
            ],
            "assumptions": assumptions,
            "source_refs": refs,
        }

    # ----------------------------------------------------------------- naive ---
    def _naive(self, req: PlanRequest, context: dict[str, Any]) -> dict[str, Any]:
        """A plausible-looking but unexecutable plan. Used as eval baseline."""
        candidates = context["candidate_locations"] or [{"id": "city_wall"}]
        first = candidates[0]["id"]
        return {
            "slots": [
                {"start": "08:00", "end": "08:30", "activity": "makeup",
                 "location_id": "indoor_studio", "label": "快速妆造", "cost_cny": 0, "notes": ""},
                {"start": "08:30", "end": "09:00", "activity": "shoot",
                 "location_id": first, "label": "第一组", "cost_cny": 0, "notes": ""},
                {"start": "09:00", "end": "10:00", "activity": "shoot",
                 "location_id": "big_wild_goose_pagoda", "label": "第二组", "cost_cny": 0, "notes": ""},
                {"start": "10:00", "end": "11:00", "activity": "shoot",
                 "location_id": "nonexistent_palace", "label": "第三组", "cost_cny": 0, "notes": ""},
                {"start": "11:00", "end": "19:00", "activity": "shoot",
                 "location_id": "datang_furong_garden", "label": "第四组", "cost_cny": 0, "notes": ""},
            ],
            "styling": {"style_id": "tang", "name": "唐制", "makeup_minutes": 30},
            "total_cost_cny": req.budget_cny * 2,
            "alternatives": [],
            "assumptions": [],
            "source_refs": [],
        }
