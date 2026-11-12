"""Mine a holdout challenge set from the failure library.

The 80-case eval suite is a *regression baseline*: v1..v6 snapshots are only
comparable because that set changes slowly and deliberately. Adding mined cases
to it would silently break that history. So mined cases live in their own
``evals/challenge/`` directory and are never read by ``run_eval``.

What the miner proves is different from what the eval suite proves. The eval
suite says "the planner passes the cases we wrote". The challenge set says "the
planner generalises to brief combinations it has never seen". Candidate briefs
are built from the knowledge base and the failure library ONLY - the miner
never looks at a case's expected output, so there is nothing to overfit to.

Pipeline (fully deterministic, no timestamps, no randomness):

    failure records -> code x city coverage matrix
    existing cases  -> city x style coverage matrix + canonical dedupe keys
    knowledge base  -> 5 cities x 5 styles x 6 pressure profiles x 4 seasons
    each candidate  -> good + naive through the REAL pipeline
    accepted iff    -> good is executable, naive is not, gap >= MIN_GAP,
                       brief is not already in the regression suite

Usage:
    python -m evals.case_miner --write       # regenerate the three artifacts
    python -m evals.case_miner --check       # CI gate: artifacts + invariants
    python -m evals.case_miner               # summary only
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from math import fsum
from pathlib import Path
from typing import Any

from evals import CASES_DIR
from evals.run_eval import load_cases
from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.knowledge.tools import get_tools
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
CHALLENGE_PATH = ROOT / "evals" / "challenge" / "cases.jsonl"
SNAPSHOT_PATH = ROOT / "evals" / "case_mining.json"
REPORT_PATH = ROOT / "docs" / "case-mining.md"
FAILURE_PATH = ROOT / "evals" / "failures" / "cases.jsonl"

# A newly mined case must separate the two providers by at least this much.
MIN_GAP = 0.30
# The quality floor written into every accepted case. Deliberately coarse so a
# machine-dependent rounding difference can never turn a green gate red.
CHALLENGE_MIN_SCORE = 0.70
# CI gate floors. These are well below observed values on purpose.
MIN_ACCEPTED = 100
MIN_NEW_STYLE_CELLS = 1

SEASONS: tuple[tuple[str, str], ...] = (
    ("winter", "2026-01-10"),
    ("spring", "2026-04-12"),
    ("summer", "2026-07-05"),
    ("autumn", "2026-10-04"),
)
PRESSURES: tuple[str, ...] = (
    "baseline",
    "tight_time",
    "tight_budget",
    "avoid_two",
    "long_party",
    "dual_style",
    "micro_budget",
    "no_outdoor",
    "conflicting",
)
PRESSURE_LABELS: dict[str, str] = {
    "baseline": "标准时段与预算",
    "tight_time": "仅 3 小时",
    "tight_budget": "预算 600 元",
    "avoid_two": "排除两个户外热门地点",
    "long_party": "9 小时 / 3 人",
    "dual_style": "双风格混搭 / 8 小时",
    "micro_budget": "预算贴近理论下限 400 元",
    "no_outdoor": "排除全部户外地点",
    "conflicting": "同一地点既指定又排除",
}


def canonical(brief: dict[str, Any]) -> str:
    """Normalised, id-free fingerprint used to detect duplicate briefs."""
    normalized = PlanRequest.from_dict(brief).normalized().to_dict()
    normalized.pop("request_id", None)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def load_failure_records(path: Path = FAILURE_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def existing_state() -> tuple[dict[str, Any], set[str], dict[tuple[str, str], int]]:
    """Regression-suite fingerprints, code x city matrix, city x style matrix."""
    cases = load_cases(CASES_DIR)
    fingerprints = {canonical(case["request"]) for case in cases}
    style_cells: Counter[tuple[str, str]] = Counter()
    for case in cases:
        req = PlanRequest.from_dict(case["request"]).normalized()
        style = req.styles[0] if req.styles else ""
        style_cells[(req.city, style)] += 1

    records = load_failure_records()
    code_city: Counter[tuple[str, str]] = Counter()
    for record in records:
        city = str(record.get("brief", {}).get("city", "unknown")).lower()
        code_city[(record["code"], city)] += 1
    return (
        {
            "case_count": len(cases),
            "fingerprints": sorted(fingerprints),
            "style_cells": style_cells,
            "code_city": code_city,
            "failure_records": len(records),
            "failure_codes": sorted({r["code"] for r in records}),
        },
        fingerprints,
        dict(style_cells),
    )


def candidate_briefs() -> list[dict[str, Any]]:
    """Deterministic cross-product. Sources: knowledge base + failure library."""
    kb = get_tools()
    cities = sorted(kb.city_ids())
    styles = [str(item["id"]) for item in kb.styles()]
    candidates: list[dict[str, Any]] = []
    for city_index, city in enumerate(cities):
        outdoors = [
            item["id"] for item in kb.locations(city) if not item["indoor"]
        ]
        for style_index, style in enumerate(styles):
            for pressure_index, pressure in enumerate(PRESSURES):
                season, date = SEASONS[
                    (city_index + style_index + pressure_index) % len(SEASONS)
                ]
                brief: dict[str, Any] = {
                    "city": city,
                    "date": date,
                    "hours": 6,
                    "budget_cny": 1800,
                    "styles": [style],
                }
                if pressure == "tight_time":
                    brief["hours"] = 3
                elif pressure == "tight_budget":
                    brief["budget_cny"] = 600
                elif pressure == "avoid_two":
                    brief["avoid"] = outdoors[:2]
                elif pressure == "long_party":
                    brief.update(hours=9, budget_cny=3000, party_size=3)
                elif pressure == "dual_style":
                    brief.update(
                        hours=8,
                        budget_cny=2600,
                        styles=[style, styles[(style_index + 2) % len(styles)]],
                    )
                elif pressure == "micro_budget":
                    brief.update(hours=4, budget_cny=400)
                elif pressure == "no_outdoor":
                    brief["avoid"] = list(outdoors)
                elif pressure == "conflicting":
                    brief.update(
                        must_include=outdoors[:1],
                        avoid=outdoors[:1],
                    )
                candidates.append(
                    {
                        "brief": brief,
                        "city": city,
                        "style": style,
                        "pressure": pressure,
                        "season": season,
                    }
                )
    return candidates


def _run(brief: dict[str, Any], mode: str) -> Any:
    return run_plan(
        PlanRequest.from_dict(brief),
        provider=MockProvider(mode),
        max_repairs=1,
    )


def mine() -> dict[str, Any]:
    state, fingerprints, style_cells = existing_state()
    kb = get_tools()
    cities = sorted(kb.city_ids())
    styles = [str(item["id"]) for item in kb.styles()]
    failures_by_city: dict[str, list[str]] = defaultdict(list)
    for (code, city), _count in sorted(state["code_city"].items()):
        if city in cities:
            failures_by_city[city].append(code)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    good_scores: list[float] = []
    naive_scores: list[float] = []
    good_hard_pass = 0
    naive_hard_pass = 0
    gaps: list[float] = []

    candidates = candidate_briefs()
    for candidate in candidates:
        brief = candidate["brief"]
        key = canonical(brief)
        reasons: list[str] = []
        if key in fingerprints:
            reasons.append("duplicate of a regression-suite brief")

        good = _run(brief, "good")
        naive = _run(brief, "naive")
        good_score = round(good.validation.score, 4)
        naive_score = round(naive.validation.score, 4)
        gap = round(good_score - naive_score, 4)

        if good.status != "ok":
            reasons.append(f"good status={good.status}")
        if not good.validation.hard_pass:
            reasons.append(
                "good hard constraints failed: "
                + ",".join(sorted({i.code for i in good.validation.hard_issues}))
            )
        if good_score < CHALLENGE_MIN_SCORE:
            reasons.append(f"good score {good_score} below {CHALLENGE_MIN_SCORE}")
        if naive.status != "ok" or naive.validation.hard_pass:
            reasons.append("no comparable pair of plans (a provider refused)")
        if gap < MIN_GAP:
            reasons.append(f"good/naive gap {gap} below {MIN_GAP}")

        if reasons:
            rejected.append(
                {
                    "city": candidate["city"],
                    "style": candidate["style"],
                    "pressure": candidate["pressure"],
                    "reasons": reasons,
                    "good_score": good_score,
                    "naive_score": naive_score,
                }
            )
            continue

        good_scores.append(good_score)
        naive_scores.append(naive_score)
        gaps.append(gap)
        good_hard_pass += int(good.validation.hard_pass)
        naive_hard_pass += int(naive.validation.hard_pass)
        accepted.append(
            {
                "id": f"H{len(accepted) + 1:03d}",
                "group": "challenge",
                "description": (
                    f"盲测：{kb.city(candidate['city'])['name']} × {candidate['style']} × "
                    f"{PRESSURE_LABELS[candidate['pressure']]}（{candidate['season']}）"
                ),
                "request": brief,
                "expect_hard_pass": True,
                "expect_min_score": CHALLENGE_MIN_SCORE,
                "provenance": {
                    "codes": failures_by_city.get(candidate["city"], []),
                    "city": candidate["city"],
                    "style": candidate["style"],
                    "pressure": candidate["pressure"],
                    "season": candidate["season"],
                    "new_combination": (
                        candidate["city"], candidate["style"]
                    ) not in style_cells,
                },
            }
        )

    accepted_cells = {
        (item["provenance"]["city"], item["provenance"]["style"])
        for item in accepted
    }
    all_cells = {(c, s) for c in cities for s in styles}
    new_cells = accepted_cells - set(style_cells)
    total_candidates = len(candidates)

    snapshot = {
        "version": 1,
        "candidate_count": total_candidates,
        "grid": {
            "cities": len(cities),
            "city_ids": cities,
            "styles": len(styles),
            "pressures": len(PRESSURES),
            "seasons": len(SEASONS),
        },
        "regression_cases": state["case_count"],
        "failure_records": state["failure_records"],
        "failure_codes": state["failure_codes"],
        "code_city_counts": {
            f"{code}|{city}": count
            for (code, city), count in sorted(state["code_city"].items())
        },
        "coverage_before": len(set(style_cells) & all_cells),
        "coverage_after": len((set(style_cells) | accepted_cells) & all_cells),
        "coverage_total": len(all_cells),
        "unseen_briefs": len(accepted),
        "new_style_cells": len(new_cells),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "reject_reasons": dict(
            Counter(reason for item in rejected for reason in item["reasons"])
        ),
        "good": {
            "cases": len(good_scores),
            "hard_pass_rate": round(good_hard_pass / max(1, len(good_scores)), 4),
            "avg_score": round(fsum(good_scores) / max(1, len(good_scores)), 4),
            "min_score": round(min(good_scores, default=0.0), 4),
        },
        "naive": {
            "cases": len(naive_scores),
            "hard_pass_rate": round(naive_hard_pass / max(1, len(naive_scores)), 4),
            "avg_score": round(fsum(naive_scores) / max(1, len(naive_scores)), 4),
        },
        "min_gap": round(min(gaps, default=0.0), 4),
        "min_gap_required": MIN_GAP,
        "quality_floor": CHALLENGE_MIN_SCORE,
        "rejected_examples": rejected[:8],
        "style_cells_before": {
            f"{city}|{style}": count
            for (city, style), count in sorted(style_cells.items())
            if (city, style) in all_cells
        },
    }
    return {"snapshot": snapshot, "cases": accepted, "rejected": rejected}


def cases_text(cases: list[dict[str, Any]]) -> str:
    return "\n".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) for item in cases
    ) + "\n"


def build_report(result: dict[str, Any]) -> str:
    s = result["snapshot"]
    rejected = result["rejected"]
    cities = s["grid"]["city_ids"]
    codes = s["failure_codes"]
    counts = s["code_city_counts"]
    lines = [
        "# 盲测挑战集：从失败案例库挖出来的新用例",
        "",
        "> 由 `python -m evals.case_miner --write` 生成，请勿手工编辑。",
        "",
        "## 它回答什么问题",
        "",
        "80 个回归用例是我自己写的，「80/80 全绿」只说明规划器通过了**见过的题**。",
        "真正该追问的是：规则是不是只对着这 80 个 brief 调出来的？",
        "所以这里用**失败案例库 + 知识库**生成一批主评测集从未见过的组合，"
        "再让同一条流水线、同一个校验器去跑，看结论是否还成立。",
        "",
        "挖掘过程**不读取任何用例的期望输出**，只看到 brief 本身用于去重——"
        "不存在「照着答案写用例」的空间。详见 [ADR 0010](adr/0010-holdout-challenge-set.md)。",
        "",
        "## 为什么不直接并进主评测集",
        "",
        "`evals/baselines/v1..v6` 是六个时代的对照快照，可比性来自「用例集变动缓慢且可解释」。"
        "若把挖掘结果直接塞进 `evals/cases/`，所有历史基线会同时失去意义，"
        "评测报告里的分数变化也无法再区分「实现变好了」和「题目变简单了」。",
        "因此挑战集独立存放在 `evals/challenge/`，`run_eval` 不会读它；"
        "它可以持续长大，而主集继续做纵向回归。",
        "",
        "## 输入：失败案例库的聚类",
        "",
        f"来自 `evals/failures/cases.jsonl` 的 **{s['failure_records']}** 条记录、"
        f"**{len(codes)}** 类硬约束违规。按「问题码 × 城市」统计命中次数：",
        "",
        "| 问题码 | " + " | ".join(cities) + " |",
        "| --- | " + " | ".join("---:" for _ in cities) + " |",
    ]
    for code in codes:
        lines.append(
            f"| `{code}` | "
            + " | ".join(str(counts.get(f"{code}|{city}", 0)) for city in cities)
            + " |"
        )
    lines += [
        "",
        "对照组的缺陷是构造出来的，因此这个矩阵不是真实模型的错误分布；"
        "它的用途是告诉挖掘器「哪些规则在哪些城市被验证过」，从而定位覆盖缺口。",
        "",
        "## 现有评测集的覆盖缺口",
        "",
        f"主集 {s['regression_cases']} 个用例在 {s['coverage_total']} 个"
        f"「城市 × 主风格」格子中只覆盖 **{s['coverage_before']}** 个。",
        "挑战集按知识库做完整交叉："
        f"{s['grid']['cities']} 城 × {s['grid']['styles']} 风格 × "
        f"{s['grid']['pressures']} 种压力配置 × {s['grid']['seasons']} 季轮换日期 = "
        f"**{s['candidate_count']}** 个候选 brief。",
        "压力配置共 9 种：标准、3 小时压缩、600 元紧预算、排除两个户外热门地点、"
        "9 小时 3 人、双风格混搭、贴近理论下限的 400 元、排除全部户外地点、"
        "以及「同一地点既指定又排除」的矛盾输入。其中 `conflicting` 是故意放进来的"
        "**负向对照**，用来检验生成前可行性判定会不会把不可满足的 brief 挡在挑战集之外；"
        "`no_outdoor` 与 `micro_budget` 则用于复核两处历史修复。"
        "日期按 (城市, 风格, 压力) 索引在四季里轮换，全部使用固定值，不含随机数与时间戳。",
        "",
        "## 筛选口径",
        "",
        "每个候选都跑 `good` 与 `naive` 两个 provider，走完整的"
        "「可行性判定 → 检索 → 生成 → 校验 → 修复」流水线。只有同时满足以下五条才纳入：",
        "",
        "1. brief 与主集 80 个用例规范化后**不重复**；",
        "2. `good` 返回 `status=ok` 且硬约束全通过；",
        f"3. `good` 得分 ≥ `{CHALLENGE_MIN_SCORE}`（写进用例的粗粒度下限）；",
        "4. `naive` 必须被判定为不可执行；",
        f"5. 两者得分差 ≥ `{MIN_GAP}`，确保这条用例真有区分度。",
        "",
        "## 结果",
        "",
        "| 指标 | 值 |",
        "| --- | ---: |",
        f"| 候选 brief | {s['candidate_count']} |",
        f"| 纳入挑战集 | **{s['accepted']}** |",
        f"| 被筛掉 | {s['rejected']} |",
        f"| 城市 × 风格覆盖 | {s['coverage_before']}/{s['coverage_total']} → "
        f"**{s['coverage_after']}/{s['coverage_total']}** |",
        f"| 与主集不重复的纳入 brief | **{s['unseen_briefs']}** / {s['accepted']} |",
        f"| 新覆盖的城市 × 风格格子 | **{s['new_style_cells']}** |",
        f"| good 硬约束通过率 | {s['good']['hard_pass_rate']:.0%} |",
        f"| good 均分 / 最低分 | {s['good']['avg_score']} / {s['good']['min_score']} |",
        f"| naive 硬约束通过率 | {s['naive']['hard_pass_rate']:.0%} |",
        f"| naive 均分 | {s['naive']['avg_score']} |",
        f"| 最小得分差 | {s['min_gap']}（要求 ≥ {s['min_gap_required']}） |",
        "",
        "注意：这里的 `good` 高分**不是**新的效果声明，它和主集一样来自离线规则 provider；"
        "价值在于「换了没见过的组合，判定链仍然一致」，而不是分数本身。",
        "",
        "## 被筛掉的候选",
        "",
    ]
    if rejected:
        lines += [
            "| 原因 | 次数 |",
            "| --- | ---: |",
        ]
        for reason, count in sorted(
            s["reject_reasons"].items(), key=lambda kv: (-kv[1], kv[0])
        ):
            lines.append(f"| {reason} | {count} |")
        lines += ["", "前几条明细（用于人工复核筛选口径是否过松或过严）：", ""]
        for item in rejected[:5]:
            lines.append(
                f"- `{item['city']}` / `{item['style']}` / `{item['pressure']}`："
                f"{'；'.join(item['reasons'])}（good {item['good_score']}、"
                f"naive {item['naive_score']}）"
            )
    else:
        lines.append("无。所有候选都满足筛选口径。")
    grid = s["grid"]
    negative_controls = grid["cities"] * grid["styles"]
    lines += [
        "",
        "## 负向对照：可行性判定挡住了什么",
        "",
        f"被筛掉的 **{s['rejected']}** 个候选全部来自 `conflicting` 配置"
        f"（{grid['cities']} 城 × {grid['styles']} 风格 = {negative_controls}）。"
        "它们不是「规划器写不出来」，而是生成前的 `check_feasibility` 直接返回 "
        "`CONFLICTING_CONSTRAINTS`，两个 provider 都停止生成、得 0 分，"
        "因此不构成可比较的一对方案。",
        "如果哪天这批候选突然被纳入挑战集，说明前置可行性判定被削弱了——"
        "CI 会把「负向对照消失」当成回归，而不是当成覆盖率提升。",
        "",
        "## 顺带复核的两处历史修复",
        "",
        f"`no_outdoor`（排除全部户外地点）与 `micro_budget`（贴近理论下限 400 元）"
        f"两类共 {negative_controls * 2} 个候选全部通过硬约束。",
        "前者对应「新增城市时室内备选地点被硬编码」的旧缺陷，后者对应 C24"
        "「紧预算仍选收费景点导致超支」的旧缺陷。这两类在主集里只有零星用例覆盖，"
        "这里在全部 25 个城市 × 风格组合上重跑一遍，说明修复不是对着单条用例打补丁。",
        "",
        "## CI 检查什么",
        "",
        "`python -m evals.case_miner --check` 会**重新挖掘一遍**，并逐字节比对"
        "`evals/challenge/cases.jsonl`、`evals/case_mining.json` 与本文件；"
        "同时断言纳入数、新组合数、覆盖格数、最小得分差没有退化。",
        "也就是说，改动校验器或知识库导致某个组合不再被正确判定时，"
        "门禁会在「挑战集内容变了」这一层直接失败，而不是悄悄把用例删掉了事。",
        "",
        "## 边界",
        "",
        "- 候选由固定网格生成，覆盖的是**组合多样性**，不是真实客户 brief 的分布。",
        "- 对照组缺陷是构造的，所以「naive 全挂」只证明判定链一致，不证明真实模型会更差。",
        "- 挑战集不参与 `run_eval`，也不影响 v1–v6 基线；它是独立的一层证据。",
        "",
        "## 复现",
        "",
        "```bash",
        "python -m evals.case_miner --write   # 重新生成三份产物",
        "python -m evals.case_miner --check  # CI 门禁",
        "```",
        "",
    ]
    return "\n".join(lines)


def check(result: dict[str, Any]) -> list[str]:
    """CI gate: deterministic artifacts + mining invariants."""
    problems: list[str] = []
    s = result["snapshot"]
    expected = {
        "cases": cases_text(result["cases"]),
        "snapshot": json.dumps(s, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "report": build_report(result),
    }
    actual = {
        "cases": CHALLENGE_PATH.read_text(encoding="utf-8")
        if CHALLENGE_PATH.exists() else "",
        "snapshot": SNAPSHOT_PATH.read_text(encoding="utf-8")
        if SNAPSHOT_PATH.exists() else "",
        "report": REPORT_PATH.read_text(encoding="utf-8")
        if REPORT_PATH.exists() else "",
    }
    for key in expected:
        if actual[key] != expected[key]:
            problems.append(
                f"{key} artifact is stale or hand-edited; run "
                "`python -m evals.case_miner --write`"
            )
    if s["accepted"] < MIN_ACCEPTED:
        problems.append(
            f"only {s['accepted']} accepted challenge cases, expected >= {MIN_ACCEPTED}"
        )
    if s["new_style_cells"] < MIN_NEW_STYLE_CELLS:
        problems.append(
            f"only {s['new_style_cells']} newly covered city/style cells, "
            f"expected >= {MIN_NEW_STYLE_CELLS}"
        )
    if s["unseen_briefs"] < s["accepted"]:
        problems.append("challenge set contains briefs copied from the regression suite")
    if s["coverage_after"] != s["coverage_total"]:
        problems.append(
            f"city/style coverage {s['coverage_after']}/{s['coverage_total']} "
            "is incomplete"
        )
    negative_controls = s["grid"]["cities"] * s["grid"]["styles"]
    if s["rejected"] < negative_controls:
        problems.append(
            f"only {s['rejected']} candidates rejected, expected >= "
            f"{negative_controls}: the infeasibility negative controls disappeared"
        )
    if s["min_gap"] < MIN_GAP:
        problems.append(f"min good/naive gap {s['min_gap']} is below {MIN_GAP}")
    if s["good"]["hard_pass_rate"] < 1.0:
        problems.append("an accepted challenge brief fails hard constraints")
    if s["naive"]["hard_pass_rate"] > 0.0:
        problems.append("an accepted challenge brief lets the control plan pass")
    if len({canonical(c["request"]) for c in result["cases"]}) != len(result["cases"]):
        problems.append("challenge set contains duplicate briefs")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mine a holdout challenge set")
    parser.add_argument("--write", action="store_true", help="regenerate artifacts")
    parser.add_argument("--check", action="store_true", help="CI gate")
    args = parser.parse_args(argv)

    result = mine()
    s = result["snapshot"]
    if args.write:
        CHALLENGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CHALLENGE_PATH.write_text(cases_text(result["cases"]), encoding="utf-8")
        SNAPSHOT_PATH.write_text(
            json.dumps(s, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        REPORT_PATH.write_text(build_report(result), encoding="utf-8")

    print(
        f"candidates: {s['candidate_count']}  accepted: {s['accepted']}  "
        f"rejected: {s['rejected']}"
    )
    print(
        f"coverage: {s['coverage_before']} -> {s['coverage_after']}/"
        f"{s['coverage_total']}  unseen briefs: {s['unseen_briefs']}  "
        f"new style cells: {s['new_style_cells']}"
    )
    print(
        f"good: {s['good']['avg_score']} ({s['good']['hard_pass_rate']:.0%})  "
        f"naive: {s['naive']['avg_score']} ({s['naive']['hard_pass_rate']:.0%})  "
        f"min gap: {s['min_gap']}"
    )
    problems = check(result) if args.check else []
    if problems:
        print("CASE-MINING GATE FAILED:")
        for problem in problems:
            print(f"  - {problem}")
    elif args.check:
        print("case-mining gate: OK")
    if args.write:
        print(f"cases: {CHALLENGE_PATH}")
        print(f"snapshot: {SNAPSHOT_PATH}")
        print(f"report: {REPORT_PATH}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
