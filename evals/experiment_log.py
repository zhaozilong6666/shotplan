"""Turn "did this produce anything?" into a number instead of a feeling.

Every experiment gets a line before it runs: the question, the change, the
expectation. After it runs, the observed value and a verdict. The point is not
bookkeeping -- it is that a prediction written down first cannot be edited into
correctness afterwards, which is the failure mode of every narrative changelog.

The aggregate the report cares about is the refutation rate. A log with no
refutations is not a log of good judgement; it is a log of experiments designed
to succeed, because only execution was ever at risk. The interesting entries are
the ones where a belief was measured and lost.

Deterministic: same ledger, same report, so the gate can hold the page to its
data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "evals" / "experiments.jsonl"
DEFAULT_REPORT = ROOT / "docs" / "experiments.md"

VERDICTS = ("confirmed", "refuted", "partial", "open")
VERDICT_LABELS = {
    "confirmed": "确认",
    "refuted": "**推翻**",
    "partial": "部分",
    "open": "待结",
}
AREA_NAMES = {
    "light": "光线排程",
    "cost": "成本与延迟",
    "prompt": "提示词",
    "rules": "约束规则",
    "ops": "运行参数",
    "provider": "模型通道",
    "data": "知识库数据",
    "measure": "测量方法",
    "multi_turn": "多轮交互",
    "docs": "文档治理",
}
REQUIRED = ("id", "date", "area", "question", "expect", "verdict")


def load(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_no}: invalid JSON: {exc}") from exc
        missing = [key for key in REQUIRED if not row.get(key)]
        if missing:
            raise ValueError(f"{path.name}:{line_no}: missing {', '.join(missing)}")
        if row["id"] in seen:
            raise ValueError(f"{path.name}:{line_no}: duplicate id {row['id']}")
        if row["verdict"] not in VERDICTS:
            raise ValueError(
                f"{path.name}:{line_no}: verdict must be one of {VERDICTS}"
            )
        evidence = row.get("evidence")
        if evidence and not (ROOT / evidence).exists():
            # A conclusion nobody can check is a rumour. This is the one field
            # that makes the ledger auditable.
            raise ValueError(
                f"{path.name}:{line_no}: evidence not found: {evidence}"
            )
        seen.add(row["id"])
        rows.append(row)
    if not rows:
        raise ValueError(f"no experiments found in {path}")
    return rows


def area_table(rows: list[dict[str, Any]]) -> list[tuple[str, int, int, int, int]]:
    buckets: dict[str, list[str]] = {}
    for row in rows:
        buckets.setdefault(row["area"], []).append(row["verdict"])
    table = []
    for area, verdicts in buckets.items():
        table.append(
            (
                area,
                len(verdicts),
                verdicts.count("confirmed"),
                verdicts.count("refuted"),
                verdicts.count("partial"),
            )
        )
    return sorted(table, key=lambda item: (-item[1], item[0]))


def calibration(rows: list[dict[str, Any]]) -> list[str]:
    """Say what the refutation rate means, not just what it is."""
    decided = [row for row in rows if row["verdict"] != "open"]
    if not decided:
        return ["还没有已结案的实验。"]
    refuted = sum(1 for row in decided if row["verdict"] == "refuted")
    rate = refuted / len(decided)

    if len(decided) < 5:
        return [
            f"已结案 {len(decided)} 条，样本太少，比例不构成结论，只作为记录。",
        ]

    lines = []
    if rate < 0.25:
        lines.append(
            f"推翻率 **{rate:.0%}**（{refuted}/{len(decided)}）。"
            "这个数字偏低通常不是因为判断准，而是因为大部分实验只测"
            "「改动有没有效果」，没测「方向对不对」——前者容易成立，后者才可能被推翻。"
        )
        lines.append("")
        lines.append(
            "如果台账里长期没有方向性的否决，说明它在记录执行，不在检验判断。"
            "下一次应该写一条**自己不确定、且可能被证伪**的假设。"
        )
    elif rate > 0.6:
        lines.append(
            f"推翻率 **{rate:.0%}**（{refuted}/{len(decided)}）。"
            "大部分假设都错了，说明对系统的直觉还不牢；此时读实验记录比读结论更有用——"
            "每一条推翻都是一次认知修正。"
        )
    else:
        lines.append(
            f"推翻率 **{rate:.0%}**（{refuted}/{len(decided)}）。"
            "这个区间说明假设有一定的信息量：既不是全部应验，也不是全部落空。"
        )
    lost = [row for row in decided if row["verdict"] == "refuted"]
    if lost:
        lines.append("")
        lines.append("被推翻的假设（最值得重读的几条）：")
        lines.append("")
        for row in lost:
            lines.append(
                f"- **{row.get('id', '?')}** {row.get('question', '')} — "
                f"原以为 {row.get('expect', '')}"
            )
    return lines


def render(rows: list[dict[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda row: (row["date"], row["id"]), reverse=True)
    total = len(ordered)
    counts = {verdict: sum(1 for r in ordered if r["verdict"] == verdict) for verdict in VERDICTS}
    cost = sum(float(row.get("cost_cny") or 0) for row in ordered)

    lines = [
        "# 实验台账",
        "",
        "由 `evals/experiment_log.py` 生成，数据源 `evals/experiments.jsonl`。",
        "每条实验在动手**之前**写下预期，做完填实测与判定。先写预测是为了让事后无法改口——",
        "叙事型 changelog 的通病就是每条改动都「效果显著」。",
        "",
        "## 摘要",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 记录实验 | {total} 条 |",
        f"| 确认 | {counts['confirmed']} 条（{counts['confirmed'] / total:.0%}） |",
        f"| 推翻 | {counts['refuted']} 条（{counts['refuted'] / total:.0%}） |",
        f"| 部分成立 | {counts['partial']} 条 |",
        f"| 待结 | {counts['open']} 条 |",
        f"| 台账内累计花费 | ¥{cost:.2f} |",
        "",
        "## 校准：预测有多准",
        "",
    ]
    lines += calibration(ordered)
    lines += [
        "",
        "## 按方向的产出",
        "",
        "| 方向 | 实验数 | 确认 | 推翻 | 部分 | 读法 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    grouped = area_table(ordered)
    for area, n, ok, bad, part in grouped:
        if n < 2:
            reading = "单条，不下结论"
        elif bad == 0 and part == 0:
            reading = "方向成立：全部达预期"
        elif bad == 0:
            reading = f"部分成立：{part} 条未完全达预期"
        elif ok + part >= n * 0.6:
            reading = "多数成立，但有明确反例"
        else:
            reading = "方向存疑：多数实验没能支持预期"
        lines.append(
            f"| {AREA_NAMES.get(area, area)} | {n} | {ok} | {bad} | {part} | {reading} |"
        )

    spread = sum(1 for _, n, _, _, _ in grouped if n >= 3)
    if spread == 0:
        lines += [
            "",
            f"**没有哪个方向累积到 3 条实验**（共 {len(grouped)} 个方向、{total} 条记录）。"
            "这不是优点：说明工作是在各处试探，而不是在一条线上逐步否定。"
            "下一步该选一个方向，连续写三条能被推翻的假设。",
        ]

    lines += [
        "",
        "## 全部实验（按日期倒序）",
        "",
        "| ID | 日期 | 方向 | 问题 | 预期 | 实测（指标：起 → 止） | 判定 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in ordered:
        if row.get("metric") and row.get("to") is not None:
            start = "—" if row.get("from") is None else row["from"]
            observed = f"{row['metric']}：{start} → {row['to']}"
        elif row.get("to") is not None:
            observed = str(row["to"])
        else:
            observed = "见证据文档"
        lines.append(
            f"| {row['id']} | {row['date']} | {AREA_NAMES.get(row['area'], row['area'])} "
            f"| {row['question']} | {row['expect']} | {observed} "
            f"| {VERDICT_LABELS[row['verdict']]} |"
        )

    open_rows = [row for row in ordered if row["verdict"] == "open"]
    lines += ["", "## 待结", ""]
    if not open_rows:
        lines += ["无。", ""]
    else:
        for row in open_rows:
            lines.append(f"- **{row['id']}** {row['question']}（{row['date']}）")
        lines.append("")

    lines += [
        "## 证据索引",
        "",
        "每条实验的 `evidence` 字段必须指向一个真实文件，脚本会校验；"
        "点不动的结论不写进台账。",
        "",
    ]
    for row in ordered:
        if row.get("evidence"):
            lines.append(f"- {row['id']} → [{row['evidence']}](../{row['evidence']})")
    lines += [
        "",
        "## 口径与已知偏差",
        "",
        "- **判定是人事后填的**，不是算法算的。算法只负责校验证据存在、汇总比例、"
        "以及指出「推翻率长期为零」这种结构性问题。",
        "- **样本少时比例无意义**：少于 5 条只记录，不给结论（脚本会自动降级措辞）。",
        "- **成本只记台账内实测花费**，不含离线实验与本次对话。",
        "- **`from`/`to` 是单值对比**，不含置信区间；有区间的实验以 evidence 文档为准。",
        "",
    ]
    return "\n".join(lines)


def next_id(rows: list[dict[str, Any]]) -> str:
    """Next free E-number, zero-padded so the ledger sorts the way it reads."""
    used = [int(row["id"][1:]) for row in rows if row["id"][:1] == "E" and row["id"][1:].isdigit()]
    return f"E{max(used, default=0) + 1:02d}"


def save(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def open_entry(args: argparse.Namespace, rows: list[dict[str, Any]]) -> int:
    """Record the prediction before the measurement exists.

    The two refusals come from E08, which sat open for three days: an expectation
    containing 「或」 names two possible metrics, and a metric of `None` means
    nobody can say afterwards whether it was met. Both are rejected at the moment
    of writing, because a warning raised here is worth more than a report nobody
    opens at the right moment.
    """
    if "或" in args.expect:
        print("预测里含「或」：两个可能指标等于没有指标，只留一个", file=sys.stderr)
        return 2
    if not args.metric:
        print("必须给 --metric：没有指标，跑完也不知道该填什么", file=sys.stderr)
        return 2
    if args.area not in AREA_NAMES:
        print(f"未知方向 {args.area!r}，可选：{', '.join(AREA_NAMES)}", file=sys.stderr)
        return 2
    row = {
        "id": next_id(rows),
        "date": args.date or dt.date.today().isoformat(),
        "area": args.area,
        "question": args.question,
        "change": args.change,
        "expect": args.expect,
        "metric": args.metric,
        "from": args.start,
        "to": None,
        "verdict": "open",
        "evidence": None,
        "cost_cny": float(args.cost or 0),
    }
    rows.append(row)
    save(args.ledger, rows)
    print(
        f"{row['id']} 已登记为待结。跑完后："
        f"python -m evals.experiment_log --close {row['id']} "
        "--verdict <confirmed|refuted|partial> --to <实测值> --evidence <文件>"
    )
    return 0


def close_entry(args: argparse.Namespace, rows: list[dict[str, Any]]) -> int:
    """Fill in the measurement and the verdict, once, with the evidence on disk."""
    match = [row for row in rows if row["id"] == args.close]
    if not match:
        print(f"台账里没有 {args.close}", file=sys.stderr)
        return 2
    row = match[0]
    if row["verdict"] != "open":
        # Re-judging is allowed, but it has to be a visible edit: silently
        # flipping a refuted result is how a ledger becomes a press release.
        print(f"{args.close} 已经是 {row['verdict']}；改判请手工编辑并写明原因", file=sys.stderr)
        return 2
    if args.verdict not in ("confirmed", "refuted", "partial"):
        print("--verdict 只能是 confirmed / refuted / partial", file=sys.stderr)
        return 2
    if not args.evidence:
        print("必须给 --evidence：点不动的结论不写进台账", file=sys.stderr)
        return 2
    if not (ROOT / args.evidence).exists():
        print(f"证据文件不存在：{args.evidence}", file=sys.stderr)
        return 2
    row["verdict"] = args.verdict
    row["evidence"] = args.evidence
    if args.end is not None:
        row["to"] = args.end
    if args.start is not None:
        row["from"] = args.start
    if args.cost is not None:
        row["cost_cny"] = float(args.cost)
    save(args.ledger, rows)
    print(f"{args.close} → {args.verdict}（证据：{args.evidence}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="实验台账：预测 → 实测 → 判定")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--open", action="store_true", help="登记一条待结实验（在动手之前）")
    parser.add_argument("--close", metavar="EID", default="", help="结案：填实测与判定")
    parser.add_argument("--area", default="")
    parser.add_argument("--question", default="")
    parser.add_argument("--expect", default="")
    parser.add_argument("--metric", default="")
    parser.add_argument("--change", default="")
    parser.add_argument("--date", default="", help="覆盖日期（测试用）")
    parser.add_argument("--verdict", default="")
    parser.add_argument("--from", dest="start", type=int, default=None)
    parser.add_argument("--to", dest="end", type=int, default=None)
    parser.add_argument("--evidence", default="")
    parser.add_argument("--cost", type=float, default=None)
    args = parser.parse_args(argv)

    try:
        rows = load(args.ledger)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.open or args.close:
        code = open_entry(args, rows) if args.open else close_entry(args, rows)
        if code == 0:
            # Recording and refreshing are one action; a ledger that needs a
            # second command to stay consistent is a ledger that drifts.
            args.report_path.write_text(render(load(args.ledger)), encoding="utf-8", newline="\n")
            print(f"已刷新 {args.report_path}", file=sys.stderr)
        return code

    body = render(rows)

    if args.check:
        if not args.report_path.exists():
            print(f"缺少 {args.report_path}，先运行 --write", file=sys.stderr)
            return 2
        if args.report_path.read_text(encoding="utf-8").strip() != body.strip():
            print(
                "实验台账页已过期：台账变了。运行 "
                "py -3.12 -m evals.experiment_log --write",
                file=sys.stderr,
            )
            return 2
        print("实验台账页与 ledger 一致", file=sys.stderr)
        return 0

    if args.write:
        args.report_path.write_text(body, encoding="utf-8")
        print(f"wrote {args.report_path} ({len(body)} chars)", file=sys.stderr)
        return 0

    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
