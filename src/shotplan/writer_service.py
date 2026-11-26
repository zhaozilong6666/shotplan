"""Single-writer sidecar for SQLite run history.

The API remains multi-worker for request concurrency, but every finished run
is shipped over loopback to this one process, which owns the only writer.
That removes cross-process SQLite write contention without giving up HTTP
workers. The service is deliberately tiny: it has no planning logic and no
provider credentials.

Run it as a separate systemd unit (see ``deploy/shotplan-writer.service``) or
with ``uvicorn shotplan.writer_service:app --workers 1``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shotplan.config import get_settings
from shotplan.storage import RunStore, StorageError, close_all_writers, get_writer


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    close_all_writers()


app = FastAPI(
    title="shotplan writer", docs_url=None, redoc_url=None, lifespan=lifespan
)
_ROW_WIDTH = 12
_STORE: RunStore | None = None


def store() -> RunStore:
    global _STORE
    if _STORE is None:
        _STORE = RunStore(get_settings().db_path)
    return _STORE


class SaveRows(BaseModel):
    rows: list[list[Any]]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/count")
def count() -> dict[str, int]:
    return {"total_runs": store().count()}


@app.post("/save")
def save(payload: SaveRows) -> dict[str, int]:
    rows: list[tuple[Any, ...]] = []
    for index, row in enumerate(payload.rows):
        if len(row) != _ROW_WIDTH:
            raise HTTPException(
                status_code=422,
                detail=f"row {index} has {len(row)} fields, expected {_ROW_WIDTH}",
            )
        rows.append(tuple(row))
    try:
        writer = get_writer(store())
        for row in rows:
            writer.submit_row(row, wait=True)
    except StorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"saved": len(rows)}
