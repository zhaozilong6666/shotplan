"""Render docs/pipeline-contract.md from the stage table in pipeline.py.

The diagram is generated, not hand-drawn, because a hand-drawn diagram is a
second source of truth and drifts silently. `--check` fails when the checked-in
document no longer matches the code; that is the gate.

Usage:
    python scripts/build_pipeline_doc.py --write
    python scripts/build_pipeline_doc.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from shotplan.agents.openai_compatible import BACKOFF_BASE_S, MAX_ATTEMPTS  # noqa: E402
from shotplan.config import Settings  # noqa: E402
from shotplan.pipeline import STAGES, TERMINALS  # noqa: E402

DOC_PATH = ROOT / "docs" / "pipeline-contract.md"


def mermaid() -> str:
    lines = ["```mermaid", "flowchart TD", "    START([\"PlanRequest 已归一化\"]) --> preflight"]
    for stage in STAGES:
        lines.append(f'    {stage.name}["{stage.name}<br/>{stage.title}"]')
    for name in ("ok", "infeasible", "error"):
        lines.append(f'    {name.upper()}(["status={name}"])')
    for stage in STAGES:
        if stage.on_success in TERMINALS:
            lines.append(f"    {stage.name} -->|通过| {stage.on_success.upper()}")
        else:
            lines.append(f"    {stage.name} -->|通过| {stage.on_success}")
        if stage.on_failure in TERMINALS:
            lines.append(f"    {stage.name} -.->|失败| {stage.on_failure.upper()}")
        else:
            lines.append(f"    {stage.name} -.->|不通过| {stage.on_failure}")
    lines += [
        "    classDef terminal fill:#e8f0ff,stroke:#4a6fa5,stroke-width:1px;",
        "    class OK,INFEASIBLE,ERROR terminal;",
        "```",
    ]
    return "\n".join(lines)


def worst_case() -> dict[str, float]:
    """Compose the ceiling from the constants the code actually uses."""
    cfg = Settings()
    attempts = 1 + cfg.max_repairs
    http_calls = attempts * MAX_ATTEMPTS
    backoff = attempts * BACKOFF_BASE_S * (1 + 2)
    worst = http_calls * cfg.llm_timeout + backoff
    return {
        "max_repairs": cfg.max_repairs,
        "attempts": attempts,
        "max_attempts_per_call": MAX_ATTEMPTS,
        "http_calls": http_calls,
        "llm_timeout": cfg.llm_timeout,
        "backoff_s": round(backoff, 1),
        "worst_case_s": round(worst, 1),
        "budget_s": cfg.pipeline_budget_s,
    }


def build() -> str:
    wc = worst_case()
    parts: list[str] = [
        "# 流水线契约：状态、输入输出与失败降级",
        "",
        "> 由 `python scripts/build_pipeline_doc.py --write` 从 `src/shotplan/pipeline.py` "
        "的 `STAGES` 生成。**不要手改**：CI 会比对生成结果与本文，漂移即失败。",
        "",
        "## 先说清楚这是什么、不是什么",
        "",
        "这是一个**确定性的有向流水线**，不是自由循环的 agent：每一步做什么、失败后去哪里、"
        "什么时候停，都在代码里写死。没有「模型决定下一步调哪个工具」这一层，也就没有"
        "「模型陷入死循环」这一类故障。",
        "",
        "这个选择是刻意的：本项目要证明的是「方案可执行、质量可回归」，"
        "而不是「能自主编排工具」。多轮 tool calling 会引入一个新的不确定性来源，"
        "却不会让硬约束校验更可信。所以这里**没有 Tool Registry，也没有 Agent Executor**——"
        "不是漏了，是判断不需要。",
        "",
        "真正需要防的「停不下来」有两种，都在下面处理：",
        "",
        "1. **修复循环**：`max_repairs` 计数上限 + `(hard_pass, score)` 严格变优才接受；",
        "2. **单次调用过慢**：provider 内部 3 次尝试 × 超时，叠起来可能几分钟——"
        "用**墙钟预算**兜底，超预算就带着当前最优草稿降级返回，不无限等。",
        "",
        "## 执行链路",
        "",
        mermaid(),
        "",
        "终态：",
        "",
    ]
    for name, description in TERMINALS.items():
        parts.append(f"- **`status={name}`** —— {description}")
    parts += [
        "",
        "## 每个节点的契约",
        "",
        "| 节点 | 输入 | 输出 | 成功去往 | 失败去往 | 重试 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for stage in STAGES:
        parts.append(
            f"| `{stage.name}` | {', '.join(stage.inputs)} | {', '.join(stage.outputs)} | "
            f"`{stage.on_success}` | `{stage.on_failure}` | {'是' if stage.retryable else '否'} |"
        )
    parts.append("")
    for stage in STAGES:
        parts += [
            f"### `{stage.name}` — {stage.title}",
            "",
            "- 输入：",
            "```json",
            json.dumps(stage.inputs, ensure_ascii=False, indent=2),
            "```",
            "- 输出：",
            "```json",
            json.dumps(stage.outputs, ensure_ascii=False, indent=2),
            "```",
            f"- 说明：{stage.notes}",
            "",
        ]
    parts += [
        "## 最坏情况延迟是可以算出来的",
        "",
        "这些数字由代码里的常量算出来，不是拍脑袋写的：",
        "",
        "```",
        f"单次请求的模型调用次数上限 = 1 + max_repairs                = 1 + {wc['max_repairs']} = {wc['attempts']}",
        f"每次调用的 HTTP 尝试上限   = MAX_ATTEMPTS                   = {wc['max_attempts_per_call']}",
        f"HTTP 调用总数上限          = {wc['attempts']} × {wc['max_attempts_per_call']} = {int(wc['http_calls'])}",
        f"每次尝试的超时             = SHOTPLAN_LLM_TIMEOUT           = {wc['llm_timeout']}s",
        f"退避总和（0.5s + 1s 每轮）  = {wc['backoff_s']}s",
        f"最坏墙钟                   = {int(wc['http_calls'])} × {wc['llm_timeout']}"
        f" + {wc['backoff_s']} = {wc['worst_case_s']}s",
        f"墙钟预算                   = SHOTPLAN_PIPELINE_BUDGET_S     = {wc['budget_s']}s",
        "```",
        "",
        "结论：默认配置下最坏情况约 "
        f"**{wc['worst_case_s']}s**，远大于预算 {wc['budget_s']}s。所以预算**先于**重试上限生效："
        "第 3 次 HTTP 尝试还没跑完，流水线就已经决定不再开始新的修复轮次。"
        "只数次数不设预算，等于把延迟交给「provider 有多慢」决定。",
        "",
        "预算耗尽时不是报错退出，而是：",
        "",
        "1. 停止开启新的修复轮次；",
        "2. 返回**当前最优草稿**；",
        "3. 在校验报告里追加一条 `REPAIR_BUDGET_EXCEEDED` 软问题，并在 trace 里记 "
        "`budget.exhausted=true`。",
        "",
        "只有连第一次生成都没开始就超预算，才会返回 `status=error` + "
        "`DEADLINE_EXCEEDED`——那时没有任何可用草稿可以降级。",
        "",
        "## 这张表怎么保证不漂移",
        "",
        "1. **文档由代码生成**：`python scripts/build_pipeline_doc.py --check` 比对生成结果"
        "与本文，CI 里跑。改了 `STAGES` 不改文档，门禁红。",
        "2. **运行时事件必须来自这张表**：`tests/test_pipeline_contract.py` 跑通正常、不可满足、"
        "provider 失败、解析失败、预算耗尽五条路径，断言 trace 里的每个事件名要么是声明的节点，"
        "要么是 `repair_N` / `budget`。加了步骤不声明，测试红。",
        "3. **转移目标必须存在**：测试断言每个 `on_success` / `on_failure` 都指向已声明的节点或终态，"
        "防止拼错一个字符串就多出一条永远走不到的死路。",
        "",
        "## 已知边界",
        "",
        "- 预算只能**在节点之间**检查。一次已经发出的 HTTP 请求不能被预算中断，"
        "它仍然受 `llm_timeout` 约束。要做硬中断得在 provider 层加取消机制，当前没有。",
        "- 修复轮次之间不重试「同一个草稿」：每轮都是一次新的生成，成本按轮次线性增长。",
        "- 离线 mock provider 下这些上限都不会触发，所以预算逻辑由单测用人工时钟/极小预算覆盖，"
        "而不是靠评测集碰巧跑到。",
        "",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="generate or verify the pipeline contract doc")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    rendered = build()
    if args.check:
        if not DOC_PATH.exists():
            print(f"PIPELINE-DOC GATE FAILED: {DOC_PATH} does not exist")
            return 1
        current = DOC_PATH.read_text(encoding="utf-8")
        if current != rendered:
            print("PIPELINE-DOC GATE FAILED: docs/pipeline-contract.md is stale")
            print("  run: python scripts/build_pipeline_doc.py --write")
            return 1
        print("pipeline-doc gate: OK")
        return 0

    DOC_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {DOC_PATH}")
    if not args.write:
        print("(pass --write to make this explicit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
