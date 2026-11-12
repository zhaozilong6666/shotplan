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
