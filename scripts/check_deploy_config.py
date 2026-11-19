"""The Prometheus config has to be deployable, not just described in a doc.

`docs/slo.md` already carries the alert rules as a fenced YAML block. Copying that
block into `deploy/` by hand guarantees it will drift: the doc says one threshold,
the running Prometheus alerts on another. So the rules file is generated from the
doc, and this checker refuses a stale copy.

It also validates what can be checked without a Prometheus server:

* the scrape config points at `/metrics` and loads the generated rules file;
* every alert has an expression, a `for`, a severity and a summary;
* every `shotplan_*` metric named in an expression exists in `observability.py`
  (a renamed metric would otherwise fail silently at scrape time).

    python scripts/check_deploy_config.py --write   # regenerate rules from docs/slo.md
    python scripts/check_deploy_config.py --check   # CI
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SLO = ROOT / "docs" / "slo.md"
DEPLOY = ROOT / "deploy" / "prometheus"
SCRAPE = DEPLOY / "shotplan.yml"
RULES = DEPLOY / "shotplan.rules.yml"
OBSERVABILITY = ROOT / "src" / "shotplan" / "observability.py"

RULE_BLOCK = re.compile(r"```yaml\n(groups:\n.*?)```", re.DOTALL)
METRIC = re.compile(r"\b(shotplan_[a-z0-9_]+|probe_success|up)\b")
EXTERNAL = {"up", "probe_success"}


def extract_rules() -> str:
    text = SLO.read_text(encoding="utf-8")
    match = RULE_BLOCK.search(text)
    if not match:
        raise SystemExit("docs/slo.md 里找不到以 groups: 开头的 yaml 代码块")
    return match.group(1).rstrip() + "\n"


def known_metrics() -> set[str]:
    text = OBSERVABILITY.read_text(encoding="utf-8")
    return set(re.findall(r"shotplan_[a-z0-9_]+", text))


def validate(scrape: dict[str, Any], rules: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    jobs = scrape.get("scrape_configs") or []
    job = next((item for item in jobs if item.get("job_name") == "shotplan"), None)
    if job is None:
        problems.append("scrape_configs 里没有 job_name=shotplan")
    else:
        if job.get("metrics_path") != "/metrics":
            problems.append("shotplan 的 metrics_path 必须是 /metrics")
        targets = (job.get("static_configs") or [{}])[0].get("targets") or []
        if not targets:
            problems.append("shotplan 没有 static_configs.targets")
    if "shotplan.rules.yml" not in (scrape.get("rule_files") or []):
        problems.append("rule_files 没有加载 shotplan.rules.yml")

    groups = rules.get("groups") or []
    names: set[str] = set()
    metrics = known_metrics()
    count = 0
    for group in groups:
        for rule in group.get("rules") or []:
            if "alert" not in rule:
                continue
            count += 1
            name = str(rule["alert"])
            if name in names:
                problems.append(f"告警名重复：{name}")
            names.add(name)
            expr = str(rule.get("expr") or "").strip()
            if not expr:
                problems.append(f"{name} 没有 expr")
            if not rule.get("for"):
                problems.append(f"{name} 没有 for（短窗口抖动会直接告警）")
            severity = (rule.get("labels") or {}).get("severity")
            if severity not in {"warning", "critical"}:
                problems.append(f"{name} 的 severity 必须是 warning/critical")
            if not (rule.get("annotations") or {}).get("summary"):
                problems.append(f"{name} 没有 annotations.summary")
            for metric in set(METRIC.findall(expr)):
                if metric not in EXTERNAL and metric not in metrics:
                    problems.append(f"{name} 引用了不存在的指标：{metric}")
    if count == 0:
        problems.append("rules 里没有任何 alert")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = extract_rules()
    if "--write" in args:
        DEPLOY.mkdir(parents=True, exist_ok=True)
        RULES.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {RULES.relative_to(ROOT).as_posix()}")
    if not SCRAPE.exists() or not RULES.exists():
        print("缺少 deploy/prometheus 配置，先运行 --write", file=sys.stderr)
        return 1
    if RULES.read_text(encoding="utf-8") != text:
        print("shotplan.rules.yml 与 docs/slo.md 不一致，运行 --write", file=sys.stderr)
        return 1
    try:
        scrape = yaml.safe_load(SCRAPE.read_text(encoding="utf-8")) or {}
        rules = yaml.safe_load(RULES.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        print(f"YAML 解析失败：{exc}", file=sys.stderr)
        return 1
    problems = validate(scrape, rules)
    for problem in problems:
        print(f"DEPLOY FAILED: {problem}", file=sys.stderr)
    print(
        f"deploy config: {len((rules.get('groups') or [{}])[0].get('rules') or [])} alerts, "
        f"{len(known_metrics())} app metrics"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
