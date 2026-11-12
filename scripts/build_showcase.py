"""Refresh the data-driven blocks of docs/index.html from eval artifacts.

The showcase page is committed to the repo and published via GitHub Pages. Its
numbers must never be hand-edited, otherwise they drift from the reports (this
happened once: the page said 102 tests while the suite had 109).

Run after regenerating the eval artifacts:

    python -m evals.run_eval --write-report
    python -m evals.repair_lab --write-report
    python -m evals.collect_failures --write-report
    python -m evals.cost_latency --write-report
    python scripts/build_showcase.py

The script locates each block by its section heading / wrapper and replaces only
the inner content, so the page design stays hand-authored.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = ROOT / "evals" / "out"

FAILURE_LABELS = {
    "TRANSFER_BUFFER": "跨景点没留转场",
    "BUDGET_EXCEEDED": "总花费超预算",
    "UNKNOWN_LOCATION": "编造不存在的地点",
    "TIME_OVERRUN": "时间轴超出预约时长",
    "TIME_OVERLAP": "时间块重叠",
    "MISSING_ACTIVITY": "缺少必需环节",
    "MAKEUP_ORDER": "妆造排在拍摄之后",
    "STYLE_MISMATCH": "风格与场景不匹配",
    "NO_ALTERNATIVE": "没有备选方案",
    "NO_SOURCE_REF": "缺少知识库引用",
}


def _load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(
            f"missing {path}. Run the corresponding eval command first "
            "(see the module docstring)."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def metrics_block(good: dict, naive: dict) -> str:
    delta = round(good["avg_score"] - naive["avg_score"], 4)
    return (
        '<div class="metrics">\n'
        f'      <div class="metric good"><b>{good["cases_passed"]} / {good["case_count"]}</b>'
        "<span>评测用例通过</span></div>\n"
        f'      <div class="metric good"><b>{good["avg_score"]:.4f}</b>'
        "<span>规则 Agent 均分</span></div>\n"
        f'      <div class="metric good"><b>{_pct(good["hard_pass_rate"])}</b>'
        "<span>硬约束通过率</span></div>\n"
        f'      <div class="metric bad"><b>{delta:.4f}</b>'
        "<span>与对照组的差值</span></div>\n"
        "    </div>"
    )


def groups_block(good: dict) -> str:
    order = ["normal", "conflict", "edge", "infeasible"]
    rows = []
    for name in order:
        stats = good["by_group"].get(name)
        if not stats:
            continue
        rows.append(
            f'        <tr><td>{name}</td><td class="num">{stats["cases"]}</td>'
            f'<td class="num pass">{_pct(stats["hard_pass_rate"])}</td>'
            f'<td class="num">{stats["avg_score"]:.4f}</td>'
            f'<td class="num">{stats["passed"]} / {stats["cases"]}</td></tr>'
        )
    rows.append(
        f'        <tr><td><strong>合计</strong></td>'
        f'<td class="num"><strong>{good["case_count"]}</strong></td>'
        f'<td class="num pass"><strong>{_pct(good["hard_pass_rate"])}</strong></td>'
        f'<td class="num"><strong>{good["avg_score"]:.4f}</strong></td>'
        f'<td class="num"><strong>{good["cases_passed"]} / {good["case_count"]}</strong></td></tr>'
    )
    return "      <tbody>\n" + "\n".join(rows) + "\n      </tbody>"


def repair_block(repair: dict) -> str:
    arms = repair["arms"]
    rep = arms["repairable/repairs=1"]
    naive = arms["naive/repairs=1"]
    reg = arms["regressing/repairs=1"]
    return (
        '    <div class="cards">\n'
        '      <div class="card">\n'
        '        <span class="tag good">有效</span>\n'
        "        <h3>repairable</h3>\n"
        f'        <p>首轮 {_pct(rep["first_pass_rate"])} → 修复后 {_pct(rep["after_pass_rate"])}，'
        f'一次修复成功率 <strong>{rep["repaired_successfully"]}/{rep["repairable_cases"]}</strong>。'
        "说明校验报告作为修复提示是有效的。</p>\n"
        "      </div>\n"
        '      <div class="card">\n'
        '        <span class="tag warn">无效</span>\n'
        "        <h3>naive</h3>\n"
        f'        <p>忽略修复提示，<strong>{naive["rejected"]} 次</strong>候选全部被拒绝，'
        "通过率不升。修复效果取决于 provider 是否真的使用报告。</p>\n"
        "      </div>\n"
        '      <div class="card">\n'
        '        <span class="tag">被拦截</span>\n'
        "        <h3>regressing</h3>\n"
        f'        <p>「越修越差」的 <strong>{reg["rejected"]} 次</strong>候选全被单调保护拦下，'
        "均分不降。有修复循环不等于质量会变好。</p>\n"
        "      </div>\n"
        "    </div>"
    )


def cost_block(cost: dict) -> str:
    rows = []
    for card in cost["cards"]:
        per_day = card["cost_per_call_cny"] * 100
        rows.append(
            f'        <tr><td><code>{card["model"]}</code></td>'
            f'<td class="num">{card["cost_per_call_cny"]:.6f} 元</td>'
            f'<td class="num">{card["p50_duration_ms"]:.0f} / {card["p95_duration_ms"]:.0f} ms</td>'
            f'<td class="num">{per_day:.2f} 元</td></tr>'
        )
    return "      <tbody>\n" + "\n".join(rows) + "\n      </tbody>"


def failures_block(failures: list[dict]) -> str:
    counts = Counter(record["code"] for record in failures)
    items = []
    for code, count in counts.most_common():
        label = FAILURE_LABELS.get(code, "其他硬约束违规")
        items.append(f'          <li><code>{code}</code><span>{count} 次 · {label}</span></li>')
    return '        <ul class="clean">\n' + "\n".join(items) + "\n        </ul>"


def replace_block(html: str, pattern: str, replacement: str, name: str) -> str:
    new_html, count = re.subn(pattern, replacement, html, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"could not locate block: {name}")
    print(f"{name}: updated")
    return new_html


def main() -> int:
    latest = _load(OUT / "latest.json")
    repair = _load(OUT / "repair-experiment.json")
    cost = _load(OUT / "cost-latency.json")
    failures_path = ROOT / "evals" / "failures" / "cases.jsonl"
    if not failures_path.exists():
        raise SystemExit(f"missing {failures_path}; run evals.collect_failures first")
    failures = [
        json.loads(line)
        for line in failures_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    page = DOCS / "index.html"
    html = page.read_text(encoding="utf-8")
    html = replace_block(
        html,
        r'<div class="metrics">.*?</div>\s*</header>',
        metrics_block(latest["good"], latest["naive"]) + "\n  </header>",
        "metrics",
    )
    html = replace_block(
        html,
        r'(<h2>评测结果</h2>.*?)<tbody>.*?</tbody>',
        lambda m: m.group(1) + groups_block(latest["good"]),
        "groups",
    )
    html = replace_block(
        html,
        r'(<h2>修复循环.*?)<div class="cards">.*?</div>\s*</section>',
        lambda m: m.group(1) + repair_block(repair) + "\n  </section>",
        "repair",
    )
    html = replace_block(
        html,
        r'(<h2>成本与延迟.*?)<tbody>.*?</tbody>',
        lambda m: m.group(1) + cost_block(cost),
        "cost",
    )
    html = replace_block(
        html,
        r'(<h2>失败案例库.*?)<ul class="clean">.*?</ul>',
        lambda m: m.group(1) + failures_block(failures),
        "failures",
    )
    page.write_text(html, encoding="utf-8")
    print(f"wrote {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
