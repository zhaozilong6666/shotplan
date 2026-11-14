"""Answer: what does the repair loop actually rescue, and what does it not?

Aggregates every committed real-model snapshot. The loop is expensive (an extra
LLM call), so "it fixed 3 things" is not enough: we need to know which failure
types are worth retrying and which should fail fast.

    python -m evals.repair_attribution --write
    python -m evals.repair_attribution --check
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "repair-attribution.md"
SNAPSHOTS = ROOT / "evals"


def _snapshots() -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    for path in sorted(SNAPSHOTS.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and "hard_pass_rate" in data:
            found.append((path.name, data))
    return found


def _repairs_of(case: dict) -> int:
    return int(case.get("repairs", case.get("max_repairs", 0)) or 0)


def _passed(case: dict) -> bool:
    if "hard_pass" in case:
        return bool(case["hard_pass"])
    return float(case.get("pass_rate", 0.0)) >= 1.0


def build() -> str:
    snapshots = _snapshots()
    fixed: Counter = Counter()
    failed: Counter = Counter()
    rounds: dict[int, list[bool]] = defaultdict(list)
    coded_cases = 0

    for _, data in snapshots:
        fixed.update(data.get("repair_fixed_codes") or {})
        failed.update(data.get("repair_failed_codes") or {})
        for case in data.get("per_case", []):
            rounds[_repairs_of(case)].append(_passed(case))
            if "initial_hard_codes" in case:
                coded_cases += 1

    total_cases = sum(len(data.get("per_case", [])) for _, data in snapshots)
    repaired_cases = sum(sum(bool(_repairs_of(c)) for c in data.get("per_case", []))
                         for _, data in snapshots)

    lines = [
        "# 修复循环归因",
        "",
        "> 由 `python -m evals.repair_attribution --write` 从所有已归档快照生成；`--check` 是门禁。",
        "",
        "修复一次要多花一次 LLM 调用。所以问题不是「修了几次」，而是",
        "**哪些失败值得重试、哪些应该直接 fail fast**。",
        "",
        "## 数据覆盖",
        "",
        "| 快照 | 用例 | 触发修复 | 有逐例错误码 |",
        "| --- | ---: | ---: | --- |",
    ]
    for name, data in snapshots:
        cases = data.get("per_case", [])
        has_codes = "initial_hard_codes" in (cases[0] if cases else {})
        lines.append(
            f"| `{name}` | {len(cases)} | "
            f"{sum(bool(_repairs_of(c)) for c in cases)} | {'是' if has_codes else '否（旧快照）'} |"
        )

    lines += [
        "",
        f"合计 {len(snapshots)} 份快照、{total_cases} 条逐例记录、{repaired_cases} 条触发过修复，",
        f"其中带错误码归因的观察共 {sum(fixed.values()) + sum(failed.values())} 条"
        f"（一份修复可能同时有多个初始问题，所以不等于用例数）。",
        "",
        "## 按初始失败类型",
        "",
        "| 初始硬问题 | 修好 | 没修好 | 修复成功率 | 判断 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    codes = sorted(set(fixed) | set(failed), key=lambda c: (-(fixed[c] + failed[c]), c))
    for code in codes:
        ok, no = fixed[code], failed[code]
        rate = ok / (ok + no) if (ok + no) else 0.0
        if ok + no < 3:
            verdict = "样本太少，不下结论"
        elif rate == 1.0:
            verdict = "稳定救回"
        elif rate == 0.0:
            verdict = "**救不了，建议 fail fast**"
        else:
            verdict = "部分有效，值得重试"
        lines.append(f"| `{code}` | {ok} | {no} | {rate * 100:.0f}% | {verdict} |")

    if not codes:
        lines.append("| — | 0 | 0 | — | 尚无带错误码的快照 |")

    lines += [
        "",
        "## 按修复轮次",
        "",
        "| 修复次数 | 用例 | 最终通过 | 通过率 |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for repairs in sorted(rounds):
        flags = rounds[repairs]
        lines.append(
            f"| {repairs} | {len(flags)} | {sum(flags)} | "
            f"{sum(flags) / len(flags) * 100:.1f}% |"
        )

    stuck = [code for code in codes if failed[code] > 0 and fixed[code] == 0]
    lines += [
        "",
        "## 结论与限度",
        "",
        (
            "**没有任何一种失败被证明「完全救不了」**"
            if not stuck
            else "目前「只失败、从未救回」的类型：" + "、".join(f"`{c}`" for c in stuck)
        )
        + "。",
        "",
        "- 结论只覆盖真实模型跑过的少量修复事件；早期快照没有记录初始错误码，不能倒推。",
        "- 「修好」= 该用例最终通过硬约束，不等于修复是唯一原因，也不等于方案质量变好。",
        "- 单模型、单温度、单供应商；换模型后成功率需重新测。",
        "- 样本量小于 3 的类型一律不下结论——小样本上的 100% 和 0% 都不可信。",
        "",
        "## 怎么让这张表变可信",
        "",
        "下一次真实评测会自动带上 `initial_hard_codes` / `final_issue_codes`（已加入 `per_case`）。",
        "积累到每类 ≥10 次触发后，再考虑把「救不了」的类型接进 fail-fast 策略。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="aggregate repair outcomes")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    expected = build()
    if args.check:
        actual = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if actual.strip() != expected.strip():
            print("repair-attribution drift: run --write", file=sys.stderr)
            return 1
        print("repair-attribution gate: OK")
        return 0
    if args.write:
        OUT.write_text(expected, encoding="utf-8")
        print(f"wrote {OUT}")
        return 0
    print(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
