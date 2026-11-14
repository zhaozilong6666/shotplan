"""Generate docs/rule-map.md from the rule registry, the failure corpus and the tests.

The point is to make "we have a rule for that" checkable: every rule should
say what it protects, how often it actually fired, and which test would fail if
someone deleted it. Rules with no test are listed as gaps instead of being
quietly forgotten.

    python scripts/build_rule_map.py --write
    python scripts/build_rule_map.py --check
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shotplan.domain.constraints import RULES  # noqa: E402

OUT = ROOT / "docs" / "rule-map.md"
FAILURES = ROOT / "evals" / "failures" / "cases.jsonl"
REAL = ROOT / "evals" / "real-model.json"


def _test_index() -> list[tuple[str, str, str]]:
    """(file, function, source) for every test function in the suite."""
    found: list[tuple[str, str, str]] = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                found.append((path.name, node.name, ast.get_source_segment(source, node) or ""))
    return found


# A test that merely lists a code among many is weaker evidence than one that
# asserts on it. Ranking beats filtering here: when a rule has no dedicated
# test, showing the generic one is still better than showing nothing.
_GENERIC = {
    "test_failure_records_are_complete",
    "test_rules_config_history_and_persistence",
    "test_soft_rules_fire",
}


def _score(file: str, name: str, code: str) -> int:
    score = 0
    if code.lower() in name.lower():
        score += 4
    if file == "test_validator.py":
        score += 3
    if name in _GENERIC:
        score -= 4
    return score


def _failure_counts() -> Counter:
    counts: Counter = Counter()
    if FAILURES.exists():
        for line in FAILURES.read_text(encoding="utf-8").splitlines():
            if line.strip():
                counts[json.loads(line)["code"]] += 1
    return counts


def _real_counts() -> dict[str, int]:
    if not REAL.exists():
        return {}
    snapshot = json.loads(REAL.read_text(encoding="utf-8"))
    return snapshot.get("top_issue_codes", {})


def build() -> str:
    tests = _test_index()
    failures = _failure_counts()
    real = _real_counts()

    lines = [
        "# 规则地图",
        "",
        "> 由 `python scripts/build_rule_map.py --write` 从 "
        "`src/shotplan/domain/constraints.py`、失败库与测试生成。",
        "> `--check` 会把它当门禁：规则加了但没更新文档，CI 会红。",
        "",
        "每条规则要能回答三个问题：守的是什么、真的触发过吗、删掉它哪个测试会红。",
        "",
        "| 规则 | 严重度 | 构造失败库 | 真实模型触发 | 覆盖测试 | 为什么存在 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    gaps: list[str] = []
    for rule in RULES:
        code = rule["code"]
        matches = sorted(
            (
                (_score(file, name, code), f"`{file}::{name}`")
                for file, name, body in tests
                if code in body
            ),
            key=lambda pair: -pair[0],
        )
        covering = [label for _, label in matches[:2]]
        if not covering:
            gaps.append(code)
        lines.append(
            f"| `{code}` | {rule['severity']} | {failures.get(code, 0)} | "
            f"{real.get(code, 0)} | {', '.join(covering) or '—'} | {rule['why']} |"
        )

    lines += ["", "## 缺口", ""]
    if gaps:
        lines.append(
            f"{len(gaps)} 条规则没有任何测试直接引用它们："
            + "、".join(f"`{code}`" for code in gaps)
            + "。它们的正确性目前只由端到端用例间接保证。"
        )
    else:
        lines.append("每条规则都至少有一个测试直接引用。")

    lines += [
        "",
        "## 怎么读这两列触发次数",
        "",
        "- **构造失败库**：由 `naive` planner 生成的坏方案里，这条规则抓到多少次（69 个用例，527 条记录）。",
        "- **真实模型触发**：`evals/real-model.json` 里头约束通过率那一轮的统计；"
        "`0` 可能是没触发，也可能是该轮样本里没出现（69 例，检测不到低频问题）。",
        "",
        "两列都不能当作「规则重要性」的排名：高频可能只是因为 `naive` 特别容易犯这个错。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build the rule map")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    expected = build()
    if args.check:
        actual = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if actual.strip() != expected.strip():
            print("rule-map drift: run python scripts/build_rule_map.py --write", file=sys.stderr)
            return 1
        print("rule-map gate: OK")
        return 0
    if args.write:
        OUT.write_text(expected, encoding="utf-8")
        print(f"wrote {OUT}")
        return 0
    print(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
