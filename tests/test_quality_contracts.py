"""Contracts that keep the new observability/writer paths honest.

These do not repeat the happy-path API tests: they pin the headers and error
mappings that the multi-worker investigation depends on.
"""

from contextlib import contextmanager

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from shotplan import writer_service
from shotplan.agents import MockProvider
from shotplan.api import app
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan
from shotplan.remote import RemoteRunSink
from shotplan.storage import RunStore, StorageError


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_for(tmp_path, monkeypatch):
    @contextmanager
    def _open(name: str, **env: str):
        monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / f"{name}.db"))
        monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        with TestClient(app) as test_client:
            yield test_client

    return _open


def _result():
    return run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)


def test_plan_response_exposes_timing_headers(client):
    response = client.post(
        "/api/plan",
        json={"hours": 6, "budget_cny": 1500, "mock_mode": "good"},
    )
    assert response.status_code == 200
    for name in (
        "X-Pipeline-Duration-Ms",
        "X-Persist-Duration-Ms",
        "X-Queue-Duration-Ms",
        "X-Sink-Setup-Ms",
        "X-Body-Duration-Ms",
        "X-Post-Duration-Ms",
    ):
        value = response.headers[name]
        assert float(value) >= 0


def test_config_reports_remote_writer(client_for):
    with client_for(
        "remote",
        SHOTPLAN_WRITER_URL="http://127.0.0.1:9",
        SHOTPLAN_PERSIST_MODE="batch",
    ) as client:
        writer = client.get("/api/config").json()["writer"]
        assert writer == {"mode": "remote", "url": "http://127.0.0.1:9"}


def test_writer_service_persists_a_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "writer.db"))
    monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
    monkeypatch.setattr(writer_service, "_STORE", None)
    rows = [list(RunStore.row_for(_result())) for _ in range(3)]
    client = TestClient(writer_service.app)
    response = client.post("/save", json={"rows": rows})
    assert response.status_code == 200
    assert response.json() == {"saved": 3}
    assert client.get("/count").json() == {"total_runs": 3}


def test_remote_sink_rejects_non_200():
    result = _result()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="writer busy")

    sink = RemoteRunSink("http://writer.test", transport=httpx.MockTransport(handler))
    with pytest.raises(StorageError, match="writer busy"):
        sink.save(result)


def test_save_rows_is_public_and_counts(tmp_path):
    store = RunStore(tmp_path / "rows.db")
    rows = [RunStore.row_for(_result()) for _ in range(2)]
    assert store.save_rows(rows) == 2
    assert store.count() == 2
