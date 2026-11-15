"""The product must expose every city the knowledge base can plan for."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from shotplan.api import app
from shotplan.knowledge.loader import knowledge_version
from shotplan.knowledge.tools import get_tools

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_matches_the_knowledge_base_exactly():
    with TestClient(app) as client:
        response = client.get("/api/catalog")
    assert response.status_code == 200
    data = response.json()

    tools = get_tools()
    assert [c["id"] for c in data["cities"]] == tools.city_ids()
    assert [s["id"] for s in data["styles"]] == [s["id"] for s in tools.styles()]
    assert data["city_count"] == len(tools.city_ids())
    assert data["location_count"] == sum(
        len(tools.locations(city_id)) for city_id in tools.city_ids()
    )
    assert data["knowledge_version"] == knowledge_version()

    for city in data["cities"]:
        assert city["name"] and city["center"]
        assert city["location_count"] > 0
        assert city["indoor_count"] >= 1, f"{city['id']} needs a rainy-day fallback"
        assert city["source_freshness"]
        assert city["tags"]


def test_every_catalog_city_is_actually_plannable():
    """A dropdown entry that produces no plan is worse than no entry."""
    with TestClient(app) as client:
        cities = [c["id"] for c in client.get("/api/catalog").json()["cities"]]
        for city_id in cities:
            response = client.post("/api/plan", json={
                "city": city_id, "date": "2026-10-04", "hours": 6,
                "budget_cny": 1500, "styles": ["tang"],
            })
            assert response.status_code == 200, (city_id, response.text[:200])
            body = response.json()
            assert body["status"] == "ok", (city_id, body["status"])
            assert body["plan"]["slots"], city_id


def test_frontend_is_driven_by_the_catalog_not_a_hardcoded_list():
    app_js = (ROOT / "src/shotplan/web/app.js").read_text(encoding="utf-8")
    html = (ROOT / "src/shotplan/web/index.html").read_text(encoding="utf-8")
    assert "/api/catalog" in app_js
    assert "loadCatalog()" in app_js
    assert 'id="catalog-note"' in html and 'id="city-note"' in html
    # Only one offline fallback option may remain in the markup.
    assert html.count("<option value=\"xian\"") == 1
