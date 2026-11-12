"""Optional FastAPI layer.

Import this module only when the `api` extra is installed:
    python -m pip install -e ".[api]"
The core engine has no dependency on this file.
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "FastAPI is an optional dependency. Install with: "
        "python -m pip install -e '.[api]'"
    ) from exc

from .agents import MockProvider, build_provider
from .config import ROOT, get_settings
from .domain.constraints import RULES
from .domain.errors import ShotplanError
from .domain.models import PlanRequest
from .pipeline import run_plan
from .storage import RunStore

app = FastAPI(
    title="shotplan",
    version="0.1.0",
    description="可评测、带硬约束校验的旅拍方案 Agent",
)
WEB_DIR = ROOT / "web"


def _store() -> RunStore:
    return RunStore(get_settings().db_path)


@app.get("/api/health")
def health() -> dict[str, Any]:
    cfg = get_settings()
    return {"status": "ok", "provider": cfg.provider, "version": "0.1.0"}


@app.get("/api/config")
def config() -> dict[str, Any]:
    return get_settings().masked()


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
        result = run_plan(
            brief,
            provider=provider,
            store=_store(),
            settings=cfg,
            max_repairs=cfg.max_repairs,
        )
        return JSONResponse(result.to_dict())
    except (ShotplanError, ValueError) as exc:
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
