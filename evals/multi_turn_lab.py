"""Measure the follow-up layer on its own: no model, no clock, no cost.

The 80-case suite asks one question -- "given a complete brief, is the plan
good?" -- and it is answered well. Real conversations ask a different one:
after the first plan, does the assistant still understand what the client
means? `followup.py` answers that with a keyword whitelist and never calls a
model, which is cheap and predictable, but nobody has measured where the
whitelist stops working.

This lab runs each case's turns through `apply_followup` and checks two things
per case: did the final brief land on the fields a human would expect, and did
the last turn get recognised (or correctly refused). Because nothing is
sampled, a run is deterministic: a score here is a property of the rules, not
of a model's mood.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shotplan.domain.models import PlanRequest  # noqa: E402
from shotplan.followup import apply_followup  # noqa: E402

DEFAULT_CASES = REPO_ROOT / "evals" / "multi_turn" / "cases.jsonl"
DEFAULT_REPORT = REPO_ROOT / "docs" / "multi-turn.md"


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_no}: invalid JSON: {exc}") from exc
        if not case.get("turns") or "expect" not in case:
            raise ValueError(f"{path.name}:{line_no}: case needs turns and expect")
        cases.append(case)
    if not cases:
        raise ValueError(f"no multi-turn cases found in {path}")
    return cases


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    turns = case["turns"]
    request = PlanRequest(**turns[0]["request"]).normalized()
    said: list[str] = []
    last_recognized = True

    for turn in turns[1:]:
        result = apply_followup(request, turn["say"])
        request = result.request
        last_recognized = result.recognized
        said.append(
            "{}  ->  {}".format(
                turn["say"], "；".join(result.changes) or "（未识别）"
            )
        )

    mismatches: list[str] = []
    for key, want in case["expect"].items():
        if key == "preferences_contains":
            if want not in request.preferences:
                mismatches.append(
                    f"preferences 缺 {want!r}（实际 {request.preferences}）"
                )
            continue
        got = getattr(request, key, None)
        if isinstance(got, list) or isinstance(want, list):
            if list(got or []) != list(want):
                mismatches.append(f"{key}: 期望 {want}，实际 {got}")
        elif got != want:
            mismatches.append(f"{key}: 期望 {want}，实际 {got}")

    want_recognized = bool(case.get("expect_recognized", True))
    if last_recognized != want_recognized:
        mismatches.append(
            "最后一轮识别：期望 {}，实际 {}".format(
                "识别" if want_recognized else "拒答",
                "识别" if last_recognized else "拒答",
            )
        )

    final = request.to_dict()
    # request_id is regenerated on every normalization, so it would make the
    # report differ from itself between two identical runs.
    final.pop("request_id", None)

    return {
        "id": case["id"],
        "kind": case.get("kind", ""),
        "description": case.get("description", ""),
        "said": said,
        "final": final,
        "mismatches": mismatches,
        "pass": not mismatches,
    }


def render(results: list[dict[str, Any]], *, silent: bool) -> str:
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    lines = [
        "# 多轮追问能力实测",
        "",
        f"用例 **{total}** 条，一次通过 **{passed}** 条"
        f"（**{passed / total * 100:.0f}%**）。",
        "",
        "本页由 `evals/multi_turn_lab.py` 生成。被测对象是 `followup.py` 的"
        "确定性规则，不调用任何模型，因此结果可复现、可归因：失败即规则缺口。",
        "",
        "## 逐例结果",
        "",
        "| 用例 | 类型 | 结果 | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        mark = "通过" if r["pass"] else "**失败**"
        lines.append(
            f"| {r['id']} | {r['kind']} | {mark} | {r['description']} |"
        )

    failures = [r for r in results if not r["pass"]]
    if failures:
        lines += ["", "## 失败明细", ""]
        for r in failures:
            lines.append(f"### {r['id']} · {r['description']}")
            lines.append("")
            for sentence in r["said"]:
                lines.append(f"- {sentence}")
            for item in r["mismatches"]:
                lines.append(f"- **{item}**")
            lines.append("")
            lines.append(
                "最终 brief：`{}`".format(
                    json.dumps(r["final"], ensure_ascii=False, sort_keys=True)
                )
            )
            lines.append("")

    lines += [
        "",
        "## 口径",
        "",
        "- 一条用例通过 = `expect` 里每个字段都对，且最后一轮的「识别/拒答」符合预期。",
        "- 中间轮次不单独计分，只要求最终状态正确：多轮的价值在于收敛，不在于每句都改对。",
        "- 拒答也是正确答案之一：说不清的需求被硬塞进某个字段，比让用户改表单更糟。",
        "",
    ]
    body = "\n".join(lines)
    if not silent:
        print(body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="多轮追问能力实测（不调模型）")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--write", action="store_true", help="把报告写入磁盘")
    parser.add_argument("--json", action="store_true", help="输出逐例 JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="报告与当前规则不一致时失败。守的是「报告没漂移」，不是「用例全过」",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    results = [run_case(case) for case in cases]
    body = render(results, silent=args.json or args.check)

    if args.check:
        if not args.report_path.exists():
            print(f"缺少报告 {args.report_path}，先运行 --write", file=sys.stderr)
            return 2
        on_disk = args.report_path.read_text(encoding="utf-8")
        if on_disk.strip() != body.strip():
            print(
                "报告已过期：规则变了，但 "
                f"{args.report_path.relative_to(REPO_ROOT)} 没跟着更新。"
                "运行 py -3.12 -m evals.multi_turn_lab --write",
                file=sys.stderr,
            )
            return 2
        print("多轮报告与当前规则一致", file=sys.stderr)
        return 0

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    if args.write:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(body, encoding="utf-8")
        print(f"wrote {args.report_path}", file=sys.stderr)

    passed = sum(1 for r in results if r["pass"])
    print(f"\n{passed}/{len(results)} 通过", file=sys.stderr)
    # Writing a report is a file operation: it succeeded even when cases fail,
    # otherwise "the report was written" and "the rules are incomplete" would
    # share one exit code and the caller could not tell them apart.
    if args.write:
        return 0
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
