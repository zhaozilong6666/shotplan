import json

import httpx
from fastapi.testclient import TestClient

from shotplan import writer_service
from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan
from shotplan.remote import RemoteRunSink
from shotplan.storage import RunStore


def _result():
    return run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)


def test_writer_service_saves_a_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "writer.db"))
    monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
    monkeypatch.setattr(writer_service, "_STORE", None)
    result = _result()
    row = RunStore.row_for(result)
    client = TestClient(writer_service.app)
    assert client.get("/health").json() == {"status": "ok"}
    response = client.post("/save", json={"rows": [list(row)]})
    assert response.status_code == 200
    assert response.json() == {"saved": 1}
    assert client.get("/count").json() == {"total_runs": 1}
    stored = RunStore(tmp_path / "writer.db").get(result.trace.run_id)
    assert stored is not None and stored["trace"]["run_id"] == result.trace.run_id


def test_writer_service_rejects_wrong_row_width(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "writer.db"))
    monkeypatch.setattr(writer_service, "_STORE", None)
    client = TestClient(writer_service.app)
    response = client.post("/save", json={"rows": [[1, 2, 3]]})
    assert response.status_code == 422


def test_remote_sink_posts_the_local_row_shape():
    result = _result()
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"saved": 1})

    sink = RemoteRunSink("http://writer.test", transport=httpx.MockTransport(handler))
    assert sink.save(result) == result.trace.run_id
    assert seen["path"] == "/save"
    payload = seen["json"]
    assert isinstance(payload, dict)
    rows = payload["rows"]
    assert len(rows) == 1 and len(rows[0]) == 12
    assert rows[0][0] == result.trace.run_id


def test_remote_sink_maps_transport_errors_to_storage_error():
    result = _result()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no writer", request=request)

    sink = RemoteRunSink("http://writer.test", transport=httpx.MockTransport(handler))
    try:
        sink.save(result)
    except Exception as exc:  # noqa: BLE001 - assert the public mapping
        assert exc.__class__.__name__ == "StorageError"
    else:  # pragma: no cover - failure path
        raise AssertionError("expected StorageError")
