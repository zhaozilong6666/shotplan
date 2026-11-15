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
            prompt_version=self.prompt_version,
            metadata={"mode": self.mode, "repair_hint_applied": bool(repair_hint)},
        )

    # ----------------------------------------------------------- point search ---
    @staticmethod
    def _combo_score(
        points: list[dict[str, Any]],
        req: PlanRequest,
        style: dict[str, Any],
        location_budget: int,
    ) -> float:
        """Score a candidate set on the goals a human would actually weigh.

        The previous planner hard-coded "one plain scene + one golden scene".
        That is a guess made once, not a choice made per brief: it cannot trade
        a crowd-free location against a nearer one, or notice that the requested
        style does not fit the tag mix at all.

        Weights are deliberately explicit so they can be argued with and tuned
        by the eval suite rather than buried in `if` branches.
        """
        if not points:
            return -1.0
        style_tags = {tag for tag in req.styles}
        tag_hits = sum(len(set(p["tags"]) & style_tags) for p in points)
        tag_total = max(1, len(points) * max(1, len(style_tags)))
        style_fit = tag_hits / tag_total

        prefs = " ".join(req.preferences)
        wants_quiet = any(w in prefs for w in ("人少", "安静", "小众", "避开人群"))
        quiet_ratio = sum(1 for p in points if p["crowd"] == "low") / len(points)
        crowd_fit = quiet_ratio if wants_quiet else 0.5 + 0.5 * quiet_ratio

        wants_morning = any(w in prefs for w in ("上午", "晨光", "中午前"))
        wants_evening = any(
            w in prefs for w in ("傍晚", "黄金光线", "夕阳", "氛围感", "黄金")
        )
        light_key = "morning" if wants_morning else "golden"
        if wants_morning or wants_evening:
            light_fit = sum(1 for p in points if light_key in p["best_light"]) / len(points)
        else:
            light_fit = sum(1 for p in points if p["best_light"]) / len(points)

        travel_total = sum(int(p["travel_min_from_center"]) for p in points)
        travel_fit = max(0.0, 1.0 - travel_total / 90.0)

        cost_total = sum(int(p["cost_cny"]) for p in points)
        wants_cheap = any(w in prefs for w in ("控制预算", "省钱", "便宜", "性价比", "少花钱"))
        if location_budget <= 0:
            budget_fit = 0.0 if cost_total > 0 else 1.0
        elif wants_cheap:
            # Cheap is the goal: spending less is strictly better, free is best.
            budget_fit = max(0.0, 1.0 - cost_total / location_budget)
        else:
            # Otherwise free scenes are a feature, not a failure. The earlier
            # "spend ~70% of the budget" target made the planner prefer paid
            # spots even when the user asked to save money.
            budget_fit = 1.0 if cost_total <= location_budget else 0.0

        distinct_tags = {tag for p in points for tag in p["tags"]}
        diversity = min(1.0, len(distinct_tags) / max(1, len(points) * 2))

        return (
            0.30 * style_fit
            + 0.22 * light_fit
            + 0.18 * crowd_fit
            + 0.14 * travel_fit
            + 0.10 * budget_fit
            + 0.06 * diversity
        )

    def _choose_points(
        self,
        outdoor: list[dict[str, Any]],
        required: list[dict[str, Any]],
        count: int,
        req: PlanRequest,
        location_budget: int,
        style: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Enumerate feasible sets and keep the best-scoring one.

        With 6-8 candidate scenes the search space is tiny (<= 56 sets), so an
        exhaustive scan is both cheap and explainable -- no heuristic to argue
        about, and the evaluator can measure whether the weights are any good.
        Required locations stay in every set: the user asked for them.
        """
        style = style or {"id": (req.styles or ["tang"])[0]}
        required_ids = {item["id"] for item in required}
        required_cost = sum(int(item["cost_cny"]) for item in required)
        pool = [x for x in outdoor if x["id"] not in required_ids]
        need = max(0, count - len(required))

        candidates: list[list[dict[str, Any]]] = []
        if need == 0:
            candidates = [list(required)]
        else:
            from itertools import combinations

            for combo in combinations(pool, need):
                candidates.append([*required, *combo])
        if not candidates:
            candidates = [list(required) or outdoor[:1]]

        # Feasibility first: never propose a set that cannot be paid for.
        affordable = [
            c for c in candidates
            if sum(int(p["cost_cny"]) for p in c) <= location_budget
        ] or [c for c in candidates if required_cost <= location_budget] or candidates

        best = max(
            affordable,
            key=lambda c: (self._combo_score(c, req, style, location_budget), sorted(p["id"] for p in c)),
        )
        # The longest shoot block is the last one, so put the scene that best
        # matches the requested light there.
        wants_morning = any(w in " ".join(req.preferences) for w in ("上午", "晨光", "中午前"))
        light_key = "morning" if wants_morning else "golden"

        def light_rank(item: dict[str, Any]) -> int:
            return 0 if light_key in item["best_light"] else 1

        required_part = [p for p in best if p["id"] in required_ids]
        optional_part = sorted(
            (p for p in best if p["id"] not in required_ids),
            key=lambda p: (light_rank(p), -int(p["cost_cny"])),
        )
        return [*required_part, *optional_part][:count]

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

        # `must_include` is a hard rule in the validator, so the offline planner
        # has to honour it too. Look the locations up in the whole city rather
        # than only in the retrieved candidates: a required place may not match
        # the requested style tags.
        all_locations = [x for x in kb.locations(req.city) if x["id"] not in req.avoid]
        required: list[dict[str, Any]] = []
        for location_id in req.must_include:
            item = next((x for x in all_locations if x["id"] == location_id), None)
            if item is not None and item not in required:
                required.append(item)

        outdoor = [x for x in all_locations if not x["indoor"]]
        if not outdoor:
            outdoor = context["indoor_fallback"] or all_locations[:1]

        remaining = total_minutes - makeup_minutes
        count = 2 if remaining >= 2 * (MIN_TRANSFER_MINUTES + MIN_SHOOT_MINUTES) else 1
        required = required[:count]

        styling_cost = int(style["price_cny"][0])
        location_budget = max(0, req.budget_cny - styling_cost - TRANSFER_COST_CNY)
        points = self._choose_points(
            outdoor, required, count, req, location_budget, style
        )
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
        # Makeup happens at an indoor fallback when one is allowed. If the user
        # excluded the only studio, use the first allowed location instead of
        # silently violating the avoidance list.
        makeup_location_id = (
            (context["indoor_fallback"] or [{}])[0].get("id")
            or (points[0]["id"] if points else None)
            or (context["candidate_locations"] or [{}])[0].get("id", "indoor_studio")
        )
        if context["indoor_fallback"]:
            makeup_note = "室内妆造，避免户外补妆"
        else:
            makeup_note = "室内影棚被排除，妆造安排在首个外景集合点"

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
            f"含发型 {style['makeup_minutes']} 分钟、着装 {style['costume_minutes']} 分钟；"
            f"{style['notes']}；{makeup_note}",
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

        fallback = (context["indoor_fallback"] or points or [{}])[0]
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
        included_required = [
            item["id"] for item in required if any(p["id"] == item["id"] for p in points)
        ]
        if included_required:
            assumptions.append("已按要求包含必去地点：" + ", ".join(included_required))
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
