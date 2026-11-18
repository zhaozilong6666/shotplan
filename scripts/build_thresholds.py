"""Every tunable number in the rules, and whether anyone ever measured it.

The constants in `constraints.py` each carry a comment explaining why they hold
that value, and several of those comments cite a real run ("flagged 7 of 8
real-model failures", "C46"). That reasoning is in the right place -- next to the
number -- but it is unreadable as a set: nobody scrolls three files to ask "which
of these thresholds is a measurement and which is a guess".

This page answers that question mechanically. It reads the constants, keeps the
comment block above each one, scans where each is used, and separates the ones
whose comment cites a run from the ones that are product judgement. The label is
heuristic and says so: it looks for case ids, experiment ids and the phrase
"real-model". A constant can be a good guess without a citation, and this page
should not be read as claiming otherwise.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

SOURCE = ROOT / "src" / "shotplan" / "domain" / "constraints.py"
REPORT = ROOT / "docs" / "thresholds.md"

# Judgement words that mark a value as chosen rather than measured.
MEASURED = re.compile(r"\bC\d{1,3}\b|\bE\d{2}\b|real-model|实测|measured|压测")
SKIP = {"RULES", "REQUIRED_ACTIVITIES", "GOLDEN_LIGHT_WINDOWS", "SCORE_WEIGHTS",
        "MORNING_FINISH_WORDS", "DEADLINE_PREFERENCE"}


def comment_block(lines: list[str], lineno: int) -> str:
    """The contiguous comment lines directly above a definition."""
    collected: list[str] = []
    index = lineno - 2  # 0-based, one line above
    while index >= 0 and lines[index].lstrip().startswith("#"):
        collected.append(lines[index].lstrip().lstrip("#").strip())
        index -= 1
    return " ".join(reversed([part for part in collected if part]))


def literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def collect() -> list[dict[str, Any]]:
    text = SOURCE.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text)
    rows: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Name) or not re.fullmatch(r"[A-Z][A-Z0-9_]+", target.id):
                continue
            if target.id in SKIP:
                continue
            value = literal(node.value) if node.value is not None else None
            if value is None or isinstance(value, (dict, list, tuple)):
                continue
            note = comment_block(lines, node.lineno)
            rows.append(
                {
                    "name": target.id,
                    "value": value,
                    "note": note,
                    "measured": bool(MEASURED.search(note)),
                }
            )
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    measured = [row for row in rows if row["measured"]]
    chosen = [row for row in rows if not row["measured"]]

    lines = [
        "> 由 `python scripts/build_thresholds.py --write` 生成，请勿手工编辑。",
        "",
        "# 规则的每一个数字，以及它凭什么",
        "",
        "规则里可调的数不多，但每一个都能被追问「为什么是 60 不是 50」。"
        "这些理由就写在 `constraints.py` 每个常量的上方——位置是对的，但作为集合读不了："
        "没人会为了问「哪个阈值是量出来的、哪个是拍的」去翻三个文件。",
        "",
        f"下表从代码里读出 {len(rows)} 个标量常量（规则清单、枚举和正则不算），保留各自的注释，并标出注释里是否引用了"
        "实际运行（用例编号、实验编号或「real-model」字样）。**这个标注是启发式的**："
        "没有引用不等于拍脑袋，有引用也不等于测得充分。",
        "",
        "| 常量 | 值 | 依据 |",
        "| --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda item: (not item["measured"], item["name"])):
        label = "注释引用了实测" if row["measured"] else "设计选择"
        lines.append(f"| `{row['name']}` | {row['value']} | {label} |")
    lines.append("")

    lines += [
        f"## 有实测依据的（{len(measured)} 个）",
        "",
        "这些数字背后有一次具体的运行，失败模式是那条注释里写的那个。",
        "",
    ]
    if measured:
        for row in measured:
            lines.append(f"- **`{row['name']} = {row['value']}`** —— {row['note']}")
    else:
        lines.append("没有。")
    lines.append("")

    lines += [
        f"## 设计选择的（{len(chosen)} 个）",
        "",
        "这些是按经验定的初始值，没有针对性实验。它们不是缺陷——大多数产品参数就该先有值、"
        "再等失败来修正——但面试被问「凭什么」时，诚实的答案是「这是起点，E04/E06 正在动它」，"
        "而不是编一个来源。",
        "",
    ]
    for row in chosen:
        note = f"（{row['note']}）" if row["note"] else ""
        lines.append(f"- `{row['name']} = {row['value']}`{note}")
    lines.append("")

    lines += [
        "## 怎么把这个数变成量出来的",
        "",
        "改一个阈值之前，先用命令开一条实验、写下预测，再跑：",
        "",
        "```bash",
        "python -m evals.experiment_log --open --area rules --question ... --expect ... --metric ...",
        "```",
        "",
        "跑完 `--close` 时带上 `--rules` 标记，`kb-health.md` 的「被实验检验过门槛的规则」"
        "会增加一。这个数字目前很小，它应该小——16 条规则里只有两条的门槛被真正探过。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    rows = collect()
    text = render(rows)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    if "--check" in sys.argv or "--write" in sys.argv:
        if not REPORT.exists():
            print(f"缺少 {REPORT.name}，运行 --write", file=sys.stderr)
            return 1
        if REPORT.read_text(encoding="utf-8") != text:
            print(f"{REPORT.name} 与代码不一致，运行 --write", file=sys.stderr)
            return 1
    measured = sum(1 for row in rows if row["measured"])
    print(f"thresholds: {len(rows)} constants, {measured} cite a run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
