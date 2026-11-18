"""Measure a feasibility rule's blast radius across every brief the repo has.

Adding a pre-generation check is cheap; the risk is that it refuses briefs that
are actually executable. That risk is invisible in the suite that motivated the
check, because those cases were written to fail. So this sweeps all four sets:

- `evals/challenge/` -- 320 briefs mined from real failures. The miner only
  accepts a case the good planner can execute, so every one of them must stay
  feasible. This is the hard assertion, and the reason the script exists.
- `evals/cases/`, `evals/cases_city/` -- regression sets, with a knowingly
  infeasible group.
- `evals/redteam/` -- adversarial set, likewise labelled.

A rule that starts refusing a challenge case is a false positive with a name and
an id, which is the only version of that complaint anyone can act on.

Deterministic: same briefs, same rules, same numbers.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.run_eval import load_cases  # noqa: E402
from shotplan.domain.feasibility import check_feasibility  # noqa: E402
from shotplan.domain.models import PlanRequest  # noqa: E402
from shotplan.knowledge.tools import get_tools  # noqa: E402

REPORT = ROOT / "docs" / "feasibility-sweep.md"

SETS = (
    ("主回归集", ROOT / "evals" / "cases", False),
    ("三城补充集", ROOT / "evals" / "cases_city", False),
    ("盲测集", ROOT / "evals" / "challenge", True),
    ("红队集", ROOT / "evals" / "redteam", False),
)


def sweep() -> list[dict]:
    tools = get_tools()
    results = []
    for label, directory, all_must_pass in SETS:
        cases = load_cases(directory)
        refused: list[tuple[str, str]] = []
        codes: Counter[str] = Counter()
        for case in cases:
            request = PlanRequest(**case["request"])
            report = check_feasibility(request, tools)
            if not report.feasible:
                refused.append((case["id"], report.reasons[0].code))
                codes.update(reason.code for reason in report.reasons)
        results.append(
            {
                "label": label,
                "total": len(cases),
                "refused": refused,
                "codes": codes,
                "all_must_pass": all_must_pass,
            }
        )
    return results


def problems(results: list[dict]) -> list[str]:
    found: list[str] = []
    for row in results:
        if row["all_must_pass"] and row["refused"]:
            for case_id, code in row["refused"]:
                found.append(f"{row['label']} {case_id} 被判不可行（{code}），但它是可执行的 brief")
    return found


def render(results: list[dict]) -> str:
    lines = [
        "> 由 `python -m evals.feasibility_sweep --write` 生成，请勿手工编辑。",
        "",
        "# 可行性判定的影响面",
        "",
        "加一条生成前检查很便宜，风险是它把**本来能执行**的 brief 也拒掉，"
        "而这个风险在促成它的那组用例里看不见——那些用例本来就是写来失败的。"
        "所以这里扫全部四组 brief，并把「盲测集必须全部可执行」当作硬标准："
        "那 320 条是挖掘器从真实失败里筛出来的，每一条都先跑通过 good provider 才被收进来。",
        "",
        "| 用例组 | 条数 | 判为不可行 | 不可行原因 |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in results:
        codes = "、".join(f"`{code}` ×{n}" for code, n in row["codes"].most_common()) or "—"
        lines.append(f"| {row['label']} | {row['total']} | {len(row['refused'])} | {codes} |")
    lines.append("")

    challenge = next(row for row in results if row["all_must_pass"])
    if challenge["refused"]:
        lines += [
            f"**盲测集里有 {len(challenge['refused'])} 条被判不可行**，属于误伤：",
            "",
        ]
        lines += [f"- `{case_id}` → `{code}`" for case_id, code in challenge["refused"]]
        lines.append("")
    else:
        lines += [
            f"盲测集 {challenge['total']} 条**全部判为可执行**，新增规则没有误伤。",
            "",
        ]

    lines += [
        "## 为什么只对盲测集断言",
        "",
        "主回归集与红队集里都有**故意不可行**的用例，它们被判拒绝才是对的；"
        "脚本无法从数据里可靠区分「故意不可行」和「被新规则误伤」，所以不对这两组下断言，只列数字。"
        "盲测集不同：它的每一条都经过 good provider 实跑并被接受，全可执行是它的定义。",
        "",
        "改动判据之后重跑这一页：",
        "",
        "```bash",
        "python -m evals.feasibility_sweep --write",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    results = sweep()
    text = render(results)
    found = problems(results)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    if "--check" in sys.argv or "--write" in sys.argv:
        if not REPORT.exists():
            found.append(f"缺少 {REPORT.name}，运行 --write")
        elif REPORT.read_text(encoding="utf-8") != text:
            found.append(f"{REPORT.name} 与当前输出不一致，运行 --write")
    total = sum(row["total"] for row in results)
    print(f"feasibility sweep: {total} briefs, {len(found)} problem(s)")
    for problem in found:
        print(f"  FAIL {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
