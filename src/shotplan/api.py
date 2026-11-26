"""Optional FastAPI layer.

Import this module only when the `api` extra is installed:
    python -m pip install -e ".[api]"
The core engine has no dependency on this file.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

try:
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
    from starlette.concurrency import run_in_threadpool
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "FastAPI is an optional dependency. Install with: "
        "python -m pip install -e '.[api]'"
    ) from exc

from . import __version__
from .agents import MockProvider, build_provider
from .brief import understand
from .compare import compare_strategies
from .config import Settings, configure_logging, get_settings
from .domain.constraints import RULES
from .domain.errors import (
    ConfigError,
    DeadlineExceeded,
    KnowledgeError,
    ParseError,
    ProviderError,
    ShotplanError,
    StorageError,
)
from .domain.feasibility import check_feasibility
from .domain.models import PlanRequest
from .followup import apply_followup
from .knowledge.loader import knowledge_version
from .knowledge.tools import get_tools
from .negotiation import headroom, negotiate
from .pipeline import run_plan
from .remote import RemoteRunSink
from .storage import (
    RunSink,
    RunStore,
    WaitingRunWriter,
    close_all_writers,
    get_writer,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Resolve configuration once and drain the write-behind queue on shutdown.

    Reading the environment on every request made "which config produced this
    run?" unanswerable and re-parsed `.env` thousands of times. Settings are
    now pinned at startup; `app.dependency_overrides[app_settings]` still lets
    tests substitute a specific instance.

    Draining matters too: without it SIGTERM would stop the process with rows
    still queued, which is exactly the failure mode that makes people distrust
    background writes.
    """
    settings = get_settings()
    app.state.settings = settings
    configure_logging(settings)
    if settings.retention_days > 0:
        # Optional housekeeping. A failure here must not stop the service: the
        # database being briefly locked is not a reason to refuse every request.
        try:
            removed = _store(settings).prune_older_than(settings.retention_days)
            if removed:
                _ACCESS_LOG.info(
                    "retention removed %s runs older than %s days",
                    removed, settings.retention_days,
                )
        except StorageError as exc:
            _ACCESS_LOG.warning("retention failed: %s", exc)
    try:
        yield
    finally:
        app.state.settings = None
        close_all_writers(timeout=10.0)


app = FastAPI(
    title="shotplan",
    version=__version__,
    description="可评测、带硬约束校验的旅拍方案 Agent",
    lifespan=lifespan,
)
WEB_DIR = Path(__file__).resolve().parent / "web"
_ACCESS_LOG = logging.getLogger("shotplan.api")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# No CORS middleware on purpose. The bundled UI is served from this same
# origin, so a cross-origin policy would only widen the attack surface: a
# wildcard would let any page call a key-less local deployment. A separate
# frontend needs an explicit allow-list here, not `allow_origins=["*"]`.

# Not every ShotplanError is a client mistake. Keeping the mapping here makes
# a storage outage or an upstream provider failure observable in monitoring
# instead of masquerading as "you sent a bad brief" (422).
_HTTP_STATUS: tuple[tuple[type[ShotplanError], int], ...] = (
    (DeadlineExceeded, 504),
    (ProviderError, 502),
    (StorageError, 503),
    (ConfigError, 500),
    (KnowledgeError, 500),
    (ParseError, 422),
)


def _status_for(exc: ShotplanError) -> int:
    for error_type, status in _HTTP_STATUS:
        if isinstance(exc, error_type):
            return status
    return 500


def _cached_settings(request: Request) -> Settings:
    cached = getattr(request.app.state, "settings", None)
    return cached if isinstance(cached, Settings) else get_settings()


def app_settings(request: Request) -> Settings:
    """FastAPI dependency: the startup snapshot, or env when there is none.

    The fallback keeps direct calls and tests working without a lifespan.
    """
    return _cached_settings(request)


def _store(cfg: Settings) -> RunStore:
    return RunStore(cfg.db_path)


# Annotated keeps the dependency out of argument defaults (ruff B008) and is
# the current FastAPI idiom.
SettingsDep = Annotated[Settings, Depends(app_settings)]


async def enforce_body_limit(request: Request, cfg: SettingsDep) -> None:
    """Cap request bodies on the JSON endpoints that skip /api/plan's parser.

    /api/plan reads the raw body and checks it before json.loads; the newer
    endpoints let FastAPI parse the body first. Without this dependency the
    public deployment promise "request size is capped" would only hold on one
    endpoint. `request.body()` caches, so the later model parsing still works.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cfg.max_body_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"request body exceeds {cfg.max_body_bytes} bytes",
        )
    body = await request.body()
    if len(body) > cfg.max_body_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"request body exceeds {cfg.max_body_bytes} bytes",
        )


def require_api_key(request: Request, cfg: SettingsDep) -> None:
    """Optional bearer check: off by default, on as soon as a key is configured."""
    if cfg.api_key and request.headers.get("x-api-key", "") != cfg.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: dict[str, list[float]] = {}
# Endpoints whose cost is measured in model calls, and how many slots each costs.
_COSTLY_PATHS: dict[str, int] = {"/api/plan": 1, "/api/compare": 3}
_RATE_BUCKET_SOFT_CAP = 4096


def _purge_idle_rate_buckets(now: float) -> None:
    """Bound memory: a per-client bucket would otherwise live forever.

    Only runs once the map grows past the soft cap, because the common case
    (one client, a handful of buckets) should not pay for a scan.
    """
    if len(_RATE_BUCKETS) < _RATE_BUCKET_SOFT_CAP:
        return
    for client, stamps in list(_RATE_BUCKETS.items()):
        if not stamps or now - stamps[-1] >= 60:
            _RATE_BUCKETS.pop(client, None)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """Best-effort per-process limit for the endpoints that cost model calls.

    It is deliberately in-memory: the project has no Redis and a multi-worker
    deployment would need a shared store. It still stops a single client from
    hammering one process, which is the failure mode of a public demo.

    `/api/compare` runs the pipeline three times, so it consumes three slots --
    charging it one slot would let a caller buy 3x the work for the same budget.
    """
    cfg = _cached_settings(request)
    cost = _COSTLY_PATHS.get(request.url.path)
    if cfg.rate_limit_per_minute > 0 and cost:
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with _RATE_LOCK:
            _purge_idle_rate_buckets(now)
            bucket = [stamp for stamp in _RATE_BUCKETS.get(client, []) if now - stamp < 60]
            if len(bucket) + cost > cfg.rate_limit_per_minute:
                _RATE_BUCKETS[client] = bucket
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "rate limit exceeded",
                        "request_id": getattr(request.state, "request_id", None),
                    },
                )
            bucket.extend([now] * cost)
            _RATE_BUCKETS[client] = bucket
    return await call_next(request)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Give every request an id and one machine-parseable access log line.

    The id is echoed in `X-Request-ID` and can be supplied by a proxy, which
    makes a user report ("it failed at 14:02") traceable to one log line.
    Accepted ids are restricted to a safe alphabet so a client cannot forge
    log lines or inject response headers.
    """
    supplied = request.headers.get("x-request-id", "")
    request_id = supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        _ACCESS_LOG.exception(
            "request_id=%s method=%s path=%s status=unhandled",
            request_id, request.method, request.url.path,
        )
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    _ACCESS_LOG.info(
        "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
        request_id, request.method, request.url.path,
        response.status_code, elapsed_ms,
    )
    return response


@lru_cache(maxsize=4)
def _remote_sink(url: str) -> RemoteRunSink:
    return RemoteRunSink(url)


def _sink(cfg: Settings) -> RunSink | None:
    """Where a finished run goes: nowhere, disk now, the queue, or one writer."""
    if cfg.writer_url:
        return _remote_sink(cfg.writer_url)
    if cfg.persist_mode == "off":
        return None
    store = _store(cfg)
    if cfg.persist_mode == "async":
        return get_writer(store)
    if cfg.persist_mode == "batch":
        return WaitingRunWriter(get_writer(store))
    return store


@app.get("/api/health")
def health(cfg: SettingsDep) -> dict[str, Any]:
    return {"status": "ok", "provider": cfg.provider, "version": __version__}


@app.get("/api/config")
def config(cfg: SettingsDep) -> dict[str, Any]:
    payload = cfg.masked()
    if cfg.writer_url:
        payload["writer"] = {"mode": "remote", "url": cfg.writer_url}
    elif cfg.persist_mode in ("async", "batch"):
        # Make the queue observable: pending rows are the honest answer to
        # "did my write land?", and sync_fallbacks means it is saturated.
        payload["writer"] = get_writer(_store(cfg)).stats.to_dict()
    return payload


@app.exception_handler(StorageError)
async def storage_error_handler(request: Request, exc: StorageError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.post(
    "/api/compare",
    dependencies=[Depends(require_api_key), Depends(enforce_body_limit)],
)
def compare_plans(payload: dict[str, Any], cfg: SettingsDep) -> dict[str, Any]:
    """Three real plans, three explicit trade-offs, one evidenced recommendation.

    Runs the pipeline three times, so it must be behind the same auth and rate
    limit as /api/plan -- an unauthenticated triple-run endpoint is a 3x cost
    amplifier for anyone who finds it.
    """
    raw = payload.get("request")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="request must be an object")
    request = PlanRequest.from_dict(raw)
    result = compare_strategies(request, settings=cfg, provider=build_provider(cfg))
    # Cost is invisible otherwise: compare writes no runs, so /api/summary never
    # sees these three generations unless they are reported here.
    result["cost_cny"] = round(
        sum(float(s["metrics"].get("cost_cny", 0.0)) for s in result["strategies"]), 6
    )
    result["runs_executed"] = len(result["strategies"])
    return result


@app.post("/api/understand", dependencies=[Depends(enforce_body_limit)])
def understand_brief(payload: dict[str, Any]) -> dict[str, Any]:
    """One free-form sentence -> structured request + visible assumptions.

    This is the entry point that makes the product feel less like a form and
    more like a conversation. Everything inferred is returned with its source
    and evidence, so the user can correct it before generating anything.
    """
    text = payload.get("text") or ""
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="text must be a non-empty string")
    today_raw = payload.get("today")
    today = None
    if today_raw:
        try:
            today = dt.date.fromisoformat(str(today_raw))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="today must be YYYY-MM-DD") from exc
    parsed = understand(text[:500], today=today, tools=get_tools())
    result = parsed.to_dict()
    verdict = check_feasibility(parsed.request, get_tools())
    result["feasibility"] = verdict.to_dict()
    result["negotiations"] = (
        [option.to_dict() for option in negotiate(parsed.request, tools=get_tools())]
        if not verdict.feasible else []
    )
    result["headroom"] = (
        [option.to_dict() for option in headroom(parsed.request, tools=get_tools())]
        if verdict.feasible else []
    )
    return result


@app.post("/api/followup", dependencies=[Depends(enforce_body_limit)])
def followup_brief(payload: dict[str, Any]) -> dict[str, Any]:
    """Adjust the previous brief with a small, deterministic vocabulary.

    This is intentionally not a chat endpoint: it returns the fields it changed,
    and ``recognized=False`` when it would have to guess. No model call.
    """
    text = payload.get("text") or ""
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="text must be a non-empty string")
    previous = payload.get("previous")
    if not isinstance(previous, dict):
        raise HTTPException(status_code=422, detail="previous must be a request object")
    return apply_followup(
        PlanRequest.from_dict(previous), text[:500], tools=get_tools()
    ).to_dict()


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    """What the knowledge base can actually plan for.

    The web client used to hardcode one city and five styles, so adding a city
    to the data never made it reachable in the product. Both dropdowns are now
    generated from here; a new city becomes usable without a frontend change.
    """
    tools = get_tools()
    cities = []
    for city_id in tools.city_ids():
        city = tools.city(city_id)
        locations = tools.locations(city_id)
        cities.append({
            "id": city_id,
            "name": city["name"],
            "center": city["center"],
            "location_count": len(locations),
            "indoor_count": sum(1 for loc in locations if loc["indoor"]),
            "tags": sorted({tag for loc in locations for tag in loc["tags"]}),
            "source_freshness": min(
                (loc.get("last_verified_at") or "") for loc in locations
            ),
        })
    styles = [
        {
            "id": s["id"],
            "name": s["name"],
            "era": s["era"],
            "price_cny": s["price_cny"],
            "suitable_tags": s["suitable_tags"],
        }
        for s in tools.styles()
    ]
    return {
        "cities": cities,
        "styles": styles,
        "city_count": len(cities),
        "style_count": len(styles),
        "location_count": sum(c["location_count"] for c in cities),
        "knowledge_version": knowledge_version(),
    }


@app.get("/api/rules")
def rules() -> dict[str, Any]:
    return {"count": len(RULES), "rules": RULES}


@app.post("/api/plan", dependencies=[Depends(require_api_key)])
async def create_plan(request: Request, cfg: SettingsDep) -> JSONResponse:
    try:
        raw = await request.body()
        if len(raw) > cfg.max_body_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"request body exceeds {cfg.max_body_bytes} bytes",
            )
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"body must be valid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        brief = PlanRequest.from_dict(body)
        # `mock_mode` is a demo aid. With a real provider configured, a client
        # must not be able to swap it for the offline planner.
        mode = body.pop("mock_mode", None) if cfg.provider == "mock" else None
        provider = (
            MockProvider(mode=mode)
            if mode in ("good", "naive")
            else build_provider(cfg)
        )
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
                request_id=request.state.request_id,
            )
        )
        return JSONResponse(result.to_dict())
    except ShotplanError as exc:
        raise HTTPException(status_code=_status_for(exc), detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/runs", dependencies=[Depends(require_api_key)])
def runs(cfg: SettingsDep, limit: int = 20) -> list[dict[str, Any]]:
    return _store(cfg).recent(min(max(limit, 1), 100))


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_api_key)])
def run_detail(run_id: str, cfg: SettingsDep) -> dict[str, Any]:
    found = _store(cfg).get(run_id)
    if found is None:
        raise HTTPException(status_code=404, detail="run not found")
    return found


def _export_payload(run_id: str, cfg: Settings) -> dict[str, Any]:
    payload = _store(cfg).get(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="run not found")
    return payload


@app.get("/api/runs/{run_id}/export.md", dependencies=[Depends(require_api_key)])
def export_markdown(run_id: str, cfg: SettingsDep) -> Response:
    from .export import plan_to_markdown, safe_filename

    text = plan_to_markdown(_export_payload(run_id, cfg))
    filename = f"shotplan-{safe_filename(run_id)}.md"
    return Response(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/runs/{run_id}/export.ics", dependencies=[Depends(require_api_key)])
def export_ics(run_id: str, cfg: SettingsDep) -> Response:
    from .export import plan_to_ics, safe_filename

    try:
        text = plan_to_ics(_export_payload(run_id, cfg))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    filename = f"shotplan-{safe_filename(run_id)}.ics"
    return Response(
        text,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/summary", dependencies=[Depends(require_api_key)])
def summary(cfg: SettingsDep) -> dict[str, Any]:
    return _store(cfg).summary()


@app.get("/api/stats", dependencies=[Depends(require_api_key)])
def stats(cfg: SettingsDep, limit: int = 200) -> dict[str, Any]:
    from .observability import build_dashboard

    return build_dashboard(_store(cfg), min(max(limit, 1), 1000))


@app.get("/metrics", dependencies=[Depends(require_api_key)], response_class=PlainTextResponse)
def metrics(cfg: SettingsDep, limit: int = 200):
    """Prometheus scrape endpoint. Same data as /api/stats, in text format."""
    from .observability import build_dashboard, render_prometheus

    dashboard = build_dashboard(_store(cfg), min(max(limit, 1), 1000))
    return PlainTextResponse(
        render_prometheus(dashboard),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


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
