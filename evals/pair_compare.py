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


def noninferior(
    pass_diff: dict[str, float], margin_pp: float
) -> tuple[bool, float]:
    """Can we rule out a drop larger than `margin_pp` percentage points?

    This is a one-sided non-inferiority check on the paired bootstrap CI:
    pass only when the lower bound is above -margin. A wide CI that includes
    a large drop fails, even if the point estimate looks flat.
    """
    margin = margin_pp / 100.0
    return pass_diff["ci_low"] > -margin, margin


def build_report(
    a_name: str,
    b_name: str,
    pass_diff: dict[str, float],
    score_diff: dict[str, float],
    regression_margin_pp: float | None = None,
) -> str:
    def verdict(stats: dict[str, float]) -> str:
        if stats["mean_diff"] == 0 and stats["ci_low"] == 0 and stats["ci_high"] == 0:
            return "无差异（配对完全一致）"
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
    if regression_margin_pp is not None:
        ok, _ = noninferior(pass_diff, regression_margin_pp)
        lines += [
            "",
            f"非劣判定（允许下降 {regression_margin_pp:g}pp）："
            f"**{'通过' if ok else '未通过'}**。",
            "非劣只说明「没有证据表明下降超过该幅度」；区间越宽，结论越弱。"
            "要证明更小的差异，用 `--repeat` 或扩样本，不要缩 margin 硬凑。",
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
    parser.add_argument(
        "--regression-margin-pp",
        type=float,
        default=None,
        help="non-inferiority margin in percentage points (e.g. 3); exit 0 only if the lower CI is above -margin",
    )
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
        a_doc.get("model", str(args.a)),
        b_doc.get("model", str(args.b)),
        pass_diff,
        score_diff,
        args.regression_margin_pp,
    )
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(report)
    if args.regression_margin_pp is not None:
        ok, _ = noninferior(pass_diff, args.regression_margin_pp)
        return 0 if ok else 1
    significant = pass_diff["ci_low"] > 0 or pass_diff["ci_high"] < 0
    return 0 if significant else 1


if __name__ == "__main__":
    raise SystemExit(main())
