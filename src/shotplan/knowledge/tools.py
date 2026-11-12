"""Deterministic tools exposed to the planning agent.

This is the retrieval layer. It is deliberately keyword/exact-match rather than
vector search: the knowledge base is tiny, constraints must be matched exactly,
and every retrieved item has to be citable in the final plan. See docs/adr/0003.
"""

from __future__ import annotations

from typing import Any

from ..domain.constraints import (
    MIN_TRANSFER_COST_CNY,
    MIN_TRANSFER_MINUTES,
    golden_window,
)
from ..domain.errors import KnowledgeError
from ..domain.models import PlanRequest, fmt_minutes
from .loader import load_locations, load_styles


class KnowledgeTools:
    def __init__(self) -> None:
        self._locations = load_locations()
        self._styles = load_styles()

    def city_ids(self) -> list[str]:
        return sorted(self._locations["cities"])

    def city(self, city_id: str) -> dict[str, Any]:
        try:
            return self._locations["cities"][city_id]
        except KeyError as exc:
            raise KnowledgeError(
                f"unknown city {city_id!r}; available: {', '.join(self.city_ids())}"
            ) from exc

    def locations(self, city_id: str) -> list[dict[str, Any]]:
        return list(self.city(city_id)["locations"])

    def location(self, city_id: str, location_id: str) -> dict[str, Any] | None:
        return next(
            (x for x in self.locations(city_id) if x["id"] == location_id), None
        )

    def location_ids(self, city_id: str) -> set[str]:
        return {x["id"] for x in self.locations(city_id)}

    def styles(self) -> list[dict[str, Any]]:
        return list(self._styles["styles"])

    def style(self, style_id: str) -> dict[str, Any] | None:
        return next((s for s in self.styles() if s["id"] == style_id), None)

    def style_ids(self) -> set[str]:
        return {s["id"] for s in self.styles()}

    def search_locations(
        self,
        city_id: str,
        tags: list[str] | None = None,
        exclude: list[str] | None = None,
        indoor: bool | None = None,
        max_cost: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        wanted = set(tags or [])
        blocked = set(exclude or [])
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in self.locations(city_id):
            item_tags = set(item["tags"])
            if item["id"] in blocked:
                continue
            if indoor is not None and bool(item["indoor"]) != indoor:
                continue
            if max_cost is not None and item["cost_cny"] > max_cost:
                continue
            overlap = len(wanted & item_tags)
            penalty = len(blocked & item_tags) * 2
            # Prefer low-crowd, low-weather-risk options when relevance ties.
            bonus = (1 if item.get("crowd") == "low" else 0) + (
                1 if item.get("weather_risk") == "low" else 0
            )
            scored.append((overlap * 10 + bonus - penalty, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["cost_cny"], pair[1]["id"]))
        return [item for _, item in scored[:limit]]

    def estimate_transfer(self, city_id: str, from_id: str, to_id: str) -> int:
        """Minutes needed to move between two locations, including a buffer."""
        if from_id == to_id:
            return 0
        a, b = self.location(city_id, from_id), self.location(city_id, to_id)
        if a is None or b is None:
            raise KnowledgeError(f"cannot estimate transfer for unknown location: {from_id} -> {to_id}")
        if a["district"] == b["district"]:
            base = 15
        else:
            base = abs(a["travel_min_from_center"] - b["travel_min_from_center"]) + 20
        return max(MIN_TRANSFER_MINUTES, base)

    def light_window(self, date_str: str) -> tuple[str, str]:
        try:
            month = int(date_str.split("-")[1])
        except (IndexError, ValueError) as exc:
            raise KnowledgeError(f"cannot parse month from date {date_str!r}") from exc
        start, end = golden_window(month)
        return fmt_minutes(start * 60), fmt_minutes(end * 60)

    def best_style_for(self, requested: list[str], budget_cny: int) -> dict[str, Any]:
        # Reserve the transfer fee first: a styling package that consumes the
        # whole budget makes even a single-location plan unexecutable.
        usable = max(0, budget_cny - MIN_TRANSFER_COST_CNY)
        for style_id in requested:
            style = self.style(style_id)
            if style and style["price_cny"][0] <= usable:
                return style
        # Budget too tight for the requested styles: fall back to the cheapest one.
        cheapest = min(self.styles(), key=lambda s: s["price_cny"][0])
        if cheapest["price_cny"][0] > usable:
            raise KnowledgeError(
                f"budget {budget_cny} cannot cover the cheapest styling plus transfer "
                f"({cheapest['price_cny'][0]} + {MIN_TRANSFER_COST_CNY})"
            )
        return cheapest

    def context_for(self, request: PlanRequest) -> dict[str, Any]:
        """Compact, citable context handed to the model provider."""
        city_id = request.normalized().city
        styles = [s for sid in request.styles if (s := self.style(sid))]
        wanted_tags: list[str] = []
        for style in styles:
            wanted_tags.extend(style["suitable_tags"])
        candidates = self.search_locations(
            city_id,
            tags=list(dict.fromkeys(wanted_tags)),
            exclude=request.avoid,
            max_cost=max(0, request.budget_cny // 3),
            limit=6,
        )
        fallback = self.search_locations(city_id, indoor=True, limit=1)
        light_start, light_end = self.light_window(request.date)
        return {
            "city": city_id,
            "city_name": self.city(city_id)["name"],
            "date": request.date,
            "available_hours": request.hours,
            "budget_cny": request.budget_cny,
            "party_size": request.party_size,
            "requested_styles": [s["id"] for s in styles],
            "style_details": styles,
            "candidate_locations": candidates,
            "indoor_fallback": fallback,
            "golden_light": {"start": light_start, "end": light_end},
            "min_transfer_minutes": MIN_TRANSFER_MINUTES,
            "preferences": request.preferences,
            "must_include": request.must_include,
            "avoid": request.avoid,
        }


_TOOLS: KnowledgeTools | None = None


def get_tools() -> KnowledgeTools:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = KnowledgeTools()
    return _TOOLS
