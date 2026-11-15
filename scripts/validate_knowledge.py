"""Enforce the knowledge-base data contract before and after city expansion.

Phase 8 asks for 10 cities. Adding cities is easy; adding data nobody verified
is how a planner starts quoting stale ticket prices. This gate defines exactly
what a valid record looks like, checks all current records, and makes the
expansion checklist executable. It does NOT invent any new city data.

    python scripts/validate_knowledge.py
    python scripts/validate_knowledge.py --check
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "shotplan" / "knowledge" / "data"
OUT = ROOT / "docs" / "knowledge-contract.md"

LIGHT_VALUES = {"morning", "golden", "night", "any"}
CROWD_VALUES = {"low", "medium", "high"}
RISK_VALUES = {"low", "medium", "high"}

LOCATION_FIELDS = (
    "id", "source", "last_verified_at", "name", "district", "tags",
    "best_light", "indoor", "min_minutes", "travel_min_from_center",
    "cost_cny", "crowd", "weather_risk", "notes",
)
STYLE_FIELDS = (
    "id", "source", "last_verified_at", "name", "era", "makeup_minutes",
    "costume_minutes", "price_cny", "suitable_tags", "avoid_tags", "notes",
)


def _date_ok(value: Any, today: dt.date) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        return False
    return parsed <= today


def validate(today: dt.date) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    locations = json.loads((DATA / "locations.json").read_text(encoding="utf-8"))
    styles = json.loads((DATA / "styles.json").read_text(encoding="utf-8"))

    ids: Counter = Counter()
    n_locations = 0
    for city_id, city in locations.get("cities", {}).items():
        for key in ("name", "center", "locations"):
            if key not in city:
                problems.append(f"city {city_id}: missing '{key}'")
        for index, loc in enumerate(city.get("locations", [])):
            label = f"{city_id}/{loc.get('id', f'#{index}')}"
            n_locations += 1
            ids[loc.get("id")] += 1
            for key in LOCATION_FIELDS:
                if key not in loc:
                    problems.append(f"{label}: missing '{key}'")
            if loc.get("source") in (None, "", "None"):
                problems.append(f"{label}: empty source")
            if not _date_ok(loc.get("last_verified_at"), today):
                problems.append(f"{label}: invalid/future last_verified_at")
            lights = loc.get("best_light")
            if not isinstance(lights, list) or not lights or not set(lights) <= LIGHT_VALUES:
                problems.append(f"{label}: best_light must be a non-empty subset of {sorted(LIGHT_VALUES)}")
            if not isinstance(loc.get("indoor"), bool):
                problems.append(f"{label}: indoor must be a bool")
            if not isinstance(loc.get("tags"), list) or not loc["tags"]:
                problems.append(f"{label}: tags must be a non-empty list")
            for field in ("min_minutes", "travel_min_from_center", "cost_cny"):
                value = loc.get(field)
                if not isinstance(value, int) or value < 0:
                    problems.append(f"{label}: {field} must be a non-negative int")
            if isinstance(loc.get("min_minutes"), int) and loc["min_minutes"] == 0:
                problems.append(f"{label}: min_minutes must be > 0")
            for field, allowed in (("crowd", CROWD_VALUES), ("weather_risk", RISK_VALUES)):
                if loc.get(field) not in allowed:
                    problems.append(f"{label}: {field} must be one of {sorted(allowed)}")

    duplicates = [key for key, count in ids.items() if count > 1]
    for key in duplicates:
        problems.append(f"duplicate location id: {key}")

    style_ids: Counter = Counter()
    for style in styles.get("styles", []):
        label = f"style/{style.get('id', '?')}"
        style_ids[style.get("id")] += 1
        for key in STYLE_FIELDS:
            if key not in style:
                problems.append(f"{label}: missing '{key}'")
        if style.get("source") in (None, "", "None"):
            problems.append(f"{label}: empty source")
        if not _date_ok(style.get("last_verified_at"), today):
            problems.append(f"{label}: invalid/future last_verified_at")
        price = style.get("price_cny")
        if (not isinstance(price, list) or len(price) != 2
                or not all(isinstance(v, int) and v >= 0 for v in price)
                or price[0] > price[1]):
            problems.append(f"{label}: price_cny must be [min, max] with min <= max")
        for field in ("makeup_minutes", "costume_minutes"):
            value = style.get(field)
            if not isinstance(value, int) or value <= 0:
                problems.append(f"{label}: {field} must be a positive int")
        for field in ("suitable_tags", "avoid_tags"):
            if not isinstance(style.get(field), list):
                problems.append(f"{label}: {field} must be a list")

    for key, count in style_ids.items():
        if count > 1:
            problems.append(f"duplicate style id: {key}")

    stats = {
        "cities": len(locations.get("cities", {})),
        "locations": n_locations,
        "styles": len(styles.get("styles", [])),
        "location_fields": len(LOCATION_FIELDS),
        "style_fields": len(STYLE_FIELDS),
    }
    return problems, stats


def render(problems: list[str], stats: dict[str, Any]) -> str:
    return "\n".join([
        "# 知识库数据契约",
        "",
        "> 由 `python scripts/validate_knowledge.py --check` 校验。",
        "> **本文件不含新城市数据**：扩到 10 城需要人工核验票价、开放时间与光线条件，",
        "> 编造地名与价格比不扩展更糟。这里只定义「什么才算一条合格记录」。",
        "",
        "## 当前状态",
        "",
        f"- {stats['cities']} 座城市、{stats['locations']} 个地点、{stats['styles']} 种风格",
        f"- 每条地点记录必须有 {stats['location_fields']} 个字段，每种风格 {stats['style_fields']} 个字段",
        f"- 校验结果：**{len(problems)} 项问题**" + ("（全部通过）" if not problems else ""),
        "",
        "## 一条合格记录必须满足",
        "",
        "| 约束 | 原因 |",
        "| --- | --- |",
        "| `source` 非空 | 没有出处的价格无法复核 |",
        "| `last_verified_at` 是合法且非未来的 ISO 日期 | 与 `check_knowledge_freshness.py` 的 180 天门禁配套 |",
        "| `id` 全库唯一 | 重复 id 会让 `location()` 返回错误地点 |",
        "| `best_light` 是 `morning/golden/night/any` 的非空子集 | 校验器的 `LIGHT_MISUSE` 直接读它 |",
        "| `indoor` 是布尔 | 决定雨天备选与光线规则是否适用 |",
        "| `min_minutes > 0`，`travel_min_from_center >= 0`，`cost_cny >= 0` | 时间轴与预算算术的输入 |",
        "| `crowd` / `weather_risk` ∈ low/medium/high | 排序与备选方案的枚举值 |",
        "| 风格 `price_cny = [min, max]` 且 min ≤ max | 预检用它算最低可执行成本 |",
        "",
        "## 扩城流程（需要真人核验）",
        "",
        "1. 每个候选地点记录：官方名称、区县、门票/预约价、建议停留时长、",
        "   从市中心交通时间、最佳光线、室内/室外、客流与天气风险。",
        "2. 为每条记录写 `source`（可复核的出处）与 `last_verified_at`（核验当天）。",
        "3. 运行 `python scripts/validate_knowledge.py`，再运行 `make gates`。",
        "4. 扩展后必须重跑：`evals/run_eval`（80 用例）、`evals/case_miner`（盲测集覆盖）、",
        "   `evals/slo_baseline`（延迟/成本基线可能变化）。",
        "5. 新城市不能只加进 `locations.json` 就算完成：风格标签、候选检索、",
        "   转场时间估算、`light_window()` 的季节表都要一起复核。",
        "",
        "## 为什么这一项仍不能勾掉",
        "",
        "阶段 8 的验收是「城市从 5 扩到 10，每条记录带 source 与 last_verified_at」。",
        "工具链能保证**格式与可复核性**，但保证不了**数据是真的**。",
        "在拿到可核验的票价与光线资料之前，写满 10 座城市只会把 5 座可信数据稀释成 10 座存疑数据。",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--today", default=None)
    args = parser.parse_args()
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    problems, stats = validate(today)
    expected = render(problems, stats)
    if args.write:
        OUT.write_text(expected, encoding="utf-8")
        print(f"wrote {OUT} ({len(problems)} problems)")
        return 1 if problems else 0
    if args.check:
        actual = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if actual.strip() != expected.strip() or problems:
            for problem in problems[:20]:
                print(f"  FAIL {problem}", file=sys.stderr)
            print(f"knowledge-contract gate: FAIL ({len(problems)} problems)", file=sys.stderr)
            return 1
        print("knowledge-contract gate: OK")
        return 0
    print(expected)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
