"""Turn official ticket pages into knowledge-base facts, so nobody hand-copies.

The knowledge base is the weakest asset in this repo: 68 entries whose prices and
opening hours were typed by hand, and nothing can re-verify them later. This
script does the mechanical half -- fetch, extract, validate, diff -- and leaves
only judgement (is this the right page? is 50 the peak price?) to a human.

Design constraints:
* stdlib only (urllib + html.parser + re): the repo promises zero dependencies.
* Extraction is conservative. A page that does not clearly state a price yields
  no price; a wrong number in the knowledge base is worse than a missing one.
* Nothing is written into `locations.json` automatically. Drafts go to stdout
  (or --out); merging stays a human decision.

    python scripts/ingest_city.py fetch --url https://example.com/tickets
    python scripts/ingest_city.py check          # re-fetch every registered URL
    python scripts/ingest_city.py check --out drafts.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "shotplan" / "knowledge" / "data" / "locations.json"
SOURCES = ROOT / "src" / "shotplan" / "knowledge" / "data" / "sources.json"

UA = "Mozilla/5.0 (compatible; ShotplanIngest/1.0; +https://github.com/zhaozilong6666/shotplan)"
MAX_BYTES = 2_000_000
TIMEOUT = 20

# ￥50 / ¥50 / 50元 / 成人票 50
PRICE_PATTERNS = (
    r"(?:全价|成人)[^\d￥¥]{0,6}[￥¥]?\s*(\d{1,5}(?:\.\d+)?)\s*(?:元|/人)?",
    r"[￥¥]\s*(\d{1,5}(?:\.\d+)?)",
    r"门票[^\d￥¥]{0,6}(\d{1,5}(?:\.\d+)?)\s*元",
    r"(\d{1,5}(?:\.\d+)?)\s*元\s*/?\s*(?:人|成人)?",
)

# 09:00 开馆 ... 18:00 闭馆 / 09:00-18:00 / 17:00 停止入馆
# The gap between "开放入馆" and "闭馆" can hold a whole phrase
# ("08:30 开放入馆时间 开放入馆 15:30 停止入馆时间 停止入馆 16:30 闭馆时间"),
# so 12 characters was far too tight.
HOURS_OPEN_CLOSE = re.compile(
    # The gap may itself contain a time ("15:30 停止入馆"), so it cannot be
# [^\d]; non-greedy and bounded keeps it from spanning sentences.
r"(\d{1,2}:\d{2})\s*(?:开放入馆|开馆|开园|开放).{0,40}?"
    r"(\d{1,2}:\d{2})\s*(?:闭馆|闭园|关闭|停止售票)"
)
HOURS_RANGE = re.compile(r"(\d{1,2}:\d{2})\s*[-–~至—]\s*(\d{1,2}:\d{2})")
LAST_ENTRY = re.compile(r"(\d{1,2}:\d{2})\s*停止(?:入馆|入园|入场|售)")

WEEKDAYS = {"一": "monday", "二": "tuesday", "三": "wednesday", "四": "thursday",
            "五": "friday", "六": "saturday", "日": "sunday", "天": "sunday"}
CLOSED_DAY = re.compile(r"周([一二三四五六日天])\s*(?:闭馆|闭园|休息)")

# Pages phrase this both ways: 「提前7天预约」and 「于参观7日前…预订」.
BOOKING = re.compile(r"(?:提前\s*(\d+)\s*天|(\d+)\s*日前)")


class _Text(HTMLParser):
    """Strip tags and script/style so the regexes see prose, not markup."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return " ".join(parser.parts)


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw = response.read(MAX_BYTES)
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def extract(text: str) -> dict[str, Any]:
    """Pull only what the page states clearly. Missing beats wrong."""
    facts: dict[str, Any] = {}

    for pattern in PRICE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            value = float(match.group(1))
            facts["cost_cny"] = int(value) if value == int(value) else value
            facts["price_evidence"] = match.group(0)[:40]
            break
    # "关于…免费开放日的公告" is a notice headline, not a price. Only treat
    # "免费" as a price when it is not followed by 日/政策 and not preceded by
    # 公告/通知.
    if "cost_cny" not in facts:
        for match in re.finditer(r"免费(?!开放日|日|政策|不免票|票)", text):
            before = text[max(0, match.start() - 12):match.start()]
            if "公告" in before or "通知" in before:
                continue
            facts["cost_cny"] = 0
            facts["price_evidence"] = "免费"
            break

    hours = HOURS_OPEN_CLOSE.search(text)
    if hours is None:
        # Fall back to a bare range, but never to a support-line window.
        for candidate in HOURS_RANGE.finditer(text):
            before = text[max(0, candidate.start() - 8):candidate.start()]
            if "服务" in before or "电话" in before:
                continue
            hours = candidate
            break
    if hours:
        facts["open_time"] = f"{hours.group(1)}-{hours.group(2)}"
    last = LAST_ENTRY.search(text)
    if last:
        facts["last_entry"] = last.group(1)

    closed = sorted({WEEKDAYS[m.group(1)] for m in CLOSED_DAY.finditer(text)})
    if closed:
        facts["closed_days"] = closed

    booking = BOOKING.search(text)
    if booking:
        days = booking.group(1) or booking.group(2)
        facts["booking"] = f"提前 {days} 天预约"

    return facts


def registered() -> dict[str, dict[str, str]]:
    if not SOURCES.exists():
        return {}
    return json.loads(SOURCES.read_text(encoding="utf-8"))


def check(out: Path | None = None) -> int:
    """Re-fetch every registered page and report differences against the KB."""
    data = json.loads(DATA.read_text(encoding="utf-8"))
    index = {
        (city_id, loc["id"]): loc
        for city_id, city in data["cities"].items()
        for loc in city["locations"]
    }
    facts_by_url = registered()
    if not facts_by_url:
        print("no sources registered; see src/shotplan/knowledge/data/sources.json")
        return 0

    results: list[dict[str, Any]] = []
    drifted = 0
    for key, entry in sorted(facts_by_url.items()):
        city_id, _, loc_id = key.partition("/")
        loc = index.get((city_id, loc_id))
        if loc is None:
            print(f"{key}: not in the knowledge base (stale source entry)")
            continue
        try:
            text = to_text(fetch(entry["url"]))
            facts = extract(text)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"{key}: fetch failed: {type(exc).__name__}")
            results.append({"key": key, "status": "unreachable", "error": type(exc).__name__})
            continue

        kb_price = loc["cost_cny"]
        web_price = facts.get("cost_cny")
        price_match = web_price is None or web_price == kb_price
        if not price_match:
            drifted += 1
        results.append({
            "key": key, "status": "ok",
            "kb": {"cost_cny": kb_price},
            "web": facts,
            "price_match": price_match,
        })
        flag = "OK " if price_match else "DIFF"
        shown = f"web={web_price}" if web_price is not None else "web=?"
        print(f"[{flag}] {key:28s} kb={kb_price:<6} {shown:<12} "
              f"{facts.get('open_time', '-')} {','.join(facts.get('closed_days') or [])}")

    print(f"\n{len(results)} checked, {drifted} price difference(s)")
    if out:
        out.write_text(json.dumps({
            "checked_at": dt.date.today().isoformat(),
            "results": results,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest official ticket pages into KB facts")
    sub = parser.add_subparsers(dest="command", required=True)

    one = sub.add_parser("fetch", help="extract facts from one page")
    one.add_argument("--url", required=True)
    one.add_argument("--out")

    chk = sub.add_parser("check", help="re-fetch all registered pages and diff")
    chk.add_argument("--out")

    args = parser.parse_args(argv)
    if args.command == "fetch":
        text = to_text(fetch(args.url))
        facts = extract(text)
        facts["source_url"] = args.url
        payload = json.dumps(facts, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            Path(args.out).write_text(payload, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            sys.stdout.write(payload)
        return 0
    return check(Path(args.out) if args.out else None)


if __name__ == "__main__":
    raise SystemExit(main())
