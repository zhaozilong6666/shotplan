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
    """Quality metrics over successful runs only.

    An `error` is a provider outage and `infeasible` is a structured refusal:
    neither is a bad plan. Counting them as failures makes the infrastructure
    grade the model -- the exact bug the eval reports already fixed.
    """
    count = len(rows)
    ok = [r for r in rows if r["status"] == "ok"]
    errors = sum(1 for r in rows if r["status"] == "error")
    infeasible = sum(1 for r in rows if r["status"] == "infeasible")
    if not ok:
        return {
            "runs": count, "ok_runs": 0, "error_runs": errors,
            "infeasible_runs": infeasible,
            "error_rate": round(errors / count, 4) if count else 0.0,
            "infeasible_rate": round(infeasible / count, 4) if count else 0.0,
            "hard_pass_rate": 0.0, "avg_score": 0.0,
            "p50_duration_ms": 0.0, "p95_duration_ms": 0.0,
            "total_cost_cny": round(fsum(float(r["cost_cny"]) for r in rows), 6),
            "total_repairs": sum(int(r["repairs"]) for r in rows),
        }
    scores = [float(r["score"]) for r in ok]
    durations = [float(r["duration_ms"]) for r in ok]
    n_ok = len(ok)
    return {
        "runs": count,
        "ok_runs": n_ok,
        "error_runs": errors,
        "infeasible_runs": infeasible,
        "error_rate": round(errors / count, 4),
        "infeasible_rate": round(infeasible / count, 4),
        "hard_pass_rate": round(sum(bool(r["hard_pass"]) for r in ok) / n_ok, 4),
        "avg_score": round(fsum(scores) / n_ok, 4),
        "p50_duration_ms": _percentile(durations, 0.50),
        "p95_duration_ms": _percentile(durations, 0.95),
        # Money was spent even on failed calls; latency is a quality metric only.
        "total_cost_cny": round(fsum(float(r["cost_cny"]) for r in rows), 6),
        "total_repairs": sum(int(r["repairs"]) for r in rows),
    }


def _label(value: str) -> str:
    """Prometheus label values: backslash, quote and newline must be escaped."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus(dashboard: dict[str, Any]) -> str:
    """Expose the dashboard as scrapeable text, without a metrics dependency.

    These are gauges over the last `window` runs, not cumulative counters:
    the dashboard itself is a sliding window, and calling a window gauge a
    counter would make rate() lie.
    """
    overall = dashboard.get("overall", {})
    window = dashboard.get("window", 0)
    lines = [
        "# HELP shotplan_runs Number of runs in the current observation window.",
        "# TYPE shotplan_runs gauge",
        f'shotplan_runs{{window="{window}"}} {overall.get("runs", 0)}',
        "# HELP shotplan_hard_pass_rate Fraction of successful runs passing hard constraints (status=ok only).",
        "# TYPE shotplan_hard_pass_rate gauge",
        f'shotplan_hard_pass_rate{{window="{window}"}} {overall.get("hard_pass_rate", 0.0)}',
        "# HELP shotplan_ok_runs Runs that produced a plan (status=ok).",
        "# TYPE shotplan_ok_runs gauge",
        f'shotplan_ok_runs{{window="{window}"}} {overall.get("ok_runs", 0)}',
        "# HELP shotplan_error_rate Fraction of runs that failed in the provider (not a bad plan).",
        "# TYPE shotplan_error_rate gauge",
        f'shotplan_error_rate{{window="{window}"}} {overall.get("error_rate", 0.0)}',
        "# HELP shotplan_infeasible_rate Fraction of runs rejected by preflight.",
        "# TYPE shotplan_infeasible_rate gauge",
        f'shotplan_infeasible_rate{{window="{window}"}} {overall.get("infeasible_rate", 0.0)}',
        "# HELP shotplan_avg_score Mean validation score.",
        "# TYPE shotplan_avg_score gauge",
        f'shotplan_avg_score{{window="{window}"}} {overall.get("avg_score", 0.0)}',
        "# HELP shotplan_duration_ms Request duration percentiles in milliseconds.",
        "# TYPE shotplan_duration_ms gauge",
        f'shotplan_duration_ms{{window="{window}",quantile="0.5"}} '
        f'{overall.get("p50_duration_ms", 0.0)}',
        f'shotplan_duration_ms{{window="{window}",quantile="0.95"}} '
        f'{overall.get("p95_duration_ms", 0.0)}',
        "# HELP shotplan_repairs_total Repair attempts in the window.",
        "# TYPE shotplan_repairs_total gauge",
        f'shotplan_repairs_total{{window="{window}"}} {overall.get("total_repairs", 0)}',
        "# HELP shotplan_cost_cny_total Estimated spend in the window (CNY).",
        "# TYPE shotplan_cost_cny_total gauge",
        f'shotplan_cost_cny_total{{window="{window}"}} {overall.get("total_cost_cny", 0.0)}',
        "# HELP shotplan_runs_by_status Runs in the window, by outcome status.",
        "# TYPE shotplan_runs_by_status gauge",
    ]
    for status, count in sorted((dashboard.get("by_status") or {}).items()):
        lines.append(f'shotplan_runs_by_status{{status="{_label(status)}"}} {count}')

    lines += [
        "# HELP shotplan_group_runs Runs per provider / prompt version.",
        "# TYPE shotplan_group_runs gauge",
        "# HELP shotplan_group_hard_pass_rate Hard pass rate per provider / prompt version.",
        "# TYPE shotplan_group_hard_pass_rate gauge",
        "# HELP shotplan_group_p95_duration_ms P95 duration per provider / prompt version.",
        "# TYPE shotplan_group_p95_duration_ms gauge",
    ]
    group_runs: list[str] = []
    group_pass: list[str] = []
    group_p95: list[str] = []
    for group in dashboard.get("groups", []):
        labels = (
            f'provider="{_label(group.get("provider", ""))}",'
            f'prompt_version="{_label(group.get("prompt_version", ""))}"'
        )
        group_runs.append(f"shotplan_group_runs{{{labels}}} {group.get('runs', 0)}")
        group_pass.append(
            f"shotplan_group_hard_pass_rate{{{labels}}} {group.get('hard_pass_rate', 0.0)}"
        )
        group_p95.append(
            f"shotplan_group_p95_duration_ms{{{labels}}} {group.get('p95_duration_ms', 0.0)}"
        )
    return "\n".join(lines + group_runs + group_pass + group_p95) + "\n"


def build_dashboard(store: RunStore, limit: int = 200) -> dict[str, Any]:
    """Return overall metrics plus one row per (provider, prompt_version) pair."""
    rows = store.stats_rows(limit)
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
                "errors": sum(1 for r in items if r["status"] == "error"),
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
            "Quality metrics (hard_pass_rate, avg_score, p50/p95) use status=ok runs only; "
            "error=provider outage and infeasible=structured refusal are counted separately. "
            "Cost and repairs cover all runs. Offline providers have zero estimated cost."
        ),
    }
