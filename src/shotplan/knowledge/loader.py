"""Loads and validates the curated knowledge base.

Validation at load time is deliberate: a malformed knowledge entry would
otherwise surface much later as a mysterious validation failure.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..domain.errors import KnowledgeError

DATA_DIR = Path(__file__).resolve().parent / "data"

_REQUIRED_LOCATION = {
    "id", "source", "last_verified_at", "name", "district", "tags",
    "best_light", "indoor", "min_minutes", "travel_min_from_center",
    "cost_cny", "crowd", "notes",
}
_REQUIRED_STYLE = {
    "id", "source", "last_verified_at", "name", "era", "makeup_minutes",
    "costume_minutes", "price_cny", "suitable_tags", "avoid_tags", "notes",
}


def _check_provenance(item: dict[str, Any], kind: str) -> None:
    """Every entry must say where it came from and when it was last reviewed.

    Prices and opening facts age silently; without a date nobody can tell a
    curated entry from a guess. `last_verified_at` is the review date in this
    repository, not a live booking-site confirmation (see the file's
    provenance note).
    """
    if not str(item.get("source", "")).strip():
        raise KnowledgeError(f"{kind} {item.get('id')} has an empty source")
    try:
        date.fromisoformat(str(item["last_verified_at"]))
    except ValueError as exc:
        raise KnowledgeError(
            f"{kind} {item.get('id')} has an invalid last_verified_at: "
            f"{item['last_verified_at']!r}"
        ) from exc


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise KnowledgeError(f"knowledge file missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KnowledgeError(f"invalid JSON in {path.name}: {exc}") from exc


@lru_cache(maxsize=1)
def load_locations(path: Path | None = None) -> dict[str, Any]:
    data = _read_json(path or DATA_DIR / "locations.json")
    cities = data.get("cities")
    if not isinstance(cities, dict) or not cities:
        raise KnowledgeError("locations.json must contain a non-empty 'cities' object")
    for city_id, city in cities.items():
        locations = city.get("locations")
        if not isinstance(locations, list) or not locations:
            raise KnowledgeError(f"city {city_id} has no locations")
        seen: set[str] = set()
        for item in locations:
            missing = _REQUIRED_LOCATION - set(item)
            if missing:
                raise KnowledgeError(f"location in {city_id} missing fields: {sorted(missing)}")
            if item["id"] in seen:
                raise KnowledgeError(f"duplicate location id in {city_id}: {item['id']}")
            seen.add(item["id"])
            if item["min_minutes"] <= 0 or item["cost_cny"] < 0:
                raise KnowledgeError(f"invalid numbers for location {item['id']}")
            _check_provenance(item, "location")
    return data


@lru_cache(maxsize=1)
def load_styles(path: Path | None = None) -> dict[str, Any]:
    data = _read_json(path or DATA_DIR / "styles.json")
    styles = data.get("styles")
    if not isinstance(styles, list) or not styles:
        raise KnowledgeError("styles.json must contain a non-empty 'styles' list")
    seen: set[str] = set()
    for item in styles:
        missing = _REQUIRED_STYLE - set(item)
        if missing:
            raise KnowledgeError(f"style missing fields: {sorted(missing)}")
        if item["id"] in seen:
            raise KnowledgeError(f"duplicate style id: {item['id']}")
        seen.add(item["id"])
        price = item["price_cny"]
        if not isinstance(price, list) or len(price) != 2 or price[0] > price[1]:
            raise KnowledgeError(f"invalid price_cny for style {item['id']}")
        _check_provenance(item, "style")
    return data


@lru_cache(maxsize=1)
def knowledge_version() -> str:
    """Content hash of the curated knowledge files, recorded in every trace.

    A score is only reproducible if the data behind it is identifiable. The
    hash changes whenever a price, tag or location changes, which is exactly
    when historical runs stop being directly comparable.
    """
    digest = hashlib.sha256()
    for name in ("locations.json", "styles.json"):
        digest.update((DATA_DIR / name).read_bytes())
    return digest.hexdigest()[:12]
