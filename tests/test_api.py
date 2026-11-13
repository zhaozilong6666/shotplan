import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from shotplan.api import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_rules_endpoint(client):
    body = client.get("/api/rules").json()
    assert body["count"] == len(body["rules"]) > 8


def test_plan_endpoint_good(client):
    response = client.post("/api/plan", json={"hours": 6, "budget_cny": 1500,
                                              "styles": ["tang"], "mock_mode": "good"})
    assert response.status_code == 200
    data = response.json()
    assert data["validation"]["hard_pass"] is True
    assert data["plan"]["slots"]
    assert data["trace"]["events"]


def test_plan_endpoint_naive_exposes_failures(client):
    response = client.post("/api/plan", json={"hours": 6, "budget_cny": 1500,
                                              "mock_mode": "naive"})
    assert response.status_code == 200
    assert response.json()["validation"]["hard_pass"] is False


def test_plan_endpoint_rejects_bad_input(client):
    assert client.post("/api/plan", json="not-an-object").status_code == 422


def test_plan_endpoint_survives_junk_fields(client):
    """Wrong types must degrade to a structured answer, never a 500."""
    response = client.post(
        "/api/plan",
        json={"hours": "abc", "budget_cny": None, "styles": "tang",
              "party_size": [1], "date": "2026-02-30", "mock_mode": "good"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in {"ok", "infeasible"}
    assert data["request"]["styles"] == ["tang"]
    assert 1.0 <= data["request"]["hours"] <= 12.0


def test_run_history_and_detail(client):
    created = client.post("/api/plan", json={"mock_mode": "good"}).json()
    run_id = created["trace"]["run_id"]
    assert client.get("/api/runs").json()[0]["run_id"] == run_id
    assert client.get(f"/api/runs/{run_id}").json()["trace"]["run_id"] == run_id
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/summary").json()["total_runs"] == 1


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "shotplan" in response.text


def test_stats_endpoint_reports_provider_groups(client):
    client.post("/api/plan", json={"mock_mode": "good"})
    client.post("/api/plan", json={"hours": 1, "budget_cny": 0, "mock_mode": "good"})
    data = client.get("/api/stats").json()
    assert data["total_runs"] == 2
    assert data["by_status"]["infeasible"] == 1
    assert data["groups"]
    assert "p95_duration_ms" in data["overall"]


def test_plan_endpoint_reports_infeasible_brief(client):
    response = client.post("/api/plan", json={"hours": 1, "budget_cny": 0,
                                              "mock_mode": "good"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "infeasible"
    assert data["feasibility"]["feasible"] is False
    assert data["plan"]["slots"] == []
    assert data["validation"]["issues"][0]["code"] == "INFEASIBLE"


def test_async_persist_mode_flushes_on_shutdown(tmp_path, monkeypatch):
    """The API hands the row to the writer and the lifespan drains it."""
    from shotplan.storage import RunStore

    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "async.db"))
    monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
    monkeypatch.setenv("SHOTPLAN_PERSIST_MODE", "async")

    with TestClient(app) as client:
        for _ in range(3):
            response = client.post("/api/plan", json={"mock_mode": "good"})
            assert response.status_code == 200
        body = client.get("/api/config").json()
        assert body["persist_mode"] == "async"
        assert body["writer"]["submitted"] == 3
        assert body["writer"]["sync_fallbacks"] == 0

    # After the context manager exits, lifespan has drained the queue.
    assert RunStore(tmp_path / "async.db").summary()["total_runs"] == 3


def test_off_persist_mode_stores_nothing(tmp_path, monkeypatch):
    from shotplan.storage import RunStore

    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "off.db"))
    monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
    monkeypatch.setenv("SHOTPLAN_PERSIST_MODE", "off")

    with TestClient(app) as client:
        assert client.post("/api/plan", json={"mock_mode": "good"}).status_code == 200

    assert RunStore(tmp_path / "off.db").summary()["total_runs"] == 0
