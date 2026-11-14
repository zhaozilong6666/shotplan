"""Fail CI when the curated knowledge base goes stale.

Every location and style carries `source` and `last_verified_at`. A price or
opening rule that nobody has checked in six months should not silently keep
shaping plans. This script lists the overdue entries; `--check` (the default)
returns non-zero so it can be wired into the gate.

    python scripts/check_knowledge_freshness.py --max-age-days 180
    python scripts/check_knowledge_freshness.py --max-age-days 1 --list-only
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "shotplan" / "knowledge" / "data"


@dataclass
class Record:
    label: str
    source: str
    verified: str


@dataclass
class Verdict:
    total: int = 0
    stale: list[tuple[str, str, int]] = field(default_factory=list)
    invalid: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    oldest: tuple[str, int] | None = None


def collect(locations: dict, styles: dict) -> list[Record]:
    records: list[Record] = []
    for city_id, city in locations.get("cities", {}).items():
        for item in city.get("locations", []):
            records.append(
                Record(
                    label=f"{city_id}/{item.get('id', '?')}",
                    source=str(item.get("source", "")),
                    verified=str(item.get("last_verified_at", "")),
                )
            )
    for item in styles.get("styles", []):
        records.append(
            Record(
                label=f"style/{item.get('id', '?')}",
                source=str(item.get("source", "")),
                verified=str(item.get("last_verified_at", "")),
            )
        )
    return records


def evaluate(records: list[Record], today: dt.date, max_age_days: int) -> Verdict:
    verdict = Verdict(total=len(records))
    ages: list[tuple[str, int]] = []
    for record in records:
        if not record.source or record.source == "None":
            verdict.missing.append(record.label)
        try:
            verified = dt.date.fromisoformat(record.verified)
        except ValueError:
            verdict.invalid.append((record.label, record.verified or "<empty>"))
            continue
        age = (today - verified).days
        if age < 0:
            verdict.invalid.append((record.label, f"future date {record.verified}"))
            continue
        ages.append((record.label, age))
        if age > max_age_days:
            verdict.stale.append((record.label, record.verified, age))
    if ages:
        verdict.oldest = max(ages, key=lambda pair: pair[1])
    verdict.stale.sort(key=lambda row: -row[2])
    return verdict


def render(verdict: Verdict, max_age_days: int, today: dt.date) -> str:
    lines = [
        f"# 知识库新鲜度（{today.isoformat()}，阈值 {max_age_days} 天）",
        "",
        f"检查 {verdict.total} 条记录；过期 {len(verdict.stale)} 条、"
        f"缺 `source` {len(verdict.missing)} 条、日期非法 {len(verdict.invalid)} 条。",
        "",
    ]
    if verdict.oldest:
        lines.append(f"最久未核验：`{verdict.oldest[0]}`，{verdict.oldest[1]} 天。")
        lines.append("")
    if verdict.stale:
        lines += ["| 条目 | 上次核验 | 距今（天） |", "| --- | --- | ---: |"]
        lines += [f"| `{label}` | {date} | {age} |" for label, date, age in verdict.stale]
        lines.append("")
    for title, rows in (("缺 `source`", verdict.missing), ("日期非法", [f"{a}: {b}" for a, b in verdict.invalid])):
        if rows:
            lines += [f"## {title}", ""] + [f"- `{row}`" for row in rows] + [""]
    lines += ["复核方式：更新 `last_verified_at` 前请实际核对票价、开放时间与光线条件。", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="check knowledge freshness")
    parser.add_argument("--max-age-days", type=int, default=180)
    parser.add_argument("--today", type=str, default=None, help="override today (YYYY-MM-DD), for tests")
    parser.add_argument("--list-only", action="store_true", help="always exit 0")
    args = parser.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    locations = json.loads((DATA / "locations.json").read_text(encoding="utf-8"))
    styles = json.loads((DATA / "styles.json").read_text(encoding="utf-8"))
    verdict = evaluate(collect(locations, styles), today, args.max_age_days)
    print(render(verdict, args.max_age_days, today))
    if verdict.stale or verdict.invalid or verdict.missing:
        return 0 if args.list_only else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
