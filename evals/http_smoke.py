"""Full-stack HTTP smoke test against a REAL uvicorn process.

Unit tests use TestClient (in-process ASGI). This lab goes through the loopback
network stack, JSON serialisation, the lifespan config snapshot and a real
SQLite file, then walks every public endpoint. It is the cheapest way to catch
a refactor that broke the deployment shape while unit tests stayed green.

    python -m evals.http_smoke --write
    python -m evals.http_smoke --check
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from evals.http_load import server

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "http-smoke.md"


@dataclass
class Check:
    endpoint: str
    method: str
    status: int
    contract: str
    ok: bool
    detail: str = ""


def walk(base_url: str) -> list[Check]:
    checks: list[Check] = []
    client = httpx.Client(base_url=base_url, timeout=20.0)

    def get(path: str, contract: str, verify) -> Any:
        response = client.get(path)
        payload = None
        try:
            payload = response.json()
        except Exception:
            pass
        ok, detail = verify(response, payload)
        checks.append(Check(path, "GET", response.status_code, contract, ok, detail))
        return payload

    def post(path: str, contract: str, body: dict, verify) -> Any:
        response = client.post(path, json=body)
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
        ok, detail = verify(response, payload)
        checks.append(Check(path, "POST", response.status_code, contract, ok, detail))
        return payload

    get("/api/health", "200 + status ok",
        lambda r, p: (r.status_code == 200 and p is not None and p.get("status") == "ok",
                      str(p)[:100] if p else f"status={r.status_code}"))
    get("/api/config", "masked config exposes provider",
        lambda r, p: (r.status_code == 200 and isinstance(p, dict) and "provider" in p, ""))
    get("/api/rules", "at least 15 rules",
        lambda r, p: (r.status_code == 200 and isinstance(p, dict)
                      and len(p.get("rules", p if isinstance(p, list) else [])) >= 15, ""))

    post("/api/plan", "feasible brief -> status ok",
         {"city": "xian", "date": "2026-10-04", "hours": 6, "budget_cny": 1500,
          "styles": ["tang"]},
         lambda r, p: (r.status_code == 200 and p and p.get("status") == "ok"
                       and p.get("validation", {}).get("hard_pass") is True, ""))
    post("/api/plan", "budget 100 -> structured infeasible",
         {"city": "xian", "date": "2026-10-04", "hours": 6, "budget_cny": 100,
          "styles": ["tang"]},
         lambda r, p: (r.status_code == 200 and p and p.get("status") == "infeasible", ""))

    get("/api/runs", "both runs persisted",
        lambda r, p: (r.status_code == 200 and isinstance(p, list) and len(p) >= 2,
                      f"rows={len(p) if isinstance(p, list) else 'n/a'}"))
    get("/api/summary", "status split: 1 ok + 1 infeasible, pass stays 1.0",
        lambda r, p: (r.status_code == 200 and isinstance(p, dict)
                      and p.get("total_runs") == 2 and p.get("ok_runs") == 1
                      and p.get("infeasible_runs") == 1
                      and p.get("hard_pass_rate") == 1.0, str(p)[:160]))
    get("/api/stats", "dashboard agrees with summary",
        lambda r, p: (r.status_code == 200 and isinstance(p, dict)
                      and p.get("overall", {}).get("ok_runs") == 1
                      and p.get("overall", {}).get("error_runs") == 0, ""))

    get("/api/catalog", "catalog is generated from the knowledge base",
        lambda r, p: (r.status_code == 200 and isinstance(p, dict)
                      and p.get("city_count", 0) >= 5 and p.get("style_count", 0) >= 5
                      and bool(p.get("knowledge_version")), str(p)[:120]))

    post("/api/understand", "one sentence -> request + assumptions",
         {"text": "国庆去西安拍唐风，预算 1500，最好上午收工"},
         lambda r, p: (r.status_code == 200 and isinstance(p, dict)
                       and p.get("request", {}).get("city") == "xian"
                       and bool(p.get("assumptions"))
                       and p.get("feasibility", {}).get("feasible") is True,
                       str(p)[:160]))

    post("/api/followup", "previous brief + more budget -> 700, no model call",
         {"previous": {"city": "xian", "date": "2026-10-04", "hours": 2,
                       "budget_cny": 400, "styles": ["tang"]},
          "text": "预算多一点"},
         lambda r, p: (r.status_code == 200 and isinstance(p, dict)
                       and p.get("recognized") is True
                       and p.get("request", {}).get("budget_cny") == 700
                       and bool(p.get("changes")), str(p)[:160]))

    post("/api/compare", "three strategies, all executable under the same brief",
         {"request": {"city": "xian", "date": "2026-10-04", "hours": 6,
                      "budget_cny": 1500, "styles": ["tang"]}},
         lambda r, p: (r.status_code == 200 and isinstance(p, dict)
                       and p.get("runs_executed") == 3
                       and all(s.get("metrics", {}).get("status") == "ok"
                               and s.get("metrics", {}).get("hard_pass") is True
                               for s in p.get("strategies", [])),
                       str(p.get("recommendation", {}))[:120]))

    def verify_metrics(r, p):
        text = r.text
        needed = ("shotplan_runs", "shotplan_error_rate", "shotplan_llm_cost_cny",
                  "shotplan_hard_pass_rate")
        missing = [name for name in needed if name not in text]
        return (r.status_code == 200 and "text/plain" in r.headers.get("content-type", "")
                and not missing), f"missing={missing}"

    get("/metrics", "Prometheus text with status-aware gauges", verify_metrics)
    get("/", "built-in web UI served",
        lambda r, p: (r.status_code == 200 and "text/html" in r.headers.get("content-type", ""), ""))

    client.close()
    return checks


def render(checks: list[Check]) -> str:
    lines = [
        "# HTTP 全链路冒烟",
        "",
        "> 由 `python -m evals.http_smoke --write` 生成；`--check` 是门禁。",
        "> 真实 `uvicorn` 子进程 + loopback 请求，不是 TestClient；零 API 成本（mock provider）。",
        "",
        "| 端点 | 方法 | HTTP | 契约 | 结果 |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for c in checks:
        result = "PASS" if c.ok else f"FAIL {c.detail}"
        lines.append(f"| `{c.endpoint}` | {c.method} | {c.status} | {c.contract} | {result} |")
    passed = sum(c.ok for c in checks)
    lines += [
        "",
        f"**{passed}/{len(checks)} 通过。**",
        "",
        "## 为什么需要这一层",
        "",
        "单元测试走 TestClient（进程内 ASGI），跳过 lifespan 的配置快照、loopback 网络栈、",
        "真实 SQLite 文件与多端点之间的状态传递。`config.py` / `api.py` / `observability.py`",
        "的改动恰恰最容易在这些地方出问题，而单测仍全绿。",
        "",
        "重点验证的四条契约：",
        "",
        "1. 预检拒绝（`infeasible`）会被持久化，但不算进质量分母：",
        "   `total_runs=2, ok_runs=1, hard_pass_rate=1.0`。",
        "2. `/api/summary`（全表 SQL）与 `/api/stats`（最近 N 条）在状态口径上一致。",
        "3. `/metrics` 输出状态感知的 gauge（`error_rate` / `llm_cost_cny`），且是 `text/plain`。",
        "4. 新交互层在真实服务下可用：`/api/understand` 返回假设清单，"
        "`/api/compare` 三份方案全部满足同一份原始 brief。",
        "",
        "## 覆盖边界",
        "",
        "- 只用 mock provider，不验证真实模型调用；真实链路复测见 [architecture-review.md](architecture-review.md)。",
        "- 单 worker、并发 1，不覆盖多进程写锁（见 [http-load.md](http-load.md)）。",
        "- 不设 `SHOTPLAN_API_KEY`，鉴权矩阵另见 `tests/test_api.py`。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    # The uvicorn child inherits os.environ; a developer's local key must not
    # turn every endpoint into a 401.
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("SHOTPLAN_")}
    try:
        os.environ["SHOTPLAN_SKIP_DOTENV"] = "1"
        with tempfile.TemporaryDirectory() as raw:
            with server(workers=1, db_path=Path(raw) / "smoke.db", persist=True) as (base_url, _):
                checks = walk(base_url)
    finally:
        for key in list(os.environ):
            if key.startswith("SHOTPLAN_"):
                os.environ.pop(key, None)
        os.environ.update(saved)

    expected = render(checks)
    failed = [c for c in checks if not c.ok]
    if args.check:
        actual = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if actual.strip() != expected.strip() or failed:
            print(f"http-smoke gate: FAIL ({len(failed)} broken contracts)", file=sys.stderr)
            return 1
        print("http-smoke gate: OK")
        return 0
    if args.write:
        OUT.write_text(expected, encoding="utf-8")
        print(f"wrote {OUT} ({sum(c.ok for c in checks)}/{len(checks)} passed)")
        return 1 if failed else 0
    print(expected)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
