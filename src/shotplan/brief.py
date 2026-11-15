"""Turn one sentence of human intent into a structured, *accountable* request.

Most "AI feels dumb" complaints start at the input: a person says 「国庆去西安拍唐风，
预算一千五，最好上午收工，别去人多的地方」 and we hand them six form fields.
This module does the translation without a model, and -- more importantly --
shows every assumption it made so the user can correct it.

Design rules:
  * zero dependencies (the core engine stays dependency-free);
  * deterministic: same text + same today + same catalog -> same result;
  * never silently invent. Anything not said is marked `default` or `inferred`
    with the exact text fragment that caused it;
  * conflicts become questions, not hidden guesses.

    python -c "from shotplan.brief import understand; print(understand('...').to_dict())"
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .domain.models import PlanRequest
from .knowledge.tools import KnowledgeTools, get_tools

SOURCES = ("stated", "inferred", "default")

# Historical / poetic names only. The authoritative alias list is derived from
# the knowledge base at call time, so adding a city to locations.json is enough
# for the sentence parser to recognise its official name -- no code change.
EXTRA_CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "xian": ("长安",),
    "suzhou": ("姑苏",),
    "nanjing": ("金陵",),
    "hangzhou": ("临安",),
    "luoyang": ("洛邑", "神都"),
}


def city_aliases(tools: KnowledgeTools) -> dict[str, tuple[str, ...]]:
    aliases: dict[str, tuple[str, ...]] = {}
    for city_id in tools.city_ids():
        city = tools.city(city_id)
        names = [city_id, str(city.get("name", "")), *EXTRA_CITY_ALIASES.get(city_id, ())]
        aliases[city_id] = tuple(dict.fromkeys(name for name in names if name))
    return aliases

# Style-specific slang stays here (the data file has ids and names, not how
# people actually say them). Everything authoritative comes from styles.json.
EXTRA_STYLE_ALIASES: dict[str, tuple[str, ...]] = {
    "tang": ("唐制", "唐风", "唐朝", "唐"),
    "song": ("宋制", "宋风", "宋朝", "宋"),
    "ming": ("明制", "明风", "明朝", "明"),
    "weijin": ("魏晋", "魏晋风"),
    "republic": ("民国", "旗袍", "旗装", "民国风"),
}


def style_aliases(tools: KnowledgeTools) -> dict[str, tuple[str, ...]]:
    aliases: dict[str, tuple[str, ...]] = {}
    for style in tools.styles():
        style_id = style["id"]
        names = [
            style_id, str(style.get("name", "")), str(style.get("era", "")),
            *EXTRA_STYLE_ALIASES.get(style_id, ()),
        ]
        aliases[style_id] = tuple(dict.fromkeys(name for name in names if name))
    return aliases

LIGHT_WORDS = ("上午", "中午前", "中午之前", "早上", "晨光", "半天", "傍晚", "黄金光线", "夕阳")
QUIET_WORDS = ("人少", "安静", "避开人群", "不要人多", "冷门", "小众")
INDOOR_WORDS = ("室内", "下雨", "雨天", "怕晒")
NO_NIGHT_WORDS = ("不拍夜景", "不要夜景", "不拍晚上", "避免夜景")

# Aesthetic intent is the part forms cannot express. These are deliberately
# low-confidence inferences: the UI shows them so the user can overrule them,
# rather than the system pretending it "knows" what 温柔 means.
MOOD_HINTS: dict[str, tuple[str, ...]] = {
    "song": ("温柔", "婉约", "清雅", "素净", "淡雅", "书卷气", "江南"),
    "tang": ("大气", "华丽", "盛唐", "雍容", "富贵", "浓艳", "热闹"),
    "weijin": ("清冷", "仙气", "飘逸", "出尘", "空灵", "竹林"),
    "ming": ("端庄", "端正", "规整", "明代", "古板"),
    "republic": ("复古", "旧上海", "摩登", "胶片", "民国"),
}
ATMOSPHERE_WORDS = ("氛围感", "电影感", "故事感", "有情绪", "出片")


@dataclass
class Assumption:
    field: str
    value: Any
    source: str  # stated | inferred | default
    confidence: float
    evidence: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field, "value": self.value, "source": self.source,
            "confidence": self.confidence, "evidence": self.evidence, "note": self.note,
        }


@dataclass
class BriefUnderstanding:
    request: PlanRequest
    assumptions: list[Assumption] = field(default_factory=list)
    clarifications: list[dict[str, str]] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "assumptions": [a.to_dict() for a in self.assumptions],
            "clarifications": self.clarifications,
            "raw_text": self.raw_text,
            "stated_count": sum(a.source == "stated" for a in self.assumptions),
            "inferred_count": sum(a.source == "inferred" for a in self.assumptions),
            "default_count": sum(a.source == "default" for a in self.assumptions),
        }


def _find(text: str, words: tuple[str, ...]) -> str | None:
    return next((w for w in sorted(words, key=len, reverse=True) if w in text), None)


def _default_date(today: dt.date) -> dt.date:
    """Next weekend, so a demo brief is always executable regardless of season."""
    delta = (5 - today.weekday()) % 7 or 7
    return today + dt.timedelta(days=delta)


def _parse_date(text: str, today: dt.date) -> tuple[str, str | None, str, float]:
    match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if match:
        y, m, d = (int(v) for v in match.groups())
        try:
            return dt.date(y, m, d).isoformat(), match.group(0), "stated", 1.0
        except ValueError:
            pass
    match = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if match:
        m, d = int(match.group(1)), int(match.group(2))
        year = today.year
        candidate = dt.date(year, m, d)
        if candidate < today:
            candidate = dt.date(year + 1, m, d)
        return candidate.isoformat(), match.group(0), "stated", 1.0
    if any(w in text for w in ("国庆", "十一")):
        year = today.year if (today.month, today.day) <= (10, 1) else today.year + 1
        return f"{year}-10-01", _find(text, ("国庆", "十一")), "inferred", 0.85
    if "明天" in text:
        return (today + dt.timedelta(days=1)).isoformat(), "明天", "stated", 1.0
    if "下周" in text or "下个周末" in text:
        return (today + dt.timedelta(days=7)).isoformat(), "下周", "inferred", 0.7
    return _default_date(today).isoformat(), None, "default", 0.0


def _parse_hours(text: str) -> tuple[float, str | None, str, float]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:个)?\s*(?:小时|钟头|h)", text, re.I)
    if match:
        return min(12.0, max(1.0, float(match.group(1)))), match.group(0), "stated", 1.0
    if "一整天" in text or "全天" in text:
        return 8.0, _find(text, ("一整天", "全天")), "inferred", 0.8
    if "半天" in text:
        return 4.0, "半天", "inferred", 0.75
    if "上午" in text or "中午前" in text or "中午之前" in text:
        return 3.0, _find(text, ("上午", "中午前", "中午之前")), "inferred", 0.7
    return 6.0, None, "default", 0.0


def _parse_budget(text: str) -> tuple[int, str | None, str, float]:
    # A bare 4-digit number is far more likely to be a year than a budget.
    # Only accept money when it is marked as money.
    patterns = (
        r"(?:预算|花费|大概|差不多|不超过|最多|控制在|封顶)\s*([1-9]\d{2,5})\s*(?:元|块|块钱)?",
        r"([1-9]\d{2,5})\s*(?:元|块钱|块)(?:以内|左右|以下)?",
        r"([1-9]\d{2,5})\s*(?:以内|以下)\s*(?:元|块)?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1)), match.group(0), "stated", 1.0
    for word, value in (("两千", 2000), ("一千五", 1500), ("一千", 1000)):
        if word in text:
            return value, word, "stated", 1.0
    return 1500, None, "default", 0.0


def _parse_party(text: str) -> tuple[int, str | None, str, float]:
    match = re.search(r"(\d+)\s*(?:个)?人", text)
    if match:
        return min(10, max(1, int(match.group(1)))), match.group(0), "stated", 1.0
    if "两个人" in text or "俩人" in text:
        return 2, _find(text, ("两个人", "俩人")), "stated", 1.0
    if "情侣" in text or "和对象" in text:
        return 2, _find(text, ("情侣", "和对象")), "inferred", 0.8
    return 1, None, "default", 0.0


def understand(
    text: str,
    *,
    today: dt.date | None = None,
    tools: KnowledgeTools | None = None,
) -> BriefUnderstanding:
    """Parse one free-form brief into a request plus an auditable assumption log."""
    today = today or dt.date.today()
    tools = tools or get_tools()
    raw = " ".join(str(text or "").split())
    assumptions: list[Assumption] = []
    clarifications: list[dict[str, str]] = []

    def add(name: str, value: Any, source: str, confidence: float,
            evidence: str | None, note: str = "") -> None:
        assumptions.append(Assumption(name, value, source, confidence, evidence or "", note))

    available = set(tools.city_ids())
    aliases_by_city = city_aliases(tools)
    city = None
    city_evidence = None
    for city_id, aliases in aliases_by_city.items():
        if city_id not in available:
            continue
        hit = _find(raw, aliases)
        if hit:
            city, city_evidence = city_id, hit
            break
    if city is None:
        city = "xian" if "xian" in available else sorted(available)[0]
        add("city", city, "default", 0.0, None,
            f"没听出城市，默认用 {tools.city(city)['name']}；可在下面直接改")
    else:
        add("city", city, "stated", 1.0, city_evidence)

    date_value, ev, src, conf = _parse_date(raw, today)
    add("date", date_value, src, conf, ev,
        "按周末安排光线窗口" if src == "default" else "")

    hours, ev, src, conf = _parse_hours(raw)
    add("hours", hours, src, conf, ev,
        "未说明时长，按常见半天档" if src == "default" else "")

    budget, ev, src, conf = _parse_budget(raw)
    add("budget_cny", budget, src, conf, ev,
        "未说明预算，按中档妆造+外景估算" if src == "default" else "")

    mood_evidence: list[str] = []
    style_alias_map = style_aliases(tools)
    styles = [sid for sid, aliases in style_alias_map.items()
              if sid in tools.style_ids() and _find(raw, aliases)]
    if styles:
        add("styles", styles[:2], "stated", 1.0,
            "、".join(_find(raw, style_alias_map[s]) or s for s in styles[:2]))
    else:
        # No explicit dynasty was named. Try the aesthetic vocabulary instead.
        for sid, words in MOOD_HINTS.items():
            if sid not in tools.style_ids():
                continue
            hit = _find(raw, words)
            if hit:
                mood_evidence.append(hit)
                styles = [sid]
                break
        if styles:
            chosen = tools.style(styles[0]) or {}
            add("styles", styles, "inferred", 0.55, "、".join(mood_evidence),
                f"从「{'、'.join(mood_evidence)}」推断偏 {chosen.get('name', styles[0])}；"
                "这只是气质上的猜测，可以直接改")
        else:
            styles = ["tang"]
            add("styles", styles, "default", 0.0, None,
                "没说风格，默认唐制（适配古建最多）")

    atmosphere = _find(raw, ATMOSPHERE_WORDS)
    if atmosphere:
        add("atmosphere", atmosphere, "inferred", 0.5, atmosphere,
            "偏氛围的表达会优先安排黄金光线与少人场景")

    party, ev, src, conf = _parse_party(raw)
    add("party_size", party, src, conf, ev)

    preferences: list[str] = []
    for word in LIGHT_WORDS:
        if word in raw and word not in preferences:
            preferences.append(word)
    for word in QUIET_WORDS:
        if word in raw and word not in preferences:
            preferences.append(word)
    if _find(raw, NO_NIGHT_WORDS):
        preferences.append("不拍夜景")
    if _find(raw, INDOOR_WORDS):
        preferences.append("雨天备选")
    if atmosphere and "黄金光线" not in preferences:
        preferences.append("黄金光线")
    stated_prefs = [w for w in LIGHT_WORDS + QUIET_WORDS if w in raw and w in preferences]
    add(
        "preferences", preferences,
        "stated" if stated_prefs else ("inferred" if preferences else "default"),
        1.0 if stated_prefs else (0.5 if preferences else 0.0),
        "、".join(stated_prefs) or ("由氛围词推断" if preferences else None),
    )

    avoid: list[str] = []
    for marker in ("别去", "不去", "避开", "排除", "不想去"):
        for fragment in re.findall(marker + r"([\u4e00-\u9fa5A-Za-z0-9_]{2,12})", raw):
            for loc in tools.locations(city):
                if loc["name"] in fragment or fragment in loc["name"] or fragment == loc["id"]:
                    if loc["id"] not in avoid:
                        avoid.append(loc["id"])
    add("avoid", avoid, "stated" if avoid else "default",
        1.0 if avoid else 0.0, marker if avoid else None)

    # Conflicts and guesses become questions, not hidden decisions.
    wants_morning_finish = any(
        w in raw for w in ("上午收工", "中午前", "中午之前", "上午拍完", "上午", "晨光")
    )
    hours_source = next(a.source for a in assumptions if a.field == "hours")
    if wants_morning_finish and hours >= 6:
        clarifications.append({
            "question": f"你希望上午收工，但给了 {hours:g} 小时。妆造通常就要 1.5–2 小时，"
                        "上午很难拍完 3 个以上外景。",
            "why": "时长与收工时间冲突",
            "suggestion": "把时长改成 3 小时，或把收工时间放宽到下午",
        })
    elif wants_morning_finish and hours_source != "stated":
        clarifications.append({
            "question": f"你没说拍多久，我从「上午收工」推断为 {hours:g} 小时，对吗？",
            "why": "关键时长来自推断而非原话",
            "suggestion": "确认 3 小时，或在下面改成你真实可用的时间",
        })
    if budget <= 800:
        clarifications.append({
            "question": f"预算 {budget} 元可能只够妆造，外景门票与转场会比较紧。",
            "why": "预算接近最低可执行成本",
            "suggestion": "提高到 1000 元，或减少一个外景点",
        })
    stated = sum(a.source == "stated" for a in assumptions)
    if stated <= 1:
        clarifications.append({
            "question": "你描述得比较抽象，我大部分是按常见情况假设的。",
            "why": "关键条件几乎全是默认值",
            "suggestion": "补一句城市、日期或预算，方案会准很多；也可以先看看默认方案长什么样",
        })

    # A random request_id would make two parses of the same sentence differ,
    # breaking caching, replay and the determinism promise of this module.
    digest = hashlib.sha256(f"{raw}|{today.isoformat()}".encode()).hexdigest()[:12]
    request = PlanRequest(
        city=city,
        date=date_value,
        hours=hours,
        budget_cny=budget,
        party_size=party,
        styles=styles,
        preferences=preferences,
        avoid=avoid,
        request_id=f"req_{digest}",
    )
    return BriefUnderstanding(
        request=request.normalized(),
        assumptions=assumptions,
        clarifications=clarifications,
        raw_text=raw,
    )
