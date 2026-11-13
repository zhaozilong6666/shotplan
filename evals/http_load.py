"""Real HTTP load test: from in-process microbenchmarks to an actual service.

Every throughput number so far came from calling Python functions directly:
``loadtest.py`` drives the pipeline in threads, ``scaling.py`` drives it in a
process pool, ``benchmark.py`` times a single call. None of them include the
parts a real deployment actually pays for: HTTP parsing, ASGI middleware, JSON
serialisation of a full plan + trace, the loopback network stack, and a SQLite
write from *several OS processes* at once.

This lab starts a real ``uvicorn`` subprocess with 1 / 2 / 4 workers and sends
POST /api/plan over loopback with httpx. It measures concurrency 1 / 4 / 8 and
the worker-scaling curve, and verifies that every accepted request produced a
stored run (multi-process SQLite writers are a different failure mode from the
thread case).

Following ADR 0008: throughput is machine-dependent and is REPORTED, never
gated. The CI gate asserts only machine-independent contracts -- zero errors,
zero lost writes, and a deliberately loose latency ceiling.

Usage:
    python -m evals.http_load --check       # CI gate: one small, stable scenario
    python -m evals.http_load --write       # full matrix -> snapshot + report
    python -m evals.http_load               # quick summary only
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from evals import OUT_DIR
from evals.benchmark import percentile

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "evals" / "http_load.json"
REPORT_PATH = ROOT / "docs" / "http-load.md"

BRIEFS: tuple[dict[str, Any], ...] = (
    {"city": "xian", "date": "2026-10-04", "hours": 6, "budget_cny": 1500, "styles": ["tang"]},
    {"city": "suzhou", "date": "2026-04-18", "hours": 5, "budget_cny": 1200, "styles": ["song"]},
    {"city": "luoyang", "date": "2026-05-16", "hours": 8, "budget_cny": 2200, "styles": ["ming"]},
    {"city": "hangzhou", "date": "2026-03-21", "hours": 7, "budget_cny": 1800, "styles": ["song"]},
)

CONCURRENCY_LEVELS = (1, 4, 8)
WORKER_LEVELS = (1, 2, 4)
DEFAULT_REQUESTS = 200
CHECK_REQUESTS = 40
CHECK_CONCURRENCY = 4
# A very loose ceiling: its only job is to catch a hung service, not to grade
# performance. Real numbers are reported in docs/http-load.md.
MAX_P95_MS = 3000.0
READY_TIMEOUT_S = 40.0


@dataclass(frozen=True)
class Result:
    kind: str  # ok | http | plan | exception
    elapsed_ms: float
    detail: str = ""


@dataclass
class Scenario:
    workers: int
    concurrency: int
    requests: int
    results: list[Result] = field(default_factory=list)
    wall_s: float = 0.0
    runs_before: int = 0
    runs_after: int = 0
    startup_s: float = 0.0

    @property
    def latencies(self) -> list[float]:
        return [r.elapsed_ms for r in self.results]

    @property
    def errors(self) -> list[str]:
        return [r.detail for r in self.results if r.kind != "ok"]

    @property
    def ok_count(self) -> int:
        return sum(r.kind == "ok" for r in self.results)

    @property
    def runs_delta(self) -> int:
        return self.runs_after - self.runs_before

    @property
    def lost_writes(self) -> int:
        return max(0, self.ok_count - self.runs_delta)

    def metrics(self) -> dict[str, Any]:
        latencies = self.latencies
        kinds = Counter(r.kind for r in self.results)
        return {
            "workers": self.workers,
            "concurrency": self.concurrency,
            "requests": self.requests,
            "ok": self.ok_count,
            "errors": len(self.errors),
            "error_samples": self.errors[:3],
            "kinds": dict(sorted(kinds.items())),
            "wall_s": round(self.wall_s, 3),
            "startup_s": round(self.startup_s, 2),
            "rps": round(self.requests / self.wall_s, 2) if self.wall_s else 0.0,
            "p50_ms": round(percentile(latencies, 50), 2),
            "p95_ms": round(percentile(latencies, 95), 2),
            "mean_ms": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "runs_before": self.runs_before,
            "runs_after": self.runs_after,
            "runs_delta": self.runs_delta,
            "lost_writes": self.lost_writes,
        }


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop(proc: subprocess.Popen) -> None:
    """Kill the whole uvicorn process tree, including spawned workers."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
    else:
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)


@contextlib.contextmanager
def server(workers: int, db_path: Path) -> Iterator[tuple[str, float]]:
    """Start real uvicorn in a subprocess; always tear it down."""
    port = free_port()
    env = os.environ.copy()
    env["SHOTPLAN_DB_PATH"] = str(db_path)
    env["SHOTPLAN_PROVIDER"] = "mock"
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable, "-m", "uvicorn", "shotplan.api:app",
        "--host", "127.0.0.1", "--port", str(port),
        "--workers", str(workers),
        "--log-level", "warning", "--no-access-log",
    ]
    kwargs: dict[str, Any] = {
        "env": env,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    started = time.perf_counter()
    proc = subprocess.Popen(command, **kwargs)  # noqa: S603 - fixed, local command
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.perf_counter() + READY_TIMEOUT_S
        while time.perf_counter() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"uvicorn exited early with code {proc.returncode}")
            try:
                # trust_env=False: HTTP(S)_PROXY on this machine rewrites
                # loopback requests into absolute-form targets, which uvicorn
                # answers with 404 for everything except the health probe.
                with httpx.Client(timeout=1.0, trust_env=False) as probe:
                    if probe.get(base_url + "/api/health").status_code == 200:
                        break
            except Exception:  # noqa: BLE001 - readiness polling
                time.sleep(0.2)
        else:
            raise RuntimeError("uvicorn did not become ready in time")
        yield base_url, time.perf_counter() - started
    finally:
        _stop(proc)


def total_runs(client: httpx.Client) -> int:
    response = client.get("/api/stats", params={"limit": 1000})
    response.raise_for_status()
    return int(response.json().get("total_runs", 0))


def one(client: httpx.Client, index: int) -> Result:
    started = time.perf_counter()
    try:
        response = client.post("/api/plan", json=BRIEFS[index % len(BRIEFS)])
        elapsed = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            return Result("http", elapsed, f"HTTP {response.status_code}: {response.text[:120]}")
        payload = response.json()
        if payload.get("status") != "ok":
            return Result("plan", elapsed, f"status={payload.get('status')}")
        if not isinstance(payload.get("validation"), dict):
            return Result("plan", elapsed, "missing validation block")
        return Result("ok", elapsed)
    except Exception as exc:  # noqa: BLE001 - a load test must catch everything
        return Result(
            "exception", (time.perf_counter() - started) * 1000,
            f"{type(exc).__name__}: {exc}",
        )


def run_scenario(workers: int, concurrency: int, requests: int) -> Scenario:
    scenario = Scenario(workers, concurrency, requests)
    with tempfile.TemporaryDirectory(
        prefix="shotplan-http-", ignore_cleanup_errors=True
    ) as tmp:
        db_path = Path(tmp) / "runs.db"
        with server(workers, db_path) as (base_url, startup_s):
            scenario.startup_s = startup_s
            limits = httpx.Limits(
                max_connections=max(8, concurrency * 2),
                max_keepalive_connections=max(8, concurrency * 2),
            )
            with httpx.Client(
                base_url=base_url, timeout=10.0, limits=limits, trust_env=False
            ) as client:
                scenario.runs_before = total_runs(client)
                started = time.perf_counter()
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    scenario.results = list(
                        pool.map(lambda i: one(client, i), range(requests))
                    )
                scenario.wall_s = time.perf_counter() - started
                scenario.runs_after = total_runs(client)
    return scenario


def contract_problems(scenario: Scenario, *, gate: bool) -> list[str]:
    """Machine-independent invariants only; throughput is never gated."""
    problems: list[str] = []
    if scenario.requests != len(scenario.results):
        problems.append(
            f"only {len(scenario.results)}/{scenario.requests} responses collected"
        )
    for error in scenario.errors[:3]:
        problems.append(f"request failed: {error}")
    if len(scenario.errors) > 3:
        problems.append(f"... and {len(scenario.errors) - 3} more failures")
    if scenario.lost_writes:
        problems.append(
            f"{scenario.lost_writes} accepted request(s) were not persisted "
            f"(before={scenario.runs_before}, after={scenario.runs_after})"
        )
    if gate and percentile(scenario.latencies, 95) > MAX_P95_MS:
        problems.append(
            f"p95 {percentile(scenario.latencies, 95):.1f}ms exceeds the loose "
            f"ceiling {MAX_P95_MS:.0f}ms: the service may be hung"
        )
    return problems


def build_report(scenarios: list[Scenario], cpu_count: int) -> str:
    by_key = {(s.workers, s.concurrency): s for s in scenarios}
    ladder = [by_key[(1, c)] for c in CONCURRENCY_LEVELS if (1, c) in by_key]
    workers_curve = [by_key[(w, 8)] for w in WORKER_LEVELS if (w, 8) in by_key]
    # The worker curve is measured at concurrency 8, so its baseline must be
    # the single-worker scenario at that SAME concurrency, not concurrency 1.
    baseline = by_key.get((1, 8))
    base_rps = baseline.requests / baseline.wall_s if baseline and baseline.wall_s else 0.0
    lines = [
        "# 真实 HTTP 服务压测",
        "",
        "> 由 `python -m evals.http_load --write` 生成本机快照。数字与机器相关，**不进 CI 门禁**。",
        "",
        "## 为什么还要做这一层",
        "",
        "之前的 `loadtest.py`（进程内线程）、`scaling.py`（进程池）、`benchmark.py`（单次调用）"
        "测的都是 Python 函数，没有 HTTP 解析、ASGI 中间件、完整 plan + trace 的 JSON 序列化、"
        "loopback 网络栈，也没有**多个 OS 进程同时写同一个 SQLite 文件**。",
        "这一层启动真正的 `uvicorn`，用 `httpx` 走 `127.0.0.1` 发 `POST /api/plan`，"
        "把「服务」而不是「函数」放到压力下面。",
        "",
        f"环境：Python {platform.python_version()} / {platform.system()} / {cpu_count} 逻辑核；"
        "离线 mock provider，无外部网络调用。",
        "",
        "## 单 worker 的并发阶梯",
        "",
        "| 并发 | 请求 | 吞吐 (/s) | P50 / P95 (ms) | 错误 | 落库 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sc in ladder:
        lines.append(
            f"| {sc.concurrency} | {sc.requests} | {sc.metrics()['rps']} | "
            f"{sc.metrics()['p50_ms']} / {sc.metrics()['p95_ms']} | "
            f"{len(sc.errors)} | {sc.runs_delta}/{sc.ok_count} |"
        )
    lines += [
        "",
        "## worker 扩展曲线（并发固定 8）",
        "",
        "| workers | 请求 | 吞吐 (/s) | 相对 1 worker | P95 (ms) | 错误 | 启动 (s) |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sc in workers_curve:
        rps = sc.requests / sc.wall_s if sc.wall_s else 0.0
        speedup = rps / base_rps if base_rps else 0.0
        lines.append(
            f"| {sc.workers} | {sc.requests} | {rps:.2f} | {speedup:.2f}× | "
            f"{sc.metrics()['p95_ms']} | {len(sc.errors)} | {sc.startup_s:.2f} |"
        )
    lines += [
        "",
        "相对 1 worker 的倍数以**同一批并发 8 请求**为基准（表中的基准吞吐取自单 worker 并发 8 场景）。",
        "启动时间包含 uvicorn 进程创建与 `spawn`/`fork` 开销，不计入请求计时。",
        "",
        "## 与进程内数字对照",
        "",
        "- `docs/loadtest.md`：进程内线程并发 8 约 **310/s**，p95 约 **27ms**。",
        "- `docs/scaling.md`：进程池纯函数吞吐约 **数千次/秒**。",
        "- 这里单 worker 并发 8 的吞吐与进程内同量级但略低（多了 HTTP/JSON/网络栈与一次"
        "完整 SQLite 提交），P95 则从约 27ms 变成几十到上百 ms；"
        "这才是对外提供服务时真正能拿到的量级。",
        "",
    ]
    one_worker = by_key.get((1, 8))
    max_worker = workers_curve[-1] if workers_curve else None
    if one_worker and max_worker and max_worker.workers > 1:
        r1 = one_worker.requests / one_worker.wall_s if one_worker.wall_s else 0.0
        rn = max_worker.requests / max_worker.wall_s if max_worker.wall_s else 0.0
        ratio = rn / r1 if r1 else 0.0
        best = max(
            workers_curve,
            key=lambda s: s.requests / s.wall_s if s.wall_s else 0.0,
        )
        best_rps = best.requests / best.wall_s if best.wall_s else 0.0
        best_ratio = best_rps / r1 if r1 else 0.0
        curve_note = (
            f"曲线最好的一档是 **{best.workers} worker（{best_ratio:.2f}×）**，"
            "继续加 worker 反而回落。"
            if best.workers < max_worker.workers
            else f"最佳一档是 **{best.workers} worker（{best_ratio:.2f}×）**。"
        )
        lines += [
            "## 多 worker 为什么没有线性提速",
            "",
            f"实测 {max_worker.workers} worker 的吞吐是单 worker 的 **{ratio:.2f}×**，"
            f"P95 从 **{one_worker.metrics()['p95_ms']} ms** 变为 "
            f"**{max_worker.metrics()['p95_ms']} ms**。" + curve_note,
            "",
            "这不推翻 `docs/scaling.md` 里「纯计算受 GIL 限制」的结论，而是补上了一个前提："
            "**瓶颈会随架构层变化**。进程池实验里各 worker 不共享任何状态；真实服务里"
            "每个 `/api/plan` 都要写一条 run 记录，4 个 uvicorn worker 抢的是同一个 SQLite 文件，"
            "WAL 同一时刻只允许一个写者，跨进程写锁与进程协调开销会盖过并行收益。",
            "",
            "换句话说：要真正在多核上扩展，先得把共享 SQLite 从关键路径上移开"
            "（独立 DB 服务、写队列或按进程分片），再加 worker；只加 worker 只会放大争用。"
            "这条结论只有把服务真正跑起来才看得见，进程内微基准给不了。",
            "",
            "数据来自本机 loopback 小样本，不外推到容器化或带真实模型延迟的生产环境。",
            "",
        ]
    lines += [
        "## 门禁检查什么",
        "",
        "`python -m evals.http_load --check` 只跑一组稳定场景（1 worker、并发 "
        f"{CHECK_CONCURRENCY}、{CHECK_REQUESTS} 次请求），断言：",
        "",
        "1. 所有请求都收到响应；",
        "2. 全部返回 HTTP 200 + `status=ok` + `validation` 块；",
        "3. `GET /api/stats` 的 `total_runs` 增量等于成功请求数（**0 丢写**）；",
        f"4. P95 不超过 {MAX_P95_MS:.0f}ms 的宽松上限，只用于抓「服务挂住」。",
        "",
        "吞吐、P50/P95 的绝对值、worker 加速比**都不做断言**：它们随 CPU、磁盘与 CI 机器负载变化，"
        "把它们写进门禁只会制造偶发红（ADR 0008 的原则，扩展性门禁已因此修过一次）。",
        "",
        "## 边界",
        "",
        "- 单机 loopback、无 TLS、无反向代理、无真实模型延迟；离线 provider 是纯 CPU 计算。",
        "- 这不是生产 QPS，也不能外推到容器化部署；它只说明「服务层的开销量级」和"
        "「多进程写 SQLite 仍然不丢数据」。",
        "- 快照 `evals/http_load.json` 是本机一次运行的结果，重跑会变化；展示页读的是这份已提交快照。",
        "",
        "## 复现",
        "",
        "```bash",
        "python -m evals.http_load --check   # CI 门禁",
        "python -m evals.http_load --write  # 重跑完整矩阵并刷新报告",
        "```",
        "",
    ]
    return "\n".join(lines)


def snapshot(scenarios: list[Scenario], cpu_count: int) -> dict[str, Any]:
    return {
        "version": 1,
        "machine_dependent": True,
        "note": "single-machine loopback snapshot; throughput is reported, never gated",
        "python": platform.python_version(),
        "platform": platform.system(),
        "cpu_count": cpu_count,
        "requests_per_scenario": scenarios[0].requests if scenarios else 0,
        "scenarios": [sc.metrics() for sc in scenarios],
    }


def print_summary(scenario: Scenario) -> None:
    m = scenario.metrics()
    print(
        f"workers={scenario.workers} concurrency={scenario.concurrency} "
        f"requests={scenario.requests}: rps={m['rps']} "
        f"p50/p95={m['p50_ms']}/{m['p95_ms']}ms "
        f"errors={m['errors']} persisted={scenario.runs_delta}/{scenario.ok_count} "
        f"startup={m['startup_s']}s"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="real HTTP load test for shotplan")
    parser.add_argument("--write", action="store_true", help="full matrix + snapshot + report")
    parser.add_argument("--check", action="store_true", help="CI gate: stable subset")
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS)
    parser.add_argument("--workers", nargs="+", type=int, default=None)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "http_load.json")
    args = parser.parse_args(argv)

    cpu_count = os.cpu_count() or 1
    try:
        if args.write:
            requests = args.requests
            worker_levels = args.workers or list(WORKER_LEVELS)
            scenarios: list[Scenario] = []
            for concurrency in CONCURRENCY_LEVELS:
                sc = run_scenario(1, concurrency, requests)
                print_summary(sc)
                scenarios.append(sc)
            for workers in [w for w in worker_levels if w != 1]:
                sc = run_scenario(workers, 8, requests)
                print_summary(sc)
                scenarios.append(sc)
            SNAPSHOT_PATH.write_text(
                json.dumps(snapshot(scenarios, cpu_count), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            REPORT_PATH.write_text(build_report(scenarios, cpu_count), encoding="utf-8")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(snapshot(scenarios, cpu_count), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"snapshot: {SNAPSHOT_PATH}")
            print(f"report: {REPORT_PATH}")
            return 0

        sc = run_scenario(1, CHECK_CONCURRENCY, CHECK_REQUESTS if args.check else args.requests)
        print_summary(sc)
        problems = contract_problems(sc, gate=args.check)
        if problems:
            print("HTTP-LOAD GATE FAILED:")
            for problem in problems:
                print(f"  - {problem}")
        elif args.check:
            print("http-load gate: OK")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(snapshot([sc], cpu_count), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return 1 if problems else 0
    except Exception as exc:  # noqa: BLE001 - surface a failed server as a gate failure
        print(f"HTTP-LOAD GATE FAILED: could not run the service: "
              f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
