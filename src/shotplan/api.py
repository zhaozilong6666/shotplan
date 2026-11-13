"""Optional FastAPI layer.

Import this module only when the `api` extra is installed:
    python -m pip install -e ".[api]"
The core engine has no dependency on this file.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.concurrency import run_in_threadpool
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "FastAPI is an optional dependency. Install with: "
        "python -m pip install -e '.[api]'"
    ) from exc

from . import __version__
from .agents import MockProvider, build_provider
from .config import ROOT, Settings, get_settings
from .domain.constraints import RULES
from .domain.errors import ShotplanError
from .domain.models import PlanRequest
from .pipeline import run_plan
from .storage import RunSink, RunStore, close_all_writers, get_writer


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Drain the write-behind queue on shutdown.

    Without this, SIGTERM would stop the process with rows still queued, which
    is exactly the failure mode that makes people distrust background writes.
    """
    try:
        yield
    finally:
        close_all_writers(timeout=10.0)


app = FastAPI(
    title="shotplan",
    version=__version__,
    description="可评测、带硬约束校验的旅拍方案 Agent",
    lifespan=lifespan,
)
WEB_DIR = ROOT / "web"


def _store() -> RunStore:
    return RunStore(get_settings().db_path)


def _sink(cfg: Settings) -> RunSink | None:
    """Where a finished run goes: nowhere, disk now, or the write-behind queue."""
    if cfg.persist_mode == "off":
        return None
    store = _store()
    return get_writer(store) if cfg.persist_mode == "async" else store


@app.get("/api/health")
def health() -> dict[str, Any]:
    cfg = get_settings()
    return {"status": "ok", "provider": cfg.provider, "version": __version__}


@app.get("/api/config")
def config() -> dict[str, Any]:
    cfg = get_settings()
    payload = cfg.masked()
    if cfg.persist_mode == "async":
        # Make the queue observable: pending rows are the honest answer to
        # "did my write land?", and sync_fallbacks means it is saturated.
        payload["writer"] = get_writer(_store()).stats.to_dict()
    return payload


@app.get("/api/rules")
def rules() -> dict[str, Any]:
    return {"count": len(RULES), "rules": RULES}


@app.post("/api/plan")
async def create_plan(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        brief = PlanRequest.from_dict(body)
        mode = body.pop("mock_mode", None)
        provider = (
            MockProvider(mode=mode)
            if mode in ("good", "naive")
            else build_provider()
        )
        cfg = get_settings()
        sink = _sink(cfg)
        # The pipeline is synchronous CPU work (and, with a real provider, blocking
        # network I/O). Calling it directly from an async endpoint would block the
        # event loop and serialise every request. Hand it to the threadpool.
        result = await run_in_threadpool(
            lambda: run_plan(
                brief,
                provider=provider,
                store=sink,
                settings=cfg,
                max_repairs=cfg.max_repairs,
            )
        )
        return JSONResponse(result.to_dict())
    except (ShotplanError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/runs")
def runs(limit: int = 20) -> list[dict[str, Any]]:
    return _store().recent(min(max(limit, 1), 100))


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    found = _store().get(run_id)
    if found is None:
        raise HTTPException(status_code=404, detail="run not found")
    return found


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    return _store().summary()


@app.get("/api/stats")
def stats(limit: int = 200) -> dict[str, Any]:
    from .observability import build_dashboard

    return build_dashboard(_store(), min(max(limit, 1), 1000))


@app.get("/")
def index() -> FileResponse:
    page = WEB_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="web/index.html missing")
    return FileResponse(page)


@app.get("/{asset_name}")
def asset(asset_name: str) -> FileResponse:
    allowed = {"app.js", "styles.css"}
    if asset_name not in allowed:
        raise HTTPException(status_code=404, detail="not found")
    target = WEB_DIR / asset_name
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target)
