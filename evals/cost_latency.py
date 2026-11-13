"""Cost and latency simulation over the real eval suite.

Runs every case through a simulated provider for several price cards, then
reports tokens, estimated cost and latency percentiles. This validates the
reporting pipeline and produces a budget estimate; it does NOT measure a vendor.

Usage:
    python -m evals.cost_latency --write-report
    python -m evals.cost_latency            # print summary only
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evals import OUT_DIR
from evals.run_eval import load_cases
from evals.scorers import aggregate, evaluate_case, failed_case
from evals.simulated_provider import PriceCard, SimulatedProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs" / "cost-latency-simulation.md"

CARDS = [
    PriceCard("gpt-4o-mini", 0.0011, 0.0043, 800),
    PriceCard("deepseek-chat", 0.0020, 0.0080, 1200),
    PriceCard("gpt-4o", 0.0180, 0.0720, 2500),
]


def run_card(cases: list[dict[str, Any]], card: PriceCard) -> dict[str, Any]:
    provider = SimulatedProvider(card)
    outcomes = []
    for case in cases:
        try:
            result = run_plan(
                PlanRequest.from_dict(case["request"]), provider=provider, max_repairs=1
            )
            outcomes.append(evaluate_case(case, result))
        except Exception as exc:
            outcomes.append(
                failed_case(case, "simulated", f"{type(exc).__name__}: {exc}")
            )
    metrics = aggregate(outcomes)
    metrics["model"] = card.model
    metrics["price_in_per_1k"] = card.price_in_per_1k
    metrics["price_out_per_1k"] = card.price_out_per_1k
    metrics["configured_latency_ms"] = card.latency_ms
    generated = [o for o in outcomes if o.status == "ok"]
    metrics["provider_calls"] = len(generated)
    metrics["cost_per_call_cny"] = (
        round(metrics["total_cost_cny"] / len(generated), 6) if generated else 0.0
    )
    return metrics


def build_report(rows: list[dict[str, Any]], case_count: int) -> str:
    lines = [
        "# 成本与延迟模拟报告",
        "",
        "> 这是一份**配置驱动的模拟**，不是真实模型测量。token 由离线 provider 估算，"
        "延迟是价格卡里的配置值；接入真实模型后请用同样的表格记录实测值。",
        "",
        f"同一批 **{case_count}** 个用例（含无法满足的 brief，它们不调用 provider、成本为 0）。",
        "",
        "## 价格卡与结果",
        "",
        "| 模型 | 输入价 /1K | 输出价 /1K | 模拟延迟 | 调用次数 | token in / out | "
        "估算总成本 | 单次成本 | P50 / P95 | 通过率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['model']}` | {row['price_in_per_1k']} | {row['price_out_per_1k']} | "
            f"{row['configured_latency_ms']} ms | {row['provider_calls']} | "
            f"{row['total_tokens_in']} / {row['total_tokens_out']} | "
            f"{row['total_cost_cny']} 元 | {row['cost_per_call_cny']} 元 | "
            # Latency is a PRICE-CARD CONFIGURATION, not a measurement. Measured
            # p50/p95 include this machine's pipeline overhead, which can tip
            # round() from 1200 to 1201 and break the showcase diff. Display the
            # configured value twice: byte-identical across runners (ADR 0008).
            f"{row['configured_latency_ms']} / {row['configured_latency_ms']} ms | "
            f"{row['hard_pass_rate']:.0%} |"
        )

    lines += [
        "",
        "## 预算换算（估算）",
        "",
        "| 模型 | 每天 100 次 | 每月按 22 天 |",
        "| --- | ---: | ---: |",
    ]
    for row in rows:
        daily = round(row["cost_per_call_cny"] * 100, 4)
        lines.append(f"| `{row['model']}` | {daily} 元 | {round(daily * 22, 4)} 元 |")

    lines += [
        "",
        "## 这验证了什么",
        "",
        "1. 成本从 provider 的 token 与价格卡一路传到 `trace`、评测聚合、报告与运行统计，没有断点。",
        "2. 延迟的 P50/P95 聚合与分组在真实模型接入前就已经可用。",
        "3. prompt 版本通过 `PriceCard.prompt_version` 进入每次 trace，"
        "接真实模型后可直接对比 v1/v2 的性价比。",
        "4. 预算换算能在花真钱之前给出量级，避免第一次跑评测就把额度烧完。",
        "",
        "## 边界（重要）",
        "",
        "- token 数是离线 provider 的字符估算，不是厂商 tokenizer 的结果；真实成本会有偏差。",
        "- 延迟是配置值，不含网络、排队、限流与重试；表格里的 P50/P95 直接显示该配置值，",
        "不叠加本机流水线开销。实测 p50/p95 仍保留在 `evals/out/cost-latency.json` 用于验证聚合链路，",
        "但跨机器不可比，也不进展示页一致性门禁。",
        "- 质量列（通过率/均分）来自规则 provider，与成本无关；真实模型的质量通常更低，"
        "这正是 `docs/eval-report.md` 要在接模型后重跑的原因。",
        "- 结论只能用于「量级与链路验证」，不能用于对外宣称某模型性价比。",
        "",
        "## 复现",
        "",
        "```bash",
        "python -m evals.cost_latency --write-report",
        "```",
        "",
        "报告与 `evals/out/cost-latency.json` 均由代码生成，请勿手工编辑。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="shotplan cost/latency simulation")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "cost-latency.json")
    args = parser.parse_args(argv)

    cases = load_cases()
    rows = [run_card(cases, card) for card in CARDS]
    payload = {"case_count": len(cases), "cards": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_report:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(build_report(rows, len(cases)), encoding="utf-8")

    print(f"cases: {len(cases)}")
    for row in rows:
        print(f"  {row['model']:<14} calls={row['provider_calls']:<3} "
              f"cost={row['total_cost_cny']:<9} per-call={row['cost_per_call_cny']:<8} "
              f"p50/p95={row['p50_duration_ms']}/{row['p95_duration_ms']} ms")
    if args.write_report:
        print(f"report: {args.report_path}")
    print(f"raw: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
