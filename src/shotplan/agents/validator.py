"""Deterministic validation: the part of the system that does NOT use an LLM.

This is the core argument of the project. A generated plan is not trusted until
it passes rules that encode what "executable" means. The same report feeds the
model's repair prompt, the eval scorers and the UI, so there is exactly one
definition of quality in the codebase.
"""

from __future__ import annotations

from math import fsum

from ..domain.constraints import (
    BUDGET_TOLERANCE_CNY,
    MAX_OVERRUN_MINUTES,
    MIN_ALTERNATIVES,
    MIN_SHOOT_BLOCK_MINUTES,
    MIN_SOURCE_REFS,
    MIN_TRANSFER_MINUTES,
    REQUIRED_ACTIVITIES,
    SCORE_WEIGHTS,
)
from ..domain.models import (
    Plan,
    PlanRequest,
    ValidationIssue,
    ValidationReport,
    to_minutes,
)
from ..knowledge.tools import KnowledgeTools, get_tools

# Python 3.12 changed sum() to compensated summation. Scores appear in reports,
# baselines and CI gates, so they must not vary by interpreter (see ADR 0006).
SCORE_DECIMALS = 6


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def validate(
    plan: Plan,
    request: PlanRequest,
    tools: KnowledgeTools | None = None,
) -> ValidationReport:
    kb = tools or get_tools()
    issues: list[ValidationIssue] = []
    slots = plan.slots

    def add(
        code: str,
        severity: str,
        message: str,
        *,
        slot_index: int | None = None,
        actual: str = "",
        expected: str = "",
        suggestion: str = "",
    ) -> None:
        issues.append(
            ValidationIssue(
                code=code,
                severity=severity,
                message=message,
                slot_index=slot_index,
                actual=actual,
                expected=expected,
                suggestion=suggestion,
            )
        )

    # --- 1. Timeline integrity -------------------------------------------------
    parsed: list[tuple[int, int]] = []
    for idx, slot in enumerate(slots):
        try:
            parsed.append((to_minutes(slot.start), to_minutes(slot.end)))
        except ValueError:
            add(
                "TIME_OVERLAP", "hard", f"时间格式非法：{slot.start}-{slot.end}",
                slot_index=idx, expected="HH:MM - HH:MM", suggestion="改为 24 小时制时间",
            )
            parsed.append((0, 0))

    for idx in range(1, len(slots)):
        prev_end = parsed[idx - 1][1]
        cur_start = parsed[idx][0]
        if cur_start < prev_end:
            add(
                "TIME_OVERLAP", "hard",
                f"时间块 {idx - 1} 与 {idx} 重叠（上一块 {slots[idx - 1].end} 结束，本块 {slots[idx].start} 开始）",
                slot_index=idx,
                actual=f"{slots[idx - 1].end} -> {slots[idx].start}",
                expected="下一块开始时间 >= 上一块结束时间",
                suggestion="顺延后续时间块，或压缩上一块时长",
            )

    if slots:
        elapsed = max(e for _, e in parsed) - min(s for s, _ in parsed)
        allowed = int(request.hours * 60) + MAX_OVERRUN_MINUTES
        if elapsed > allowed:
            add(
                "TIME_OVERRUN", "hard",
                f"总时长 {elapsed} 分钟，超出可用时长 {int(request.hours * 60)} 分钟",
                actual=f"{elapsed} min",
                expected=f"<= {allowed} min",
                suggestion="删减一个拍摄点，或缩短妆造/拍摄时长",
            )

    # --- 2. Required activities and ordering -----------------------------------
    activities = {s.activity for s in slots}
    for required in REQUIRED_ACTIVITIES:
        if required not in activities:
            add(
                "MISSING_ACTIVITY", "hard", f"缺少必需环节：{required}",
                actual=", ".join(sorted(activities)) or "empty",
                expected=f"包含 {required}",
                suggestion=f"补一个 activity={required} 的时间块",
            )

    shoot_indices = [i for i, s in enumerate(slots) if s.activity == "shoot"]
    makeup_indices = [i for i, s in enumerate(slots) if s.activity == "makeup"]
    if shoot_indices and makeup_indices and min(makeup_indices) > min(shoot_indices):
        add(
            "MAKEUP_ORDER", "hard", "妆造被排在了拍摄之后",
            slot_index=min(makeup_indices),
            actual="makeup after shoot",
            expected="makeup before first shoot",
            suggestion="把妆造移到时间轴最前面，并顺延其余时间块",
        )

    # --- 3. Transfer buffers ----------------------------------------------------
    for left, right in zip(shoot_indices, shoot_indices[1:], strict=False):
        a, b = slots[left], slots[right]
        if a.location_id == b.location_id:
            continue
        gap = to_minutes(b.start) - to_minutes(a.end)
        if gap < MIN_TRANSFER_MINUTES:
            add(
                "TRANSFER_BUFFER", "hard",
                f"从 {a.location_id} 到 {b.location_id} 只留了 {gap} 分钟转场",
                slot_index=right,
                actual=f"{gap} min",
                expected=f">= {MIN_TRANSFER_MINUTES} min",
                suggestion="在两段拍摄之间插入一个 travel 时间块",
            )

    # --- 4. Budget --------------------------------------------------------------
    slot_cost = sum(max(0, s.cost_cny) for s in slots)
    declared = max(plan.total_cost_cny, slot_cost)
    ceiling = request.budget_cny + BUDGET_TOLERANCE_CNY
    if declared > ceiling:
        add(
            "BUDGET_EXCEEDED", "hard", f"总花费 {declared} 元超出预算 {request.budget_cny} 元",
            actual=f"{declared} CNY",
            expected=f"<= {ceiling} CNY",
            suggestion="减少收费景点、改用免费场景或降低妆造套餐档位",
        )

    # --- 5. Grounding -----------------------------------------------------------
    known = kb.location_ids(plan.city)
    for idx, slot in enumerate(slots):
        loc = slot.location_id
        if slot.activity == "travel" and loc in ("", "-", "none"):
            continue
        if loc not in known:
            add(
                "UNKNOWN_LOCATION", "hard", f"引用了知识库中不存在的地点：{loc!r}",
                slot_index=idx,
                actual=loc,
                expected="candidate_locations 中的 id",
                suggestion="换成知识库里真实存在的地点 id",
            )

    # --- 6. Soft quality signals ------------------------------------------------
    for idx in shoot_indices:
        if slots[idx].minutes() < MIN_SHOOT_BLOCK_MINUTES:
            add(
                "SHOOT_TOO_SHORT", "soft",
                f"拍摄块 {idx} 只有 {slots[idx].minutes()} 分钟",
                slot_index=idx,
                expected=f">= {MIN_SHOOT_BLOCK_MINUTES} min",
                suggestion="合并相邻拍摄块或延长时长",
            )

    try:
        light_start, light_end = kb.light_window(plan.date)
        w_start, w_end = to_minutes(light_start), to_minutes(light_end)
        outdoor_shoot = any(
            s.activity == "shoot"
            and (item := kb.location(plan.city, s.location_id))
            and not item["indoor"]
            for s in slots
        )
        minutes_in_window = sum(
            max(0, min(to_minutes(s.end), w_end) - max(to_minutes(s.start), w_start))
            for s in slots
            if s.activity == "shoot"
        )
        if outdoor_shoot and minutes_in_window == 0:
            add(
                "LIGHT_MISUSE", "soft", f"黄金光线时段 {light_start}-{light_end} 完全没有外景拍摄",
                expected="黄金时段内安排外景",
                suggestion="把最后一个外景拍摄块移到傍晚",
            )
    except Exception:
        minutes_in_window = 0
        w_end, w_start = 0, 0

    if len(plan.alternatives) < MIN_ALTERNATIVES:
        add(
            "NO_ALTERNATIVE", "soft", "没有给出雨天/客流备选方案",
            expected=f">= {MIN_ALTERNATIVES} 条备选",
            suggestion="追加一条指向 indoor_fallback 的备选",
        )

    if len(plan.source_refs) < MIN_SOURCE_REFS:
        add(
            "NO_SOURCE_REF", "soft", "缺少知识库引用，输出不可溯源",
            actual=str(len(plan.source_refs)),
            expected=f">= {MIN_SOURCE_REFS} 条",
            suggestion="在 source_refs 中列出用到的 locations.json / styles.json 条目",
        )

    style_id = str(plan.styling.get("style_id", ""))
    style = kb.style(style_id) if style_id else None
    if style:
        shoot_tags: set[str] = set()
        for idx in shoot_indices:
            item = kb.location(plan.city, slots[idx].location_id)
            if item:
                shoot_tags.update(item["tags"])
        if shoot_tags and not (set(style["suitable_tags"]) & shoot_tags):
            add(
                "STYLE_MISMATCH", "soft", f"风格 {style_id} 与所选地点不匹配",
                expected=f"地点需含标签 {style['suitable_tags']}",
                suggestion="更换地点或改选更适配的妆造风格",
            )

    # --- 7. Scores --------------------------------------------------------------
    hard_issues = [i for i in issues if i.severity == "hard"]
    soft_issues = [i for i in issues if i.severity == "soft"]
    hard_pass = not hard_issues

    constraints = _clamp(1.0 - 0.25 * len(hard_issues) - 0.05 * len(soft_issues))
    budget_score = (
        1.0
        if declared <= ceiling
        else _clamp(1.0 - (declared - ceiling) / max(1, ceiling))
    )
    ref_score = _clamp(len(plan.source_refs) / max(1, MIN_SOURCE_REFS))
    slot_ref_ratio = (
        sum(1 for s in slots if s.source_refs) / len(slots) if slots else 0.0
    )
    traceability = _clamp(0.6 * ref_score + 0.4 * slot_ref_ratio)
    window = max(1, w_end - w_start)
    light = _clamp(minutes_in_window / window)
    completeness_parts = [
        bool(plan.alternatives),
        bool(plan.assumptions),
        bool(plan.styling),
        all(s.notes for s in slots),
        len(slots) >= 3,
    ]
    completeness = sum(completeness_parts) / len(completeness_parts)

    scores = {
        "constraints": constraints,
        "budget": budget_score,
        "traceability": traceability,
        "light": light,
        "completeness": completeness,
    }
    scores = {k: round(v, SCORE_DECIMALS) for k, v in scores.items()}
    # fsum is exact regardless of the interpreter's builtin sum() implementation.
    score = round(fsum(SCORE_WEIGHTS[k] * v for k, v in scores.items()), SCORE_DECIMALS)

    return ValidationReport(
        issues=issues,
        scores=scores,
        score=score,
        hard_pass=hard_pass,
    )
