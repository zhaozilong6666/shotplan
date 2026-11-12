"""Command line entry point. Works with a bare Python install."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .agents import MockProvider, build_provider
from .config import get_settings
from .domain.constraints import RULES
from .domain.errors import ShotplanError
from .domain.models import PlanRequest
from .pipeline import run_plan
from .storage import RunStore
from .tracing import trace_to_markdown


def _print_plan(result: Any) -> None:
    plan, report = result.plan, result.validation
    print(f"\n方案 {plan.plan_id}  |  {plan.city}  {plan.date}  |  provider={plan.provider}")
    print("-" * 78)
    for slot in plan.slots:
        print(
            f"  {slot.start}-{slot.end}  {slot.activity:<7} {slot.label:<18} "
            f"{slot.location_id:<24} {slot.cost_cny:>5} 元"
        )
    print("-" * 78)
    print(f"  总花费 {plan.total_cost_cny} 元 / 预算 {result.request.budget_cny} 元")
    print(f"  得分 {report.score:.3f}  硬约束 {'通过' if report.hard_pass else '未通过'}")
    for key, value in report.scores.items():
        print(f"    {key:<14} {value:.3f}")
    if report.issues:
        print("  问题:")
        for issue in report.issues:
            print(f"    [{issue.severity}] {issue.code}: {issue.message}")
    if plan.alternatives:
        print("  备选:")
        for alt in plan.alternatives:
            print(f"    - ({alt.get('trigger')}) {alt.get('description')}")
    print()
    print(trace_to_markdown(result.trace))


def cmd_plan(args: argparse.Namespace) -> int:
    request = PlanRequest(
        city=args.city,
        date=args.date,
        hours=args.hours,
        budget_cny=args.budget,
        party_size=args.party,
        styles=args.style.split(",") if args.style else [],
        preferences=args.preference.split(",") if args.preference else [],
        avoid=args.avoid.split(",") if args.avoid else [],
    )
    if args.mock_mode:
        provider = MockProvider(mode=args.mock_mode)
    else:
        provider = build_provider(get_settings())
    settings = get_settings()
    store = RunStore(settings.db_path) if args.persist else None
    result = run_plan(request, provider=provider, store=store, settings=settings)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_plan(result)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已写入 {args.out}")
    return 0 if result.validation.hard_pass else 2


def cmd_rules(_args: argparse.Namespace) -> int:
    print(f"{'CODE':<20} {'SEVERITY':<9} RULE / WHY")
    print("-" * 100)
    for rule in RULES:
        print(f"{rule['code']:<20} {rule['severity']:<9} {rule['rule']}")
        print(f"{'':<30} └─ {rule['why']}")
    return 0


def cmd_config(_args: argparse.Namespace) -> int:
    print(json.dumps(get_settings().masked(), ensure_ascii=False, indent=2))
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    rows = RunStore(get_settings().db_path).recent(args.limit)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from evals.run_eval import main as eval_main

    return eval_main(
        ["--write-report"] if args.write_report else []
    )


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("serve 需要可选依赖：python -m pip install -e '.[api]'", file=sys.stderr)
        return 1
    uvicorn.run("shotplan.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shotplan",
        description="可评测、带硬约束校验的旅拍方案 Agent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="生成一份方案")
    p.add_argument("--city", default="xian")
    p.add_argument("--date", default="2026-10-04")
    p.add_argument("--hours", type=float, default=6.0)
    p.add_argument("--budget", type=int, default=1500)
    p.add_argument("--party", type=int, default=1)
    p.add_argument("--style", default="tang", help="逗号分隔，如 tang,song")
    p.add_argument("--preference", default="")
    p.add_argument("--avoid", default="")
    p.add_argument("--mock-mode", choices=["good", "naive"], help="强制使用离线 mock")
    p.add_argument("--json", action="store_true", help="输出完整 JSON")
    p.add_argument("--out", help="把结果写入文件")
    p.add_argument("--persist", action="store_true", help="写入 SQLite 运行历史")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("rules", help="列出全部硬/软约束")
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("config", help="显示当前配置（隐藏密钥）")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("history", help="查看最近运行记录")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("eval", help="运行评测集")
    p.add_argument("--write-report", action="store_true")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("serve", help="启动 Web / API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ShotplanError as exc:
        print(f"shotplan error: {exc}", file=sys.stderr)
        return 1
    except (ValueError, KeyError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
