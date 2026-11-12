"""Aggregate stored runs into a provider / prompt-version quality dashboard.

The source of truth is the full JSON payload saved for every run, not a second
metrics table. That keeps the dashboard honest: it can only report what the
system actually recorded, including structured refusals (`infeasible`).

Standard library only, so `python -m shotplan.cli stats` works on a bare install.
"""

from __future__ import annotations

from collections import defaultdict
from math import fsum
from typing import Any

from .storage import RunStore


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))
    return round(ordered[index], 2)


def _summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    if count == 0:
        return {
            "runs": 0, "hard_pass_rate": 0.0, "avg_score": 0.0,
            "p50_duration_ms": 0.0, "p95_duration_ms": 0.0,
            "total_cost_cny": 0.0, "total_repairs": 0,
        }
    scores = [float(r["score"]) for r in rows]
    durations = [float(r["duration_ms"]) for r in rows]
    return {
        "runs": count,
        "hard_pass_rate": round(sum(bool(r["hard_pass"]) for r in rows) / count, 4),
        "avg_score": round(fsum(scores) / count, 4),
        "p50_duration_ms": _percentile(durations, 0.50),
        "p95_duration_ms": _percentile(durations, 0.95),
        "total_cost_cny": round(fsum(float(r["cost_cny"]) for r in rows), 6),
        "total_repairs": sum(int(r["repairs"]) for r in rows),
    }


def _flatten(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        trace = payload.get("trace") or {}
        validation = payload.get("validation") or {}
        rows.append(
            {
                "status": payload.get("status", "ok"),
                "provider": trace.get("provider", "unknown"),
                "prompt_version": trace.get("prompt_version", "unknown"),
                "hard_pass": bool(validation.get("hard_pass", False)),
                "score": float(validation.get("score", 0.0)),
                "duration_ms": float(trace.get("total_duration_ms", 0.0)),
                "cost_cny": float(trace.get("total_cost_cny", 0.0)),
                "repairs": int(trace.get("repair_count", 0)),
            }
        )
    return rows


def build_dashboard(store: RunStore, limit: int = 200) -> dict[str, Any]:
    """Return overall metrics plus one row per (provider, prompt_version) pair."""
    payloads = store.recent_payloads(limit)
    rows = _flatten(payloads)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["provider"], row["prompt_version"])].append(row)

    by_status: dict[str, int] = defaultdict(int)
    for row in rows:
        by_status[row["status"]] += 1

    group_rows = []
    for (provider, version), items in sorted(groups.items()):
        summary = _summarise(items)
        summary.update(
            {
                "provider": provider,
                "prompt_version": version,
                "infeasible": sum(1 for r in items if r["status"] == "infeasible"),
            }
        )
        group_rows.append(summary)

    return {
        "total_runs": len(rows),
        "window": limit,
        "by_status": dict(sorted(by_status.items())),
        "overall": _summarise(rows),
        "groups": group_rows,
        "notes": (
            "infeasible runs count as hard_pass=false with score 0; "
            "offline providers have zero estimated cost."
        ),
    }
