"""Fault injection: prove the degradation paths without spending API credit.

Every scenario drives the REAL `run_plan` (or the real API) with one injected
fault, then asserts what the caller actually observes. A green result is not
"no exception" -- it is "the failure is classified, traceable, and does not
corrupt the quality metrics".

    python -m evals.fault_injection --write
    python -m evals.fault_injection --check
"""

from __future__ import annotations

import argparse
import errno
import gc
import sys
from dataclasses import dataclass
from pathlib import Path

from shotplan.agents.base import GenerationResult, PlanProvider
from shotplan.agents.mock import MockProvider
from shotplan.config import Settings
from shotplan.domain.errors import (
    KnowledgeError,
    ProviderError,
    StorageError,
)
from shotplan.domain.models import PlanRequest
from shotplan.observability import build_dashboard
from shotplan.pipeline import run_plan
from shotplan.storage import RunStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "fault-injection.md"


@dataclass
class Result:
    name: str
    category: str
    injected: str
    status: str
    codes: list[str]
    trace_events: int
    caller_lost_plan: bool
    ok: bool
    detail: str


class FaultProvider(PlanProvider):
    name = "fault"
    prompt_version = "planner-v2"

    def __init__(self, exc: Exception):
        self.exc = exc

    def generate(self, request, context, repair_hint=None) -> GenerationResult:
        raise self.exc


class MalformedProvider(PlanProvider):
    name = "malformed"
    prompt_version = "planner-v2"

    def generate(self, request, context, repair_hint=None) -> GenerationResult:
        return GenerationResult(payload="this is not JSON", prompt_version=self.prompt_version)


class FaultStore:
    def __init__(self, exc: Exception):
        self.exc = exc

    def save(self, result) -> str:
        raise self.exc

    def save_many(self, results) -> int:
        raise self.exc


class BrokenKnowledge:
    @staticmethod
    def context_for(request):
        raise KnowledgeError("knowledge base unreadable")


def _run(tmp: Path, *, provider=None, store=None, tools=None, patch_validate=None) -> Result:
    import shotplan.pipeline as pipeline

    original = pipeline.validate
    if patch_validate is not None:
        pipeline.validate = patch_validate
    try:
        result = run_plan(
            PlanRequest(),
            provider=provider or MockProvider("good"),
            store=store,
            tools=tools,
            settings=Settings(provider="mock", max_repairs=1, db_path=tmp / "fi.db"),
            max_repairs=1,
        )
    finally:
        pipeline.validate = original
    return result


def _record(name: str, category: str, injected: str, result, expect_status: str,
            expect_code: str | None, *, plan_must_survive: bool) -> Result:
    codes = [issue.code for issue in result.validation.issues]
    events = len(result.trace.events)
    lost_plan = plan_must_survive and result.status != "ok"
    ok = (
        result.status == expect_status
        and (expect_code is None or expect_code in codes)
        and events > 0
        and not lost_plan
    )
    detail = "OK" if ok else f"status={result.status}, codes={codes[:4]}, events={events}"
    return Result(name, category, injected, result.status, codes, events, lost_plan, ok, detail)


def run_matrix() -> list[Result]:
    import tempfile

    rows: list[Result] = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)

        # --- provider faults -------------------------------------------------
        for name, exc, code, status in (
            ("HTTP 429 限流", ProviderError("HTTP 429 from relay.example: rate limited"), "PIPELINE_ERROR", "error"),
            ("HTTP 502 上游", ProviderError("HTTP 502 from relay.example: bad gateway"), "PIPELINE_ERROR", "error"),
            ("连接超时", TimeoutError("timed out"), "PIPELINE_ERROR", "error"),
        ):
            result = _run(tmp, provider=FaultProvider(exc))
            rows.append(_record(name, "provider", type(exc).__name__, result, status, code,
                                plan_must_survive=False))

        malformed = _run(tmp, provider=MalformedProvider())
        rows.append(_record("模型输出无法解析", "provider", "ParseError", malformed,
                            "error", "PIPELINE_ERROR", plan_must_survive=False))

        # --- persistence faults: the plan must still come back ---------------
        for name, exc in (
            ("SQLite 锁", StorageError("database is locked")),
            ("磁盘满 ENOSPC", OSError(errno.ENOSPC, "No space left on device")),
            ("目录无权限", PermissionError("permission denied")),
        ):
            result = _run(tmp, store=FaultStore(exc))
            rows.append(_record(name, "persistence", type(exc).__name__, result,
                                "ok", "PERSISTENCE_FAILED", plan_must_survive=True))

        # --- unexpected exceptions outside the guarded stages ---------------
        broken_kb = _run(tmp, tools=BrokenKnowledge())
        rows.append(_record("知识库读取失败", "runtime", "KnowledgeError", broken_kb,
                            "error", "PIPELINE_ERROR", plan_must_survive=False))

        def exploding_validate(*args, **kwargs):
            raise RuntimeError("validator crashed")

        broken_val = _run(tmp, patch_validate=exploding_validate)
        rows.append(_record("校验器意外异常", "runtime", "RuntimeError", broken_val,
                            "error", "PIPELINE_ERROR", plan_must_survive=False))

        # --- metric integrity: outages must not lower hard_pass_rate ---------
        db = tmp / "metrics.db"
        store = RunStore(db)
        good = run_plan(
            PlanRequest(), provider=MockProvider("good"),
            settings=Settings(provider="mock", max_repairs=0, db_path=db), max_repairs=0,
        )
        store.save(good)
        failed = run_plan(PlanRequest(), provider=FaultProvider(ProviderError("HTTP 502 from relay.example")),
                          settings=Settings(provider="mock", max_repairs=0, db_path=db), max_repairs=0)
        store.save(failed)
        overall = build_dashboard(store)["overall"]
        metric_ok = overall["hard_pass_rate"] == 1.0 and overall["error_rate"] == 0.5
        rows.append(Result(
            "断供不污染质量分母", "metrics", "1 ok + 1 error",
            f"pass={overall['hard_pass_rate']} error_rate={overall['error_rate']}",
            [], 0, False, metric_ok,
            "OK" if metric_ok else f"overall={overall}",
        ))

        # --- API status mapping ---------------------------------------------
        import os

        from fastapi.testclient import TestClient

        import shotplan.api as api_module
        from shotplan.api import app
        from shotplan.api import build_provider as api_build_provider

        os.environ["SHOTPLAN_DB_PATH"] = str(tmp / "api.db")
        os.environ["SHOTPLAN_PROVIDER"] = "mock"
        os.environ["SHOTPLAN_SKIP_DOTENV"] = "1"
        for name, exc, expected in (
            ("API 上游失败 -> 502", ProviderError("upstream down"), 502),
            ("API 存储故障 -> 503", StorageError("db locked"), 503),
        ):
            original = api_build_provider
            api_module.build_provider = lambda cfg, _exc=exc: (_ for _ in ()).throw(_exc)
            try:
                with TestClient(app) as client:
                    code = client.post("/api/plan", json={}).status_code
            finally:
                api_module.build_provider = original
            rows.append(Result(
                name, "api", type(exc).__name__, str(code), [], 0, False,
                code == expected, "OK" if code == expected else f"want {expected}, got {code}",
            ))

        # Windows keeps SQLite files locked while a connection object is alive.
        # Coverage instrumentation can keep frames (and therefore connections)
        # referenced after the block, so force a collection before the temp
        # directory is removed. This is harness cleanup, not product behaviour.
        store = None
        gc.collect()
    return rows


def render(rows: list[Result]) -> str:
    lines = [
        "# 故障注入矩阵",
        "",
        "> 由 `python -m evals.fault_injection --write` 生成；`--check` 是门禁。",
        "> 全部离线，零 API 成本。每行都驱动真实的 `run_plan` / API，只注入一处故障。",
        "",
        "绿色不等于「没抛异常」，而是「失败被分类、可追溯、且没有污染质量指标」。",
        "",
        "| 场景 | 类别 | 注入 | 观测结果 | 结论 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.name} | `{row.category}` | `{row.injected}` | "
            f"`{row.status}` | {'PASS' if row.ok else 'FAIL: ' + row.detail} |"
        )
    passed = sum(row.ok for row in rows)
    lines += [
        "",
        f"**{passed}/{len(rows)} 通过。**",
        "",
        "## 关键契约",
        "",
        "- provider 超时 / 429 / 5xx / 解析失败 → `status=error` 且带 `PIPELINE_ERROR`，",
        "  不是 500，也不是一个看起来正常的低分方案。",
        "- SQLite 锁 / 磁盘满 / 无权限 → 方案仍返回 `status=ok`，追加 `PERSISTENCE_FAILED` **soft** issue。",
        "  持久化是辅助能力，不能否决已经算出来的结果（`_safe_save` 同时捕获 `StorageError` 与 `OSError`）。",
        "- 预检、知识库、校验器等未设局部守卫的阶段抛异常，由 `run_plan` 顶层边界统一兜底。",
        "- 一条 provider 断供 + 一条正常方案 → `hard_pass_rate` 仍是 1.0、`error_rate` 是 0.5。",
        "  基础设施故障不算模型质量差。",
        "- API 层 `ProviderError → 502`、`StorageError → 503`，与客户端输入错误（422）区分。",
        "",
        "## 覆盖边界",
        "",
        "- 用注入的异常模拟故障，不是真的把磁盘写满或锁住数据库文件；",
        "  真实并发写锁行为另见 [loadtest.md](loadtest.md) 与 [persistence.md](persistence.md)。",
        "- 未覆盖进程被 SIGKILL、网络分区、DNS 劫持等需要真实环境的情况。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    rows = run_matrix()
    expected = render(rows)
    if args.check:
        actual = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if actual.strip() != expected.strip():
            print("fault-injection report drift: run --write", file=sys.stderr)
            return 1
        print("fault-injection gate: OK")
        return 0
    if args.write:
        OUT.write_text(expected, encoding="utf-8")
        print(f"wrote {OUT} ({sum(r.ok for r in rows)}/{len(rows)} passed)")
        return 0 if all(r.ok for r in rows) else 1
    print(expected)
    return 0 if all(r.ok for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
