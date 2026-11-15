"""Prepare (not fabricate) the human-agreement study.

Phase 8 asks: does our score agree with human judgement? That requires two
people independently rating 30 plans. This kit does everything that does NOT
require humans: it builds a blind annotation set, emits two empty forms, and
validates the Cohen's-kappa calculator against cases with known answers.

It deliberately does NOT emit a kappa value for real data that does not exist.

    python -m evals.annotation_kit --write     # build blind set + forms + report
    python -m evals.annotation_kit --score A.csv B.csv
    python -m evals.annotation_kit --check     # gate: calculator self-test
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from shotplan.agents import MockProvider
from shotplan.config import Settings
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
ANNOT = ROOT / "evals" / "annotation"
OUT = ROOT / "docs" / "annotation-kappa.md"
N_ITEMS = 30
SEED = 20261115

# What a human rater sees. The validator verdict is intentionally absent from
# the form: showing it would anchor the rater and fake the agreement.
QUESTIONS = ("executable", "time_ok", "budget_ok", "usable")
QUESTION_LABELS = {
    "executable": "可执行性 1-5（1=完全不可行，5=完全可执行）",
    "time_ok": "时间轴是否无冲突（是/否）",
    "budget_ok": "是否不超预算（是/否）",
    "usable": "会不会真的按这份方案去拍（是/否）",
}


def build_items() -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    requests = [
        PlanRequest(city=city, date="2026-10-04", hours=hours,
                    budget_cny=budget, styles=[style])
        for city, hours, budget, style in (
            ("xian", 6, 1500, "tang"), ("xian", 3, 900, "song"),
            ("suzhou", 6, 1200, "song"), ("suzhou", 4, 700, "ming"),
            ("nanjing", 8, 1800, "ming"), ("nanjing", 3, 600, "republic"),
            ("hangzhou", 6, 1300, "weijin"), ("luoyang", 7, 1500, "tang"),
        )
        for _ in range(4)
    ]
    modes = ["good"] * (N_ITEMS // 2) + ["naive"] * (N_ITEMS // 2)
    rng.shuffle(modes)
    rng.shuffle(requests)
    items = []
    for index, (request, mode) in enumerate(zip(requests[:N_ITEMS], modes, strict=True), 1):
        result = run_plan(
            request, provider=MockProvider(mode),
            settings=Settings(provider="mock", max_repairs=0), max_repairs=0,
        )
        items.append({
            "item_id": f"A{index:02d}",
            "brief": request.to_dict(),
            "plan": result.plan.to_dict(),
            # Hidden from raters; kept so the eventual study can compare.
            "_validator_hard_pass": result.validation.hard_pass,
            "_validator_score": result.validation.score,
            "_source_mode": mode,
        })
    return items


def write_forms(items: list[dict[str, Any]]) -> None:
    ANNOT.mkdir(parents=True, exist_ok=True)
    (ANNOT / "items.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n",
        encoding="utf-8",
    )
    for rater in ("A", "B"):
        path = ANNOT / f"rater_{rater}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["item_id", *QUESTIONS])
            for item in items:
                writer.writerow([item["item_id"], "", "", "", ""])


def cohens_kappa(a: list[Any], b: list[Any]) -> float:
    """Unweighted Cohen's kappa. Raises on malformed input rather than guessing."""
    if len(a) != len(b) or not a:
        raise ValueError("two non-empty rating lists of equal length are required")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    a_counts, b_counts = Counter(a), Counter(b)
    expected = sum(a_counts[k] * b_counts[k] for k in set(a_counts) | set(b_counts)) / (n * n)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def quadratic_weighted_kappa(a: list[int], b: list[int], k: int = 5) -> float:
    """Weighted kappa for the 1-5 ordinal question; disagreement by 4 costs more."""
    if len(a) != len(b) or not a:
        raise ValueError("two non-empty rating lists of equal length are required")
    n = len(a)
    matrix = [[0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        matrix[x - 1][y - 1] += 1
    row = [sum(row_values) for row_values in matrix]
    col = [sum(matrix[i][j] for i in range(k)) for j in range(k)]
    denom = k - 1 or 1
    expected = sum(
        row[i] * col[j] * ((i - j) / denom) ** 2 for i in range(k) for j in range(k)
    ) / (n * n)
    observed = sum(
        matrix[i][j] * ((i - j) / denom) ** 2 for i in range(k) for j in range(k)
    ) / n
    if expected == 0:
        return 1.0
    return 1 - observed / expected


def score_forms(path_a: Path, path_b: Path) -> dict[str, Any]:
    def load(path: Path) -> dict[str, dict[str, str]]:
        with path.open(encoding="utf-8", newline="") as handle:
            return {row["item_id"]: row for row in csv.DictReader(handle)}

    ra, rb = load(path_a), load(path_b)
    common = sorted(set(ra) & set(rb))
    result: dict[str, Any] = {"n_items": len(common), "questions": {}}
    for question in QUESTIONS:
        a = [ra[i][question].strip() for i in common]
        b = [rb[i][question].strip() for i in common]
        if any(not value for value in a + b):
            result["questions"][question] = {"complete": False}
            continue
        if question == "executable":
            ai, bi = [int(v) for v in a], [int(v) for v in b]
            result["questions"][question] = {
                "complete": True,
                "kappa": round(cohens_kappa(ai, bi), 4),
                "quadratic_weighted_kappa": round(quadratic_weighted_kappa(ai, bi), 4),
            }
        else:
            result["questions"][question] = {
                "complete": True, "kappa": round(cohens_kappa(a, b), 4),
            }
    disagreements = [
        {"item_id": i, **{q: (ra[i][q], rb[i][q]) for q in QUESTIONS}}
        for i in common
        if any(ra[i][q].strip() != rb[i][q].strip() for q in QUESTIONS)
    ]
    result["disagreements"] = disagreements
    return result


def self_test() -> dict[str, Any]:
    """Validate the calculators against cases with known answers."""
    perfect_a = [1, 2, 3, 4, 5] * 4
    perfect_k = cohens_kappa(perfect_a, perfect_a)
    assert abs(perfect_k - 1.0) < 1e-9, perfect_k

    # A maximally discordant 2-category sample: observed=0, expected=0.5 -> -1.
    discord_a = ["是", "是", "否", "否"]
    discord_b = ["否", "否", "是", "是"]
    discord_k = cohens_kappa(discord_a, discord_b)
    assert abs(discord_k + 1.0) < 1e-9, discord_k

    weighted_perfect = quadratic_weighted_kappa(perfect_a, perfect_a)
    assert abs(weighted_perfect - 1.0) < 1e-9, weighted_perfect
    # A near-miss on an ordinal scale should beat a wild miss on weighted kappa.
    near = quadratic_weighted_kappa([1, 1, 2, 2], [1, 2, 2, 3])
    wild = quadratic_weighted_kappa([1, 1, 5, 5], [5, 5, 1, 1])
    assert near > wild, (near, wild)

    return {
        "perfect": round(perfect_k, 4),
        "discordant": round(discord_k, 4),
        "weighted_perfect": round(weighted_perfect, 4),
        "weighted_near": round(near, 4),
        "weighted_wild": round(wild, 4),
    }


def render(checks: dict[str, Any]) -> str:
    return "\n".join([
        "# 人类一致性标注工具（待真人数据）",
        "",
        "> 由 `python -m evals.annotation_kit --write` 生成；`--check` 验证计算器正确性。",
        "> **本报告不含 kappa 结论**：阶段 8 要求两人独立标注 30 个方案，",
        "> 没有真人数据就写一个数字是伪造证据。这里交付的是可执行的研究协议。",
        "",
        "## 已备好",
        "",
        f"- `evals/annotation/items.jsonl`：{N_ITEMS} 份方案（good / naive 各半，随机打乱），",
        "  含校验器判定但**不出现在标注表里**，避免锚定。",
        "- `evals/annotation/rater_A.csv` / `rater_B.csv`：两张空白标注表，四个问题：",
        "",
    ] + [f"  - `{q}` — {QUESTION_LABELS[q]}" for q in QUESTIONS] + [
        "",
        "## 计算器自检（已知答案）",
        "",
        "| 用例 | 结果 |",
        "| --- | ---: |",
        f"| 完全一致 → κ | {checks['perfect']} |",
        f"| 完全相反（2 类）→ κ | {checks['discordant']} |",
        f"| 有序量表完全一致 → 加权 κ | {checks['weighted_perfect']} |",
        f"| 相邻分歧 → 加权 κ | {checks['weighted_near']} |",
        f"| 跨越整个量表 → 加权 κ | {checks['weighted_wild']} |",
        "",
        "自检断言：完全一致 = 1、完全相反 = −1、相邻分歧的加权 κ 高于极端分歧。",
        "同时实现无序 Cohen's κ 与二次加权 κ（1–5 有序题用后者）。",
        "",
        "## 执行协议",
        "",
        "```bash",
        "# 1. 两位标注者各自独立填写 CSV，不互相讨论、不看校验器判定",
        "# 2. 回收后计算",
        "python -m evals.annotation_kit --score evals/annotation/rater_A.csv evals/annotation/rater_B.csv",
        "```",
        "",
        "输出逐题 κ、加权 κ、以及所有分歧案例（任何一题不一致即列出）。",
        "分歧案例不是噪声，是下一步改进校验器的证据来源：",
        "如果人一致认为不可执行而校验器放行，说明漏了一条硬规则；反之亦然。",
        "",
        "## 为什么现在不能勾掉阶段 8",
        "",
        "- κ 衡量的是**人与人的一致性**，不是人与模型的一致性；需要两个真人。",
        "- 30 份方案来自离线 mock，不是真实模型输出；上线后应用真实产出重做一轮。",
        "- 本报告只证明「计算器是对的」，不证明「评分和人的判断一致」。",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--score", nargs=2, metavar=("A_CSV", "B_CSV"))
    args = parser.parse_args(argv)

    checks = self_test()
    if args.score:
        print(json.dumps(score_forms(Path(args.score[0]), Path(args.score[1])),
                         ensure_ascii=False, indent=2))
        return 0

    expected = render(checks)
    if args.check:
        actual = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if actual.strip() != expected.strip():
            print("annotation-kit report drift: run --write", file=sys.stderr)
            return 1
        for form in ("rater_A.csv", "rater_B.csv"):
            if not (ANNOT / form).exists():
                print(f"annotation-kit gate: missing {form}", file=sys.stderr)
                return 1
        print("annotation-kit gate: OK (calculator + blind forms)")
        return 0
    if args.write:
        write_forms(build_items())
        OUT.write_text(expected, encoding="utf-8")
        print(f"wrote {N_ITEMS} blind items, 2 forms and {OUT}")
        return 0
    print(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
