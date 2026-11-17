"""Regression coverage for the cities the main suite does not touch.

E08 measured the gap. The 80-case suite covers five cities, the knowledge base
has eight, and the three added in November appear only in the holdout set --
which is mined once and never re-run by the gates. A rule regression in
Chengdu, Beijing or Kaifeng would therefore stay invisible until someone
happened to plan a trip there.

Merging twelve cases into the main suite was the obvious fix and the wrong one:
it would break the v1-v6 comparison that the 80-case baseline exists to
provide. So they live in their own directory and run on every gate, asserting
two things the main suite does not:

- a normal brief in each of the three cities is executable, not merely
  parseable;
- a brief whose required places cannot fit in the booked hours is refused
  before generation, instead of producing a plausible plan that quietly drops
  one of the places. That second assertion found a real hole: the feasibility
  check priced the cheapest single location but never summed the dwell time of
  several required ones.

Deterministic: same cases, same providers, same numbers, so `--check` can hold
the page to what the code actually does.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.run_eval import load_cases  # noqa: E402
from shotplan.agents.mock import MockProvider  # noqa: E402
from shotplan.domain.models import PlanRequest  # noqa: E402
from shotplan.pipeline import run_plan  # noqa: E402

CASES_DIR = ROOT / "evals" / "cases_city"
REPORT = ROOT / "docs" / "city-gap.md"
MIN_GOOD_MEAN_SCORE = 0.8

CITY_NAMES = {"chengdu": "成都", "beijing": "北京", "kaifeng": "开封"}


@dataclass
class Row:
    case_id: str
    city: str
    group: str
    expect_hard_pass: bool
    good_status: str
    good_hard: bool
    good_score: float
    naive_hard: bool
    naive_score: float
    issues: tuple[str, ...]

    @property
    def executable(self) -> bool:
        return self.good_status == "ok" and self.good_hard


def collect() -> list[Row]:
    rows: list[Row] = []
    for case in load_cases(CASES_DIR):
        request = PlanRequest(**case["request"])
        good = run_plan(request, provider=MockProvider(mode="good"))
        naive = run_plan(request, provider=MockProvider(mode="naive"))
        rows.append(
            Row(
                case_id=case["id"],
                city=case["request"]["city"],
                group=case.get("group", ""),
                expect_hard_pass=bool(case["expect_hard_pass"]),
                good_status=good.status,
                good_hard=bool(good.validation and good.validation.hard_pass),
                good_score=float(good.validation.score) if good.validation else 0.0,
                naive_hard=bool(naive.validation and naive.validation.hard_pass),
                naive_score=float(naive.validation.score) if naive.validation else 0.0,
                issues=tuple(sorted({issue.code for issue in (good.validation.issues if good.validation else [])})),
            )
        )
    return rows


def problems(rows: list[Row]) -> list[str]:
    found: list[str] = []
    for row in rows:
        if row.executable != row.expect_hard_pass:
            found.append(
                f"{row.case_id}: 期望 {'可执行' if row.expect_hard_pass else '拒绝'}，"
                f"实际 {row.good_status}/hard={row.good_hard}"
            )
        if row.naive_hard:
            found.append(f"{row.case_id}: naive 也通过了，这一条区分不出好坏方案")
    executable = [row for row in rows if row.expect_hard_pass]
    if executable:
        mean = sum(row.good_score for row in executable) / len(executable)
        if mean < MIN_GOOD_MEAN_SCORE:
            found.append(f"可执行用例的平均分 {mean:.4f} 低于下限 {MIN_GOOD_MEAN_SCORE}")
    return found


def render(rows: list[Row]) -> str:
    executable = [row for row in rows if row.expect_hard_pass]
    refused = [row for row in rows if not row.expect_hard_pass]
    mean = sum(row.good_score for row in executable) / len(executable) if executable else 0.0
    cities = sorted({row.city for row in rows})
    lines = [
        "> 由 `python -m evals.city_gap --write` 生成，请勿手工编辑。",
        "",
        "# 三城回归覆盖",
        "",
        "主回归集覆盖 5 城，知识库有 8 城。成都 / 北京 / 开封此前只出现在盲测集里，"
        "而盲测集由 `make mine` 一次生成、门禁不重跑，所以这三城的规则退化不会让 CI 变红。"
        "这套用例补的就是这一段，独立存放，不改变 80 例基线。",
        "",
        f"用例 {len(rows)} 条，覆盖 {len(cities)} 城（{'、'.join(CITY_NAMES.get(c, c) for c in cities)}）。"
        f"其中可执行 {len(executable)} 条、应拒绝 {len(refused)} 条；"
        f"可执行部分 good 平均分 **{mean:.4f}**，naive 硬通过 {sum(1 for r in rows if r.naive_hard)} 条。",
        "",
        "## 逐例结果",
        "",
        "| 用例 | 城市 | 场景 | 期望 | good 实际 | 分数 | naive |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for row in rows:
        expected = "可执行" if row.expect_hard_pass else "拒绝"
        actual = "可执行" if row.executable else f"拒绝（{row.good_status}）"
        naive = "通过（不应发生）" if row.naive_hard else f"{row.naive_score:.2f}"
        lines.append(
            f"| {row.case_id} | {CITY_NAMES.get(row.city, row.city)} | {row.group} | "
            f"{expected} | {actual} | {row.good_score:.4f} | {naive} |"
        )
    lines += [
        "",
        "## 这套用例抓到过什么",
        "",
        "写作时的第一版把「3 小时跑两个 90 分钟地点」写成「应当可执行」。"
        "实际跑下来 good 生成了一份只排进一个地点的方案，另一处必去地点直接消失，"
        "校验器报 `MISSING_REQUIRED_LOCATION`——方案看起来合理，但漏了用户明确要求的地方。",
        "",
        "原因是可行性判定只算了「最便宜的单地点」成本与「最短闭环」时长，"
        "没有把多个必去地点的停留时间加起来。补上 `TIME_BELOW_REQUIRED_LOCATIONS` 之后，"
        "这类 brief 在生成前就被拒绝，并说明还差多少分钟。",
        "",
        "## 为什么不做成主集的一部分",
        "",
        "主集的 80 例是 v1–v6 基线的比较基准。往里加 12 例会让所有历史数字失去可比性，"
        "而历史数字正是「这版有没有变差」的唯一依据。独立存放、独立门禁，两边都不牺牲。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    rows = collect()
    text = render(rows)
    found = problems(rows)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    if "--check" in sys.argv or "--write" in sys.argv:
        if REPORT.exists() and REPORT.read_text(encoding="utf-8") != text:
            found.append(f"{REPORT.name} 与当前输出不一致，运行 --write")
    print(f"city gap: {len(rows)} cases, {len(found)} problem(s)")
    for problem in found:
        print(f"  FAIL {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
