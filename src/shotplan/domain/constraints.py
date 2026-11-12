"""The rule book: what makes a plan executable rather than merely plausible.

These constants are the single source of truth shared by the validator, the
scorers and the documentation generator. Changing a rule changes all three.
See docs/problem-and-metrics.md for the reasoning behind each number.
"""

from __future__ import annotations

# A plan may not run longer than the booked time plus this grace period.
MAX_OVERRUN_MINUTES = 15

# Minimum buffer between two different shooting locations.
MIN_TRANSFER_MINUTES = 20

# A shoot block shorter than this is not worth setting up lights for.
MIN_SHOOT_BLOCK_MINUTES = 60

# Makeup and costume must happen before the first shoot block.
REQUIRED_ACTIVITIES = ("makeup", "shoot")

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
        "code": "SHOOT_TOO_SHORT",
        "severity": "soft",
        "rule": f"单个拍摄块不少于 {MIN_SHOOT_BLOCK_MINUTES} 分钟",
        "why": "过短的拍摄块性价比低",
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
]
