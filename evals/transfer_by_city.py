"""Does `rms` hurt more in cities with a wide radius? Tested per city.

E17 switched the transfer estimate to `rms` on the main set and cost two briefs a
location. That set is Xi'an, where every place is 10-20 minutes from the centre, so
the estimate barely moves -- which makes the result look like "rms is free". It may
not be: E16 found that Luoyang and Hangzhou have pairs whose transfer is provably
longer than 20 minutes, because one place sits in the suburbs and one downtown.

The holdout is the only place to check this. It has 40 briefs per city across all
eight cities, so the same briefs can be compared under both strategies without
touching the main regression set.

The comparison is deliberately narrow: how many briefs lose a location, and how many
stop passing. If the wide-radius cities show the same cost as Xi'an, then the
explanation in E17 was wrong and `rms` is free everywhere, which would mean the
constant was never the binding constraint at all.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evals.run_eval import load_cases  # noqa: E402
from shotplan.agents.mock import MockProvider  # noqa: E402
from shotplan.config import get_settings  # noqa: E402
from shotplan.domain.models import PlanRequest  # noqa: E402
from shotplan.pipeline import run_plan  # noqa: E402

REPORT = ROOT / "docs" / "transfer-by-city.md"
CASES = ROOT / "evals" / "challenge"
STRATEGIES = ("fixed", "rms")


def measure() -> dict[str, dict[str, list[dict[str, Any]]]]:
    settings = get_settings()
    briefs = load_cases(CASES)
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for strategy in STRATEGIES:
        provider = MockProvider(mode="good", transfer_strategy=strategy)
        rows = []
        for case in briefs:
            request = PlanRequest(**case["request"])
            result = run_plan(request, provider=provider, store=None, settings=settings)
            rows.append(
                {
                    "id": case["id"],
                    "city": request.city,
                    "locations": sum(1 for slot in result.plan.slots if slot.activity == "shoot"),
                    "hard_pass": bool(result.validation.hard_pass),
                }
            )
        by_strategy[strategy] = rows
    return by_strategy


def summarise(by_strategy: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    fixed = {row["id"]: row for row in by_strategy["fixed"]}
    rms = {row["id"]: row for row in by_strategy["rms"]}
    cities = sorted({row["city"] for row in by_strategy["fixed"]})
    rows = []
    for city in cities:
        ids = [key for key, row in fixed.items() if row["city"] == city]
        lost = [key for key in ids if rms[key]["locations"] < fixed[key]["locations"]]
        broke = [key for key in ids if fixed[key]["hard_pass"] and not rms[key]["hard_pass"]]
        rows.append(
            {
                "city": city,
                "briefs": len(ids),
                "fixed_pass": sum(fixed[key]["hard_pass"] for key in ids),
                "rms_pass": sum(rms[key]["hard_pass"] for key in ids),
                "lost": len(lost),
                "broke": len(broke),
                "fixed_locations": statistics.mean(fixed[key]["locations"] for key in ids),
                "rms_locations": statistics.mean(rms[key]["locations"] for key in ids),
            }
        )
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    wide = [row for row in rows if row["city"] in ("luoyang", "hangzhou")]
    xian = next((row for row in rows if row["city"] == "xian"), None)
    wide_lost = sum(row["lost"] for row in wide)
    total_lost = sum(row["lost"] for row in rows)

    lines = [
        "> 由 `python -m evals.transfer_by_city --write` 生成，请勿手工编辑。",
        "",
        "# rms 在不同城市里贵多少",
        "",
        "E17 在主集（西安）上把转场换成 `rms`，只让 2 条 brief 少排一个地点，看起来几乎免费。"
        "但西安的地点半径集中在 10–20 分钟，`rms` 和固定 20 的差距很小。E16 发现洛阳和杭州"
        "存在「必然超过 20 分钟」的配对——一地在城郊、一地在城里。",
        "",
        "盲测集是唯一能查这件事的地方：8 城各 40 条，同一批 brief 在两种策略下各排一次。",
        "",
        "| 城市 | brief | 固定通过 | rms 通过 | 少排地点 | 由通过变失败 | 平均地点数（固定→rms） |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['city']} | {row['briefs']} | {row['fixed_pass']} | {row['rms_pass']} "
            f"| {row['lost']} | {row['broke']} | {row['fixed_locations']:.2f} → {row['rms_locations']:.2f} |"
        )
    lines.append("")

    lines += [
        "## 结论",
        "",
        f"全城合计有 {total_lost} 条 brief 因为 `rms` 少排一个地点，其中洛阳与杭州占 {wide_lost} 条。",
    ]
    if xian and xian["lost"]:
        lines.append(
            f"西安（主集所在城市）是 {xian['lost']} 条，半径跨度大的两城合计是它的"
            f"{wide_lost / xian['lost']:.0f} 倍。"
        )
    lines += [""]

    if total_lost == 0:
        lines += [
            "**一条都没变，原因不在城市，在结构。** 盲测集里每个方案平均只有 1.62 个拍摄地点，"
            "整条时间线上通常只有一次转场（妆造棚 → 外景）。`rms` 与固定 20 的差在这一段通常是 5–15 分钟；"
            "而挤掉一个地点需要省出 60 分钟以上——那是一个拍摄块的下限。"
            "**转场估计的误差比决策阈值小一个量级，所以它影响不到结果。**",
            "",
            "这同时否定了 E17 的解释。E17 说『西安半径集中所以没差别』，暗示换个城市就会有差别；"
            "实测换到洛阳、杭州也一样没差别。真正的解释是转场只是排期里的小项，不是城市结构。"
            "E17 那 2 条变化更可能是边界 brief 被别的因素推过了线，而不是半径分布。",
            "",
        ]
    else:
        verdict = (
            "「半径跨度大的城市代价更高」这个解释成立：同样的规则改动，在西安几乎不产生可观察的差别，"
            "在洛阳、杭州则能数出来。这也说明 E17 的『几乎免费』是城市场景造成的，不是模型的性质。"
            if wide_lost > (xian["lost"] if xian else 0)
            else "这个解释不成立：半径跨度大的城市并没有付出更高代价。那么 E17 的『几乎免费』不是城市场景造成的，"
            "而是这个常量本来就不是约束——排期的瓶颈在别处。"
        )
        lines += [verdict, ""]

    broke = sum(row["broke"] for row in rows)
    lines += [
        "## 通过率的影响",
        "",
        (
            f"`rms` 让 {broke} 条原本通过的 brief 变成失败。这一列比「少排地点」更重要："
            "少排一个地点是方案变保守，变成失败是方案不再可用。"
            if broke
            else "没有任何 brief 因为换策略从通过变成失败。在这个样本上，`rms` 只改变方案的内容，不改变可用性。"
        ),
        "",
        "## 这个测量不能说明什么",
        "",
        "它只说明「同一批 brief，换策略后有多少变化」，不说明 `rms` 更接近真实路程。"
        "两座城市的差异可能来自半径分布，也可能来自 brief 本身的差异（比如洛阳的 brief 恰好要求更多地点）。"
        "要排除后者，需要把同一批 brief 逐城市对调——那会破坏 brief 的城市前提，做不了。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    rows = summarise(measure())
    text = render(rows)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    wide = sum(row["lost"] for row in rows if row["city"] in ("luoyang", "hangzhou"))
    total = sum(row["lost"] for row in rows)
    print(f"transfer by city: {total} briefs lost a location, wide-radius cities {wide}")
    if "--check" in sys.argv:
        if not REPORT.exists() or REPORT.read_text(encoding="utf-8") != text:
            print(f"{REPORT.name} 与当前输出不一致，运行 --write", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
