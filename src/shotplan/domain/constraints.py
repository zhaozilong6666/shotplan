"""The rule book: what makes a plan executable rather than merely plausible.

These constants are the single source of truth shared by the validator, the
scorers and the documentation generator. Changing a rule changes all three.
See docs/problem-and-metrics.md for the reasoning behind each number.
"""

from __future__ import annotations

import hashlib
import json
import re

# A plan may not run longer than the booked time plus this grace period.
MAX_OVERRUN_MINUTES = 15

# Minimum buffer between two different shooting locations.
MIN_TRANSFER_MINUTES = 20

# A shoot block shorter than this is not worth setting up lights for. This is
# the CEILING: with time to spare, a block should be at least this long.
MIN_SHOOT_BLOCK_MINUTES = 60

# But a 1.5-hour booking is makeup (90 min) + one transfer (20 min) + whatever
# is left. Demanding a 60-minute block there flagged 7 of 8 real-model failures
# that were physically impossible, not badly planned. The floor now scales with
# the booking, capped at the ceiling above.
MIN_SHOOT_BLOCK_FLOOR = 30


def min_shoot_block_minutes(available_hours: float) -> int:
    """Smallest sensible shoot block for this booking, between floor and ceiling."""
    proportional = int(available_hours * 60 * 0.25)
    return max(MIN_SHOOT_BLOCK_FLOOR, min(MIN_SHOOT_BLOCK_MINUTES, proportional))


# Styling is the one block that can give time back. A 1.5-hour booking cannot
# hold a full 85-minute 民国 styling block plus a shoot, but the answer is a
# compressed styling, not a five-minute photoshoot -- which is what a real-model
# run actually produced before this rule existed (C46).
STYLING_SHARE = 0.35
STYLING_FLOOR_MINUTES = 60


def styling_minutes_for(standard_minutes: int, available_hours: float) -> int:
    """How long the styling block may take inside this booking.

    Never longer than the style's standard duration, never shorter than the
    floor. Shared by the offline planner and the post-generation repair so the
    two cannot disagree about what "compressed" means.
    """
    # `round`, not truncation: 3h * 35% is 63 minutes, but the float result is
    # 62.999... and `int()` would silently take one minute away from styling.
    cap = max(STYLING_FLOOR_MINUTES, int(round(available_hours * 60 * STYLING_SHARE)))
    return min(standard_minutes, cap)

# Below this, makeup + one transfer + one shoot cannot fit at all.
MIN_FEASIBLE_HOURS = 1.5

# Minimum transport cost of any executable plan (shared by the offline planner).
MIN_TRANSFER_COST_CNY = 50

# Makeup and costume must happen before the first shoot block.
REQUIRED_ACTIVITIES = ("makeup", "shoot")

# Phrases that mean "I want to wrap up before noon". When one of these is
# present, an empty golden-light window is the CORRECT outcome, not a defect:
# the user asked for a morning session. Both the validator (which used to flag
# every such plan with LIGHT_MISUSE) and the light aligner must agree on this.
# Deliberately explicit: a bare "上午" can mean "morning light is nice", not
# "finish before noon". Treating it as a finish request would skip the light
# alignment for a plan that actually wants the golden hour.
MORNING_FINISH_WORDS = (
    "上午收工", "上午结束", "中午前", "中午之前", "收工早",
    "早点拍完", "上午拍完", "早上拍完",
)


def wants_morning_finish(preferences: list[str]) -> bool:
    text = " ".join(preferences)
    return any(word in text for word in MORNING_FINISH_WORDS)


# "17:00前结束" -- written by the follow-up parser when the client mentions a
# train or a flight. Keeping the format in one place means the writer and the
# reader cannot drift apart.
DEADLINE_PREFERENCE = re.compile(r"(\d{1,2}):(\d{2})\s*前结束")


def deadline_minutes(preferences: list[str]) -> int | None:
    """The clock time the client must be done by, as minutes since midnight."""
    for preference in preferences:
        match = DEADLINE_PREFERENCE.search(str(preference))
        if not match:
            continue
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    return None


# Hard ceiling: no plan may exceed the stated budget.
BUDGET_TOLERANCE_CNY = 0

# Golden hour by month bucket, used as a soft quality signal.
# (start_hour, end_hour) in local time.
GOLDEN_LIGHT_WINDOWS: dict[str, tuple[int, int]] = {
    "winter": (15, 17),   # Nov - Feb
    "spring": (16, 18),   # Mar - Apr
    "summer": (17, 19),   # May - Aug
    "autumn": (16, 18),   # Sep - Oct
}

# A credible plan offers at least one fallback when weather or crowds interfere.
MIN_ALTERNATIVES = 1

# Every generated plan must cite the knowledge entries it used.
MIN_SOURCE_REFS = 2

# Score weights. Hard rules gate the result; these weights grade quality.
SCORE_WEIGHTS: dict[str, float] = {
    "constraints": 0.40,
    "budget": 0.20,
    "traceability": 0.15,
    "light": 0.15,
    "completeness": 0.10,
}


def season_for(month: int) -> str:
    if month in (11, 12, 1, 2):
        return "winter"
    if month in (3, 4):
        return "spring"
    if month in (5, 6, 7, 8):
        return "summer"
    return "autumn"


def golden_window(month: int) -> tuple[int, int]:
    return GOLDEN_LIGHT_WINDOWS[season_for(month)]


RULES: list[dict[str, str]] = [
    {
        "code": "TIME_OVERLAP",
        "severity": "hard",
        "rule": "时间块不得重叠，且必须按时间递增排列",
        "why": "重叠的行程在现实中不可执行，是最常见的 LLM 幻觉",
    },
    {
        "code": "TIME_OVERRUN",
        "severity": "hard",
        "rule": f"总时长不得超过预约时长 + {MAX_OVERRUN_MINUTES} 分钟",
        "why": "超出拍摄时长意味着要临时加钱或砍环节",
    },
    {
        "code": "MISSING_ACTIVITY",
        "severity": "hard",
        "rule": "必须包含妆造环节和至少一个拍摄环节",
        "why": "汉服拍摄没有妆造就不成立",
    },
    {
        "code": "MAKEUP_ORDER",
        "severity": "hard",
        "rule": "妆造必须早于第一个拍摄环节",
        "why": "顺序错误是自由文本生成的高频错误",
    },
    {
        "code": "TRANSFER_BUFFER",
        "severity": "hard",
        "rule": f"跨地点拍摄之间至少留 {MIN_TRANSFER_MINUTES} 分钟转场",
        "why": "西安景区之间转场 + 补妆实际需要 20 分钟以上",
    },
    {
        "code": "BUDGET_EXCEEDED",
        "severity": "hard",
        "rule": "总花费不得超过预算",
        "why": "预算是用户给的硬约束，不是建议",
    },
    {
        "code": "UNKNOWN_LOCATION",
        "severity": "hard",
        "rule": "所有 location_id 必须存在于知识库",
        "why": "用于检测模型编造不存在的地点",
    },
    {
        "code": "MISSING_REQUIRED_LOCATION",
        "severity": "hard",
        "rule": "must_include 中的地点必须出现在拍摄时间块里",
        "why": "用户明确要求的地点被忽略，方案即使自洽也不可交付",
    },
    {
        "code": "AVOIDED_LOCATION",
        "severity": "hard",
        "rule": "avoid 中的地点不得出现在任何时间块里",
        "why": "排除项是用户给的硬约束；只把它当检索提示，模型可以绕过",
    },
    {
        "code": "UNKNOWN_SOURCE_REF",
        "severity": "hard",
        "rule": "source_refs 必须指向知识库中已有的地点或风格条目",
        "why": "引用不存在的来源等于伪造溯源，比不引用更糟",
    },
    {
        "code": "SHOOT_TOO_SHORT",
        "severity": "soft",
        "rule": (
            f"单个拍摄块不少于 max({MIN_SHOOT_BLOCK_FLOOR}, 预约时长的 25%)，"
            f"上限 {MIN_SHOOT_BLOCK_MINUTES} 分钟"
        ),
        "why": "过短的拍摄块性价比低；但短预约在物理上装不下 60 分钟，门槛需随时长缩放",
    },
    {
        "code": "LIGHT_MISUSE",
        "severity": "soft",
        "rule": "黄金光线时段应分配给外景拍摄而非室内/转场",
        "why": "光线是成片质量的主要变量",
    },
    {
        "code": "NO_ALTERNATIVE",
        "severity": "soft",
        "rule": f"至少给出 {MIN_ALTERNATIVES} 条备选方案",
        "why": "户外拍摄受天气与客流影响，必须有 Plan B",
    },
    {
        "code": "NO_SOURCE_REF",
        "severity": "soft",
        "rule": f"至少引用 {MIN_SOURCE_REFS} 条知识库条目",
        "why": "可溯源是判断输出是否可信的前提",
    },
    {
        "code": "STYLE_MISMATCH",
        "severity": "soft",
        "rule": "所选风格应适配至少一个拍摄地点",
        "why": "唐制配现代极简场景会显得违和",
    },
    {
        "code": "END_AFTER_DEADLINE",
        "severity": "hard",
        "rule": "方案结束时间不得晚于用户承诺的离开时刻（如「17:00前结束」）",
        "why": "赶高铁/飞机是真实硬约束；识别到却不校验，等于没识别",
    },
]


def rules_fingerprint() -> str:
    """Stable fingerprint of the rule book, recorded in every run trace.

    Scores are only comparable if you know which rules produced them. Hashing
    the rule table plus the thresholds it depends on makes that visible without
    maintaining a manual version string that will eventually be forgotten.
    """
    payload = {
        "rules": RULES,
        "weights": SCORE_WEIGHTS,
        "constants": {
            "max_overrun_minutes": MAX_OVERRUN_MINUTES,
            "min_transfer_minutes": MIN_TRANSFER_MINUTES,
            "min_shoot_block_minutes": MIN_SHOOT_BLOCK_MINUTES,
            "min_feasible_hours": MIN_FEASIBLE_HOURS,
            "min_transfer_cost_cny": MIN_TRANSFER_COST_CNY,
            "required_activities": list(REQUIRED_ACTIVITIES),
            "budget_tolerance_cny": BUDGET_TOLERANCE_CNY,
            "golden_light_windows": GOLDEN_LIGHT_WINDOWS,
            "min_alternatives": MIN_ALTERNATIVES,
            "min_source_refs": MIN_SOURCE_REFS,
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]
