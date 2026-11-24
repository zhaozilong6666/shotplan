"""Human-facing exports of a stored plan: a Markdown itinerary and an ICS file.

Both functions take the payload stored in `runs.payload` (the same shape as
`PlanResult.to_dict()`), so an export never re-runs the planner and can never
disagree with the plan the user saw. No third-party calendar library: an ICS
with a handful of VEVENTs is small enough to keep auditable.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

ACTIVITY_LABELS = {
    "makeup": "妆造",
    "travel": "转场",
    "shoot": "拍摄",
    "rest": "休息",
    "backup": "备选",
}
CITY_LABELS = {
    "xian": "西安",
    "suzhou": "苏州",
    "luoyang": "洛阳",
    "hangzhou": "杭州",
    "chengdu": "成都",
    "beijing": "北京",
    "kaifeng": "开封",
    "nanjing": "南京",
}


def _slot_title(slot: dict[str, Any]) -> str:
    return str(slot.get("label") or ACTIVITY_LABELS.get(str(slot.get("activity")), "安排"))


def _date(payload: dict[str, Any]) -> str:
    plan = payload.get("plan") or {}
    request = payload.get("request") or {}
    return str(plan.get("date") or request.get("date") or "")


def _city(payload: dict[str, Any]) -> str:
    request = payload.get("request") or {}
    plan = payload.get("plan") or {}
    city = str(plan.get("city") or request.get("city") or "")
    return CITY_LABELS.get(city, city)


def plan_to_markdown(payload: dict[str, Any]) -> str:
    plan = payload.get("plan") or {}
    request = payload.get("request") or {}
    validation = payload.get("validation") or {}
    slots = plan.get("slots") or []
    title = f"{_city(payload)} · {_date(payload)} 拍摄行程".strip(" ·")
    feasibility = payload.get("feasibility") or {}
    lines = [
        f"# {title}",
        "",
        f"- 总花费：¥{plan.get('total_cost_cny', 0)} / 预算 ¥{request.get('budget_cny', 0)}",
        f"- 硬约束：{'通过' if validation.get('hard_pass') else '未通过'}"
        f"（{validation.get('score', 0):.3f} 分）",
        f"- 时间块：{len(slots)} 个",
        "",
    ]
    if feasibility and not feasibility.get("feasible", True):
        lines += [
            "## 不可行原因",
            "",
            f"{feasibility.get('headline') or '当前需求无法满足'}",
            "",
        ]
        for reason in feasibility.get("reasons") or []:
            lines.append(
                f"- `{reason.get('code', '?')}` {reason.get('message', '')}"
                f"（缺口：{reason.get('shortfall', '—')}；建议：{reason.get('suggestion', '—')}）"
            )
        lines.append("")
    lines += [
        "## 时间线",
        "",
        "| 时间 | 安排 | 地点 | 费用 | 备注 |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for slot in slots:
        notes = (
            str(slot.get("notes") or "—")
            .replace("|", "\\|")
            .replace("\r", " ")
            .replace("\n", " ")
        )
        lines.append(
            f"| {slot.get('start')}–{slot.get('end')} | {_slot_title(slot)} "
            f"| `{slot.get('location_id')}` | ¥{slot.get('cost_cny', 0)} | {notes} |"
        )
    styling = plan.get("styling") or {}
    if styling:
        lines += [
            "",
            "## 妆造",
            "",
            f"- 风格：{styling.get('name') or styling.get('style_id') or '—'}",
        ]
        if styling.get("notes"):
            lines.append(f"- 说明：{styling['notes']}")
    if plan.get("alternatives"):
        lines += ["", "## 备选", ""]
        for item in plan["alternatives"]:
            lines.append(
                f"- {item.get('trigger', '—')}：{item.get('description', '—')}"
                f"（`{item.get('location_id', '—')}`）"
            )
    if plan.get("assumptions"):
        lines += ["", "## 假设", ""]
        lines += [f"- {item}" for item in plan["assumptions"]]
    issues = validation.get("issues") or []
    if issues:
        lines += ["", "## 校验问题", ""]
        for issue in issues[:10]:
            lines.append(
                f"- `{issue.get('code', '?')}` {issue.get('message', '')}"
            )
    lines += [
        "",
        "---",
        f"由 shotplan 生成；运行 {((payload.get('trace') or {}).get('run_id')) or '—'}",
        "",
    ]
    return "\n".join(lines)


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _ics_time(date: str, clock: str) -> datetime:
    return datetime.strptime(f"{date} {clock}", "%Y-%m-%d %H:%M")


def _fold(line: str, limit: int = 75) -> str:
    """RFC 5545 folding: max 75 octets, continuation starts with one space.

    Chinese descriptions are multi-byte, so splitting by characters (or by
    Python string length) would exceed the octet limit. Encode each character,
    fill the current line to 75 bytes, then use 74 + the leading space.
    """
    if len(line.encode("utf-8")) <= limit:
        return line
    chunks: list[str] = []
    current = bytearray()
    while line:
        char = line[0]
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > limit:
            chunks.append(current.decode("utf-8"))
            current = bytearray()
            limit = 74
        current.extend(encoded)
        line = line[1:]
    chunks.append(current.decode("utf-8"))
    return "\r\n ".join(chunks)


def plan_to_ics(payload: dict[str, Any]) -> str:
    plan = payload.get("plan") or {}
    slots = plan.get("slots") or []
    if not slots:
        # An infeasible brief has reasons, not an itinerary. Exporting an empty
        # calendar would look like a successful, silently blank plan.
        raise ValueError(
            "no itinerary to export: this run has no time slots (likely infeasible); "
            "use the Markdown export for the reasons"
        )
    date = _date(payload)
    run_id = str((payload.get("trace") or {}).get("run_id") or "plan")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//shotplan//itinerary//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Shanghai",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for index, slot in enumerate(slots):
        start = _ics_time(date, str(slot.get("start")))
        end = _ics_time(date, str(slot.get("end")))
        if end <= start:
            end += timedelta(days=1)
        description = "；".join(
            part
            for part in (
                str(slot.get("notes") or ""),
                f"费用 ¥{slot.get('cost_cny', 0)}",
                "引用：" + ", ".join(slot.get("source_refs") or [])
                if slot.get("source_refs")
                else "",
            )
            if part
        )
        summary = f"{_slot_title(slot)}（{_city(payload)}）"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{run_id}-{index}@shotplan",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID=Asia/Shanghai:{start:%Y%m%dT%H%M%S}",
            f"DTEND;TZID=Asia/Shanghai:{end:%Y%m%dT%H%M%S}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"LOCATION:{_ics_escape(str(slot.get('location_id') or ''))}",
            f"DESCRIPTION:{_ics_escape(description)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def safe_filename(value: str) -> str:
    """Keep Content-Disposition filenames boring."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.replace(".", "-"))
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "shotplan"
