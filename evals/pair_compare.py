"""Paired comparison of two evaluation snapshots on the same case set.

Comparing two single runs by their headline numbers is the mistake this module
exists to prevent: with n=69 the detectability floor is ~8pp, so "A is 3pp
better than B" is usually noise. Pairing by case removes case difficulty from
the difference, and the CI says whether what is left survives resampling.

    python -m evals.pair_compare --a evals/real-model.json --b evals/real-model-v3.json

Exit code is 0 when the difference is distinguishable from zero, 1 when it is
not, and 2 on bad input — so it can gate a prompt change once both sides exist.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any

ROUNDS = 5000
SEED = 20261113


def _case_rows(snapshot: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Normalise both snapshot shapes to {case_id: {pass, score}}.

    Single-run snapshots store one outcome per case; repeat-mode snapshots
    store the per-case mean and pass rate. Both reduce to the same two numbers.
    """
    rows: dict[str, dict[str, float]] = {}
    for case in snapshot.get("per_case", []):
        if case.get("group") == "infeasible":
            continue
        if "pass_rate" in case:
            rows[case["id"]] = {
                "pass": float(case["pass_rate"]),
                "score": float(case["mean_score"]),
            }
        else:
            rows[case["id"]] = {
                "pass": 1.0 if case.get("hard_pass") else 0.0,
                "score": float(case.get("score", 0.0)),
            }
    return rows


def paired_diff(
    a: dict[str, dict[str, float]],
    b: dict[str, dict[str, float]],
    field: str,
    rounds: int = ROUNDS,
    seed: int = SEED,
) -> dict[str, float]:
    """Mean of (b - a) per case, with a percentile bootstrap CI."""
    shared = sorted(set(a) & set(b))
    if not shared:
        raise ValueError("no shared case ids")
    deltas = [b[cid][field] - a[cid][field] for cid in shared]
    rng = random.Random(seed)
    means = []
    n = len(deltas)
    for _ in range(rounds):
        total = 0.0
        for _ in range(n):
            total += deltas[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return {
        "n": float(n),
        "mean_diff": statistics.fmean(deltas),
        "ci_low": means[int(0.025 * rounds)],
        "ci_high": means[int(0.975 * rounds) - 1],
        "wins": float(sum(1 for d in deltas if d > 0)),
        "losses": float(sum(1 for d in deltas if d < 0)),
    }


def build_report(
    a_name: str, b_name: str, pass_diff: dict[str, float], score_diff: dict[str, float]
) -> str:
    def verdict(stats: dict[str, float]) -> str:
        if stats["ci_low"] > 0:
            return "B 更好（区间不跨 0）"
        if stats["ci_high"] < 0:
            return "A 更好（区间不跨 0）"
        return "测不出差异（区间跨 0）"

    lines = [
        "# 配对比较",
        "",
        f"A：`{a_name}` ｜ B：`{b_name}` ｜ 共同用例：{int(pass_diff['n'])}",
        "",
        "| 指标 | 差值 (B−A) | 95% CI | 胜负 | 结论 |",
        "| --- | ---: | ---: | ---: | --- |",
        f"| 硬约束通过率 | {pass_diff['mean_diff'] * 100:+.1f} pp | "
        f"[{pass_diff['ci_low'] * 100:+.1f}, {pass_diff['ci_high'] * 100:+.1f}] pp | "
        f"{int(pass_diff['wins'])} 胜 / {int(pass_diff['losses'])} 负 | {verdict(pass_diff)} |",
        f"| 平均得分 | {score_diff['mean_diff']:+.4f} | "
        f"[{score_diff['ci_low']:+.4f}, {score_diff['ci_high']:+.4f}] | "
        f"{int(score_diff['wins'])} 胜 / {int(score_diff['losses'])} 负 | {verdict(score_diff)} |",
        "",
        "区间跨 0 时不要写「B 略好」：那只是这次抽样里 B 恰好高一点。",
    ]
    return "\n".join(lines)


def _utf8_console() -> None:
    """Windows pipes default to cp1252; the report is Chinese.

    Without this a redirected stdout raises UnicodeEncodeError before the exit
    code can be read, which is what the Windows CI job caught.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="paired comparison of two eval snapshots")
    parser.add_argument("--a", type=Path, required=True)
    parser.add_argument("--b", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None, help="write markdown instead of stdout")
    args = parser.parse_args(argv)

    if not args.a.exists() or not args.b.exists():
        print("missing snapshot", file=sys.stderr)
        return 2
    a_doc = json.loads(args.a.read_text(encoding="utf-8"))
    b_doc = json.loads(args.b.read_text(encoding="utf-8"))
    a_rows, b_rows = _case_rows(a_doc), _case_rows(b_doc)
    only_a = sorted(set(a_rows) - set(b_rows))
    only_b = sorted(set(b_rows) - set(a_rows))
    if only_a or only_b:
        print(
            f"case sets differ: only in A={only_a[:5]} only in B={only_b[:5]}",
            file=sys.stderr,
        )
        return 2

    pass_diff = paired_diff(a_rows, b_rows, "pass")
    score_diff = paired_diff(a_rows, b_rows, "score")
    report = build_report(
        a_doc.get("model", str(args.a)), b_doc.get("model", str(args.b)), pass_diff, score_diff
    )
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(report)
    significant = pass_diff["ci_low"] > 0 or pass_diff["ci_high"] < 0
    return 0 if significant else 1


if __name__ == "__main__":
    raise SystemExit(main())
