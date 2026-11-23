import logging
from contextlib import contextmanager

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


@pytest.fixture
def client_for(tmp_path, monkeypatch):
    """Open a client with env applied *before* startup.

    Settings are resolved once in the lifespan hook, so a test that changes an
    env var after the client is running would otherwise assert against a stale
    snapshot (and previously passed by accident).
    """

    @contextmanager
    def _open(name: str, **env: str):
        monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / f"{name}.db"))
        monkeypatch.setenv("SHOTPLAN_PROVIDER", env.pop("SHOTPLAN_PROVIDER", "mock"))
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        with TestClient(app) as test_client:
            yield test_client

    return _open


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
    assert data["explanations"]
    assert any(
        "风格匹配" in reason or "黄金窗口" in reason
        for entry in data["explanations"]
        for reason in entry["reasons"]
    )


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


def test_provider_failure_maps_to_bad_gateway(monkeypatch, client):
    """An upstream/model failure is not a client input error."""
    from shotplan.domain.errors import ProviderError

    def broken(cfg):
        raise ProviderError("upstream down")

    monkeypatch.setattr("shotplan.api.build_provider", broken)
    response = client.post("/api/plan", json={})
    assert response.status_code == 502


def test_storage_outage_does_not_lose_the_plan(monkeypatch, client):
    """A write failure is reported, but the caller still gets the plan."""
    from shotplan.domain.errors import StorageError

    class FailingStore:
        def save(self, result):
            raise StorageError("disk full")

    monkeypatch.setattr("shotplan.api._sink", lambda cfg: FailingStore())
    response = client.post("/api/plan", json={"mock_mode": "good"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["validation"]["hard_pass"] is True
    assert "PERSISTENCE_FAILED" in {issue["code"] for issue in data["validation"]["issues"]}


def test_run_history_storage_error_maps_to_503(monkeypatch, client):
    from shotplan.domain.errors import StorageError

    def broken_store(_cfg):
        raise StorageError("db unavailable")

    monkeypatch.setattr("shotplan.api._store", broken_store)
    response = client.get("/api/runs")
    assert response.status_code == 503
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_api_key_protects_plan_and_history(client_for):
    with client_for("key", SHOTPLAN_API_KEY="secret") as client:
        assert client.post("/api/plan", json={"mock_mode": "good"}).status_code == 401
        assert client.get("/api/runs").status_code == 401
        ok = client.post(
            "/api/plan", json={"mock_mode": "good"}, headers={"X-API-Key": "secret"}
        )
        assert ok.status_code == 200
        assert client.get("/api/runs", headers={"X-API-Key": "secret"}).status_code == 200


def test_api_key_also_protects_aggregate_endpoints(client_for):
    """`/api/summary` and `/api/stats` expose run history in aggregate.

    They are read-only, but leaving them open while `/api/runs` needs a key is
    an inconsistent boundary: an anonymous caller could still learn volumes,
    scores and provider names. Same rule, same guard.
    """
    with client_for("key-agg", SHOTPLAN_API_KEY="secret") as client:
        assert client.get("/api/summary").status_code == 401
        assert client.get("/api/stats").status_code == 401
        headers = {"X-API-Key": "secret"}
        assert client.get("/api/summary", headers=headers).status_code == 200
        assert client.get("/api/stats", headers=headers).status_code == 200


def test_request_body_limit_is_enforced(client_for):
    with client_for("body", SHOTPLAN_MAX_BODY_BYTES="1024") as client:
        huge = {"city": "x" * 2000}
        assert client.post("/api/plan", json=huge).status_code == 413


def test_new_json_endpoints_enforce_the_same_body_limit(client_for):
    """A body cap that only protects /api/plan is not a body cap."""
    with client_for("body-new", SHOTPLAN_MAX_BODY_BYTES="1024") as client:
        assert client.post(
            "/api/compare", json={"request": {"city": "x" * 2000}}
        ).status_code == 413
        assert client.post(
            "/api/understand", json={"text": "x" * 2000}
        ).status_code == 413


def test_plan_rate_limit_is_enforced(client_for):
    from shotplan.api import _RATE_BUCKETS

    _RATE_BUCKETS.clear()
    with client_for("rate", SHOTPLAN_RATE_LIMIT_PER_MINUTE="1") as client:
        assert client.post("/api/plan", json={"mock_mode": "good"}).status_code == 200
        blocked = client.post("/api/plan", json={"mock_mode": "good"})
        assert blocked.status_code == 429
        assert blocked.json()["request_id"] == blocked.headers["x-request-id"]


def test_rate_bucket_map_stays_bounded():
    """A per-client bucket must not accumulate forever on a public demo."""
    from shotplan.api import _RATE_BUCKET_SOFT_CAP, _RATE_BUCKETS, _purge_idle_rate_buckets

    _RATE_BUCKETS.clear()
    try:
        for index in range(_RATE_BUCKET_SOFT_CAP):
            _RATE_BUCKETS[f"idle-{index}"] = [0.0]
        _RATE_BUCKETS["active"] = [10_000.0]
        _purge_idle_rate_buckets(now=10_000.0)
        assert set(_RATE_BUCKETS) == {"active"}
    finally:
        _RATE_BUCKETS.clear()


def test_mock_mode_cannot_override_a_real_provider(monkeypatch, client_for):
    from shotplan.agents import MockProvider

    monkeypatch.setattr("shotplan.api.build_provider", lambda cfg: MockProvider("naive"))
    with client_for("real", SHOTPLAN_PROVIDER="openai_compatible") as client:
        response = client.post("/api/plan", json={"mock_mode": "good"})
        assert response.status_code == 200
        assert response.json()["validation"]["hard_pass"] is False


def test_request_id_is_echoed_or_generated(client):
    generated = client.get("/api/health")
    assert len(generated.headers["x-request-id"]) == 16

    echoed = client.get("/api/health", headers={"X-Request-ID": "trace.abc-1"})
    assert echoed.headers["x-request-id"] == "trace.abc-1"

    # A value outside the safe alphabet must be replaced, not forwarded.
    forged = client.get("/api/health", headers={"X-Request-ID": "bad id"})
    assert forged.headers["x-request-id"] != "bad id"


def test_access_log_line_carries_the_request_id(client, caplog):
    with caplog.at_level(logging.INFO, logger="shotplan.api"):
        client.get("/api/health", headers={"X-Request-ID": "logged-1"})
    line = next(
        record.getMessage()
        for record in caplog.records
        if "request_id=logged-1" in record.getMessage()
    )
    assert "path=/api/health" in line
    assert "status=200" in line
    assert "duration_ms=" in line


def test_request_id_is_recorded_in_the_run_trace(client):
    response = client.post(
        "/api/plan", json={"mock_mode": "good"}, headers={"X-Request-ID": "trace-42"}
    )
    data = response.json()
    assert data["trace"]["config"]["request_id"] == "trace-42"

    stored = client.get(f"/api/runs/{data['trace']['run_id']}").json()
    assert stored["trace"]["config"]["request_id"] == "trace-42"


def test_retention_prunes_stale_runs_at_startup(tmp_path, monkeypatch):
    import sqlite3

    from shotplan.agents import MockProvider
    from shotplan.domain.models import PlanRequest
    from shotplan.pipeline import run_plan
    from shotplan.storage import RunStore

    path = tmp_path / "retained.db"
    RunStore(path).save(run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE runs SET created_at = '2020-01-01T00:00:00+00:00'")

    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(path))
    monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
    monkeypatch.setenv("SHOTPLAN_RETENTION_DAYS", "30")
    with TestClient(app) as client:
        assert client.get("/api/summary").json()["total_runs"] == 0
        assert client.get("/api/config").json()["retention_days"] == 30


def test_no_cors_headers_for_foreign_origin(client):
    """CORS is a deliberate non-feature; a wildcard would widen a public demo."""
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_batch_mode_persists_before_it_responds(client_for):
    with client_for("batch", SHOTPLAN_PERSIST_MODE="batch") as client:
        response = client.post("/api/plan", json={"mock_mode": "good"})
        assert response.status_code == 200
        assert client.get("/api/summary").json()["total_runs"] == 1


def test_batch_write_failure_is_reported(client_for, monkeypatch):
    from shotplan.domain.errors import StorageError
    from shotplan.storage import RunStore

    def broken(self, results):
        raise StorageError("disk full")

    monkeypatch.setattr(RunStore, "save_many", broken)
    with client_for("batchfail", SHOTPLAN_PERSIST_MODE="batch") as client:
        response = client.post("/api/plan", json={"mock_mode": "good"})
    assert response.status_code == 200
    codes = {issue["code"] for issue in response.json()["validation"]["issues"]}
    assert "PERSISTENCE_FAILED" in codes


def test_run_exports_are_downloadable(client):
    data = client.post("/api/plan", json={"mock_mode": "good"}).json()
    run_id = data["trace"]["run_id"]

    markdown = client.get(f"/api/runs/{run_id}/export.md")
    assert markdown.status_code == 200
    assert "text/markdown" in markdown.headers["content-type"]
    assert "attachment" in markdown.headers["content-disposition"]
    assert "拍摄行程" in markdown.text

    calendar = client.get(f"/api/runs/{run_id}/export.ics")
    assert calendar.status_code == 200
    assert "text/calendar" in calendar.headers["content-type"]
    assert calendar.text.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" in calendar.text

    assert client.get("/api/runs/nope/export.md").status_code == 404
    assert client.get("/api/runs/nope/export.ics").status_code == 404


def test_infeasible_export_explains_the_reasons(client):
    data = client.post(
        "/api/plan", json={"hours": 1, "budget_cny": 0, "mock_mode": "good"}
    ).json()
    assert data["status"] == "infeasible"
    markdown = client.get(f"/api/runs/{data['trace']['run_id']}/export.md")
    assert markdown.status_code == 200
    assert "## 不可行原因" in markdown.text
    assert "建议：" in markdown.text
    calendar = client.get(f"/api/runs/{data['trace']['run_id']}/export.ics")
    assert calendar.status_code == 409, "an empty calendar is not a valid export"
    assert "Markdown" in calendar.json()["detail"]
