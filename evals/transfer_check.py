"""Is 20 minutes enough to move between two places? A bound, not an answer.

The scheduler treats every transfer as `MIN_TRANSFER_MINUTES`, a constant, while
the knowledge base already records how long each place takes to reach from the
city centre. Those two facts do not have to agree, and nothing checks them.

They cannot be turned into a real distance: the data has one number per place
(distance to the centre), and distance between two arbitrary places needs at
least two dimensions. What the triangle inequality does give is a bracket --

    |a - b| <= d(A, B) <= a + b

-- where `a` and `b` are the travel times from the centre. So each pair falls in
one of three buckets:

- `|a - b| > 20`: every consistent geometry makes the transfer longer than 20
  minutes. The constant is provably too small for this pair.
- `a + b < 20`: every consistent geometry makes it shorter. The constant is
  generous here, which is harmless.
- otherwise: 20 minutes is *possible*, which is not the same as correct.

The third bucket is the honest headline. "Possible for most pairs" is a weak
claim, and the report says so rather than dressing it up as validation.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "shotplan" / "knowledge" / "data" / "locations.json"
REPORT = ROOT / "docs" / "transfer-budget.md"
BUDGET = 20


def classify(a: int, b: int) -> str:
    if abs(a - b) > BUDGET:
        return "too_short"
    if a + b < BUDGET:
        return "generous"
    return "possible"


def sweep() -> tuple[list[dict], Counter]:
    cities = json.loads(DATA.read_text(encoding="utf-8"))["cities"]
    rows = []
    total: Counter[str] = Counter()
    for city_id, city in cities.items():
        counts: Counter[str] = Counter()
        cross_ok = cross_all = 0
        places = city["locations"]
        for left, right in combinations(places, 2):
            a = int(left["travel_min_from_center"])
            b = int(right["travel_min_from_center"])
            kind = classify(a, b)
            counts[kind] += 1
            total[kind] += 1
            if left.get("district") != right.get("district"):
                cross_all += 1
                if kind != "too_short":
                    cross_ok += 1
        rows.append(
            {
                "city": city["name"],
                "places": len(places),
                "pairs": sum(counts.values()),
                "counts": counts,
                "cross_pairs": cross_all,
                "cross_ok": cross_ok,
            }
        )
    return rows, total


def render(rows: list[dict], total: Counter) -> str:
    pairs = sum(total.values())
    possible = total["possible"]
    share = possible / pairs * 100 if pairs else 0

    lines = [
        "> 由 `python -m evals.transfer_check --write` 生成，请勿手工编辑。",
        "",
        "# 20 分钟够不够转场：一个上下界，不是一个答案",
        "",
        f"调度器把每一次转场都当成 `MIN_TRANSFER_MINUTES = {BUDGET}`，而知识库里每个地点都记了"
        "「从市中心过去要多久」。这两件事没有必然关系，也没有任何检查对照过它们。",
        "",
        "数据只有每个地点到中心的一个数，两点之间的距离需要两个维度，所以算不出真实值。"
        "但三角不等式给了一个括号：`|a−b| ≤ 两点时间 ≤ a+b`。于是每对地点落入三类之一：",
        "",
        f"- **必然不够**：`|a−b| > {BUDGET}`，任何自洽的几何下转场都超过 20 分钟；",
        f"- **必然够**：`a+b < {BUDGET}`，怎么走都短于 20 分钟（这意味着常量在此偏松，无害）；",
        "- **可能**：区间包含 20——注意「可能」不等于「对」。",
        "",
        f"## 汇总：{pairs} 对地点",
        "",
        "| 分类 | 对数 | 占比 |",
        "| --- | ---: | ---: |",
        f"| 必然不够 | {total['too_short']} | {total['too_short'] / pairs:.0%} |",
        f"| 必然够 | {total['generous']} | {total['generous'] / pairs:.0%} |",
        f"| 可能（区间含 20） | {possible} | {share:.0%} |",
        "",
        f"**区间包含 20 分钟的比例是 {share:.0f}%**，这是那条假设的实测值。"
        "它成立的含义只有一层：这个常量对大多数地点对不是明显错的。"
        "它不成立的含义则很硬：如果多数配对连「可能」都算不上，常量就该改。",
        "",
        "## 按城市",
        "",
        "| 城市 | 地点 | 对数 | 必然不够 | 必然够 | 可能 | 跨区配对中非「必然不够」 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        cross = (
            f"{row['cross_ok']}/{row['cross_pairs']}" if row["cross_pairs"] else "—"
        )
        lines.append(
            f"| {row['city']} | {row['places']} | {row['pairs']} "
            f"| {row['counts']['too_short']} | {row['counts']['generous']} "
            f"| {row['counts']['possible']} | {cross} |"
        )
    lines.append("")

    short = [row for row in rows if row["counts"]["too_short"]]
    lines += [
        "## 哪些配对必然不够",
        "",
    ]
    if short:
        for row in short:
            lines.append(
                f"- **{row['city']}**：{row['counts']['too_short']} 对"
                f"（占该城 {row['counts']['too_short'] / row['pairs']:.0%}）"
            )
        lines += [
            "",
            "这些配对说明常量在结构上偏小，而不是碰巧偏小：只要一地在城郊（到中心 40 分钟）、"
            "另一地在城里（10 分钟），|a−b| 就是 30，20 分钟必然不够。修法不是把 20 调大——"
            "那会让市中心的短转场也被迫留出 40 分钟——而是让转场时间按地点算，"
            "知识库已经有需要的那个数了。",
        ]
    else:
        lines += ["没有。所有同城配对都至少「可能」。"]
    lines.append("")

    lines += [
        "## 这个测量不能证明什么",
        "",
        "它只能证明「20 分钟不是明显不够」，不能证明「20 分钟够」。"
        "反过来说，它也不能证明「按地点算时间」更好——那需要真实的城市路网数据或一次实测，"
        "而这里两者都没有。",
        "",
        "如果要把它变成真正的测量：给知识库的每个地点补经纬度，用球面距离除以该城市的实测平均车速，"
        "再和调度器的固定值对比。那是另一条实验，不是这一条的延伸。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    rows, total = sweep()
    text = render(rows, total)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    if "--check" in sys.argv or "--write" in sys.argv:
        if not REPORT.exists():
            print(f"缺少 {REPORT.name}，运行 --write", file=sys.stderr)
            return 1
        if REPORT.read_text(encoding="utf-8") != text:
            print(f"{REPORT.name} 与数据不一致，运行 --write", file=sys.stderr)
            return 1
    pairs = sum(total.values())
    share = total["possible"] / pairs * 100 if pairs else 0
    print(f"transfer budget: {pairs} pairs, {total['too_short']} too short, {share:.0f}% possible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
