"""Reading numbers and clock times out of one short Chinese sentence.

The follow-up parser used to work by keyword only, so "便宜 300" and "预算两千"
both fell through: there was no way to say *how much*, and the code could only
add a fixed step. This module turns the numeric part of the sentence into
values -- deterministically, no model, no tokenizer -- so the rule layer can do
arithmetic on what the client actually said.

Scope is deliberately narrow. It reads a number, its unit if it has one, and
the few words immediately before it. It does not try to parse Chinese grammar;
anything ambiguous is left unread rather than guessed, because a wrong number
is worse than asking the client to retype it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CN_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
CN_LARGE_UNITS = {"万": 10000}

ARABIC_RUN = re.compile(r"\d+(?:\.\d+)?")
CN_RUN = re.compile(r"[零〇一二两三四五六七八九十百千万]+")

# "一点/一些/一下" are hedges, not quantities. Treating them as the number 1 is
# how "预算降一点" would become "budget 1 CNY".
HEDGE_SUFFIXES = ("点", "些", "下", "会儿", "会儿吧")


@dataclass(frozen=True)
class Quantity:
    """A number found in text, with where it was, so callers can read context."""

    value: float
    start: int
    end: int
    raw: str


def cn_to_number(run: str) -> float | None:
    """Parse a Chinese numeral run. Returns None if it is not a numeral.

    Handles the spoken elisions a client actually types: 两千五 -> 2500,
    一千二 -> 1200, 三百五 -> 350, 十五 -> 15.
    """
    if not run:
        return None
    total = 0
    section = 0
    number = 0
    last_unit = 0
    seen = False
    for char in run:
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
            seen = True
        elif char in CN_SMALL_UNITS:
            unit = CN_SMALL_UNITS[char]
            section += (number or 1) * unit
            last_unit = unit
            number = 0
            seen = True
        elif char in CN_LARGE_UNITS:
            unit = CN_LARGE_UNITS[char]
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
            last_unit = unit
            seen = True
        else:
            return None
    if number:
        if last_unit >= 100:
            # "两千五" means 2500, not 2005: the trailing digit is the next
            # unit down, omitted out loud.
            section += number * (last_unit // 10)
        else:
            section += number
    return float(total + section) if seen else None


def _is_hedge(text: str, start: int, end: int) -> bool:
    if text[start:end] not in {"一", "1"}:
        return False
    return any(text[end:].startswith(suffix) for suffix in HEDGE_SUFFIXES)


def numbers_in(text: str) -> list[Quantity]:
    """Every readable number in the sentence, in order, hedges excluded."""
    found: list[Quantity] = []
    for match in ARABIC_RUN.finditer(text):
        if _is_hedge(text, match.start(), match.end()):
            continue
        found.append(
            Quantity(float(match.group()), match.start(), match.end(), match.group())
        )
    for match in CN_RUN.finditer(text):
        value = cn_to_number(match.group())
        if value is None or _is_hedge(text, match.start(), match.end()):
            continue
        found.append(Quantity(value, match.start(), match.end(), match.group()))
    found.sort(key=lambda q: q.start)
    return found


PERIOD_MERIDIEM = {
    "凌晨": 0,
    "早上": 0,
    "上午": 0,
    "中午": 12,
    "下午": 12,
    "傍晚": 12,
    "晚上": 12,
}

CLOCK = re.compile(
    r"(凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*"
    r"(\d{1,2}|[零〇一二两三四五六七八九十]{1,3})\s*[点时:：]\s*"
    r"(半|\d{1,2}|[一二三四五六七八九十]{1,3}分?)?"
)


def clock_times(text: str) -> list[int]:
    """Minute-of-day for every clock time with an explicit period word.

    A bare "五点" is deliberately ignored: it is equally likely to be 05:00 or
    17:00, and this layer must not pick one. "下午五点" is not ambiguous.
    """
    times: list[int] = []
    for match in CLOCK.finditer(text):
        period, hour_text, minute_text = match.group(1), match.group(2), match.group(3)
        if not period:
            continue
        hour_value = cn_to_number(hour_text) if not hour_text.isdigit() else float(hour_text)
        if hour_value is None or hour_value > 12:
            continue
        hour = int(hour_value)
        minute = 0
        if minute_text == "半":
            minute = 30
        elif minute_text:
            cleaned = minute_text.rstrip("分")
            parsed = cn_to_number(cleaned) if not cleaned.isdigit() else float(cleaned)
            if parsed is not None and 0 <= parsed < 60:
                minute = int(parsed)
        if PERIOD_MERIDIEM[period] and hour < 12:
            hour += 12
        if period == "中午" and hour == 12:
            hour = 12
        if 0 <= hour <= 23:
            times.append(hour * 60 + minute)
    return times


def fmt_clock(minute_of_day: int) -> str:
    return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"
