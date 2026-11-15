"""Derive the SLO baseline from committed real-model snapshots.

An SLO written from intuition ("95% under 30s") is worse than no SLO: the
measured P50 alone is 40-88s on this provider. This script makes the numbers
auditable and turns them into a gated table.

    python -m evals.slo_baseline --write
    python -m evals.slo_baseline --check
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "slo-baseline.md"

# Snapshots that used a different (much more expensive) model or a partial
# warm-up run. They stay visible but are excluded from the primary baseline.
LEGACY_MODELS = {"gpt-4o-mini", "deepseek-v4.1-flash-1017"}


def collect() -> list[dict]:
    rows = []
    for path in sorted((ROOT / "evals").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "hard_pass_rate" not in data:
            continue
        if data.get("provider") == "mock":
            continue
        planned = data.get("cases_needing_a_plan") or 0
        if not planned:
            continue
        rows.append({
            "name": path.name,
            "model": data.get("model", "?"),
            "cases": planned,
            "p50_s": (data.get("p50_ms") or 0) / 1000,
            "p95_s": (data.get("p95_ms") or 0) / 1000,
            "pass_rate": data.get("hard_pass_rate", 0.0),
            "error_rate": data.get("error_rate", 0.0),
            "cost_per_call": (data.get("cost_cny") or 0) / planned,
            "legacy": data.get("model") in LEGACY_MODELS,
        })
    return rows


def render(rows: list[dict]) -> str:
    primary = [r for r in rows if not r["legacy"]]
    lines = [
        "# SLO 实测基线",
        "",
        "> 由 `python -m evals.slo_baseline --write` 从已归档的真实模型快照生成；`--check` 是门禁。",
        "> 这不是生产流量，是离线评测的观测值；上线后必须用真实分布重新校准。",
        "",
        "| 快照 | 模型 | 生成调用 | P50 (s) | P95 (s) | 通过率 | 上游错误率 | ¥/调用 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        tag = " (旧模型)" if r["legacy"] else ""
        lines.append(
            f"| `{r['name']}` | `{r['model']}`{tag} | {r['cases']} | "
            f"{r['p50_s']:.1f} | {r['p95_s']:.1f} | {r['pass_rate'] * 100:.1f}% | "
            f"{r['error_rate'] * 100:.1f}% | {r['cost_per_call']:.4f} |"
        )

    def stats(key: str) -> tuple[float, float, float]:
        values = [r[key] for r in primary]
        return min(values), statistics.fmean(values), max(values)

    p50_lo, p50_mean, p50_hi = stats("p50_s")
    p95_lo, p95_mean, p95_hi = stats("p95_s")
    pass_lo, pass_mean, pass_hi = stats("pass_rate")
    err_lo, err_mean, err_hi = stats("error_rate")
    cost_lo, cost_mean, cost_hi = stats("cost_per_call")

    lines += [
        "",
        f"主基线：{len(primary)} 份快照（排除 {len(rows) - len(primary)} 份旧模型）。",
        "",
        "| 指标 | 最小 | 均值 | 最大 | 建议初始阈值 |",
        "| --- | ---: | ---: | ---: | --- |",
        f"| P50 延迟 (s) | {p50_lo:.1f} | {p50_mean:.1f} | {p50_hi:.1f} | ≤ 95 |",
        f"| P95 延迟 (s) | {p95_lo:.1f} | {p95_mean:.1f} | {p95_hi:.1f} | ≤ 180 |",
        f"| 硬约束通过率 | {pass_lo * 100:.1f}% | {pass_mean * 100:.1f}% | {pass_hi * 100:.1f}% | ≥ 90% |",
        f"| 上游错误率 | {err_lo * 100:.1f}% | {err_mean * 100:.1f}% | {err_hi * 100:.1f}% | ≤ 5% |",
        f"| 单次成本 (¥) | {cost_lo:.4f} | {cost_mean:.4f} | {cost_hi:.4f} | ≤ 0.08 |",
        "",
        "阈值取在观测最大值之外并留余量，不是「希望达到」的数字。"
        "P95 最差 171.8s 来自红队集的 30 例长白天用例，因此 180s 只留约 5% 余量，",
        "**任何 prompt 变复杂或模型变慢都会立刻击穿它**——这是有意的，让它成为能报警的线。",
        "",
        "## 为什么不是 30 秒",
        "",
        "路线图早期写过「95% 请求在 30s 内返回」作为 SLO 示例。实测表明：",
        f"主基线 P50 最小也有 {p50_lo:.1f}s，P95 最大 {p95_hi:.1f}s。",
        "那个目标从写下起就不可能达成，用它做告警只会让人学会忽略告警。",
        "因此 SLO 必须由快照推出，且随模型/供应商变更重新校准（见 [slo.md](slo.md)）。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    expected = render(collect())
    if args.check:
        actual = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if actual.strip() != expected.strip():
            print("slo baseline drift: run --write", file=sys.stderr)
            return 1
        print("slo-baseline gate: OK")
        return 0
    if args.write:
        OUT.write_text(expected, encoding="utf-8")
        print(f"wrote {OUT}")
        return 0
    print(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
