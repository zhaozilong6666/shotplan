"""Refresh the data-driven blocks of docs/index.html from eval artifacts.

The showcase page is committed to the repo and published via GitHub Pages. Its
numbers must never be hand-edited, otherwise they drift from the reports (this
happened once: the page said 102 tests while the suite had 109).

Run after regenerating the eval artifacts:

    python -m evals.run_eval --write-report
    python -m evals.mutation_lab --write
    python -m evals.run_eval --write-report
    python -m evals.repair_lab --write-report
    python -m evals.collect_failures --write-report
    python -m evals.case_miner --write
    python -m evals.http_load --write
    python -m evals.cost_latency --write-report
    python -m evals.benchmark --out evals/benchmark.json --write-report
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


GROUP_ORDER = ["normal", "conflict", "edge", "infeasible"]


def hero_note_block(good: dict, lab: dict, challenge: dict) -> str:
    cases = good["case_count"]
    mutants = lab["mutant_count"]
    return (
        '<p class="sub">\n'
        "      核心不是调模型，而是让质量可被测量：生成前先判定需求是否可满足，"
        "生成后用一层<strong>不经过模型</strong>的硬约束校验器判定，"
        f"并以带<strong>对照组</strong>的 {cases} 个用例守住回归；"
        f"再用 {mutants} 处受控变异验证门禁本身有效，"
        f"用 {challenge['accepted']} 个盲测 brief 检验没见过的组合。\n"
        "      技术栈 Python / FastAPI / SQLite；核心引擎仅标准库，无 API key 也能完整跑通。\n"
        "    </p>"
    )


def challenge_block(mining: dict) -> str:
    return (
        '<div class="metrics challenge">\n'
        f'      <div class="metric good"><b>{mining["unseen_briefs"]}</b>'
        "<span>与主集不重复的盲测 brief</span></div>\n"
        f'      <div class="metric good"><b>{mining["coverage_before"]} → '
        f'{mining["coverage_after"]} / {mining["coverage_total"]}</b>'
        "<span>城市 × 风格覆盖格子</span></div>\n"
        f'      <div class="metric good"><b>{_pct(mining["good"]["hard_pass_rate"])}</b>'
        "<span>盲测集 good 硬约束通过率</span></div>\n"
        f'      <div class="metric bad"><b>{_pct(mining["naive"]["hard_pass_rate"])}</b>'
        "<span>盲测集 naive 硬约束通过率</span></div>\n"
        "    </div>"
    )


def challenge_note_block(mining: dict) -> str:
    return (
        '<p class="section-note challenge-note">\n'
        f'      {mining["candidate_count"]} 个候选中 {mining["rejected"]} 个「同一地点既指定又排除」'
        "被生成前可行性判定挡下；其余全部与主集不重复。"
        f'good 均分 {mining["good"]["avg_score"]}、naive {mining["naive"]["avg_score"]}，'
        f'最小差值 {mining["min_gap"]}。挑战集独立存放，不影响 v1–v6 基线的可比性。\n'
        "    </p>"
    )


def groups_note_block(good: dict) -> str:
    parts = "、".join(
        f"{name} {good['by_group'][name]['cases']}"
        for name in GROUP_ORDER if name in good["by_group"]
    )
    return (
        f'<p class="section-note">{good["case_count"]} 个用例：{parts}。'
        "最后一组描述数学上无法满足的 brief，正确行为是结构化拒绝而非硬凑方案。</p>"
    )


def naive_note_block(good: dict, naive: dict) -> str:
    delta = round(good["avg_score"] - naive["avg_score"], 4)
    infeasible = good["by_group"].get("infeasible", {}).get("cases", 0)
    rest = good["case_count"] - infeasible
    return (
        '<p class="section-note" style="margin-top:14px">\n'
        f'      对照基线 <code>naive</code>：硬约束通过率 {naive["hard_pass_rate"] * 100:.1f}%、'
        f'均分 {naive["avg_score"]:.4f}。差值 {delta:.4f}。\n'
        f"      {infeasible} 个 infeasible 用例因共享生成前判定而两组同分，"
        f"区分度由其余 {rest} 个用例提供——这一点在报告里如实写明。\n"
        "    </p>"
    )


def mutation_block(lab: dict) -> str:
    total = lab["mutant_count"]
    restored = lab["restored_checks"] if lab["restored"] else 0
    return (
        '<div class="metrics mutation">\n'
        f'      <div class="metric good"><b>{lab["caught"]} / {total}</b>'
        "<span>受控变异被门禁拦截</span></div>\n"
        f'      <div class="metric bad"><b>{lab["caught_by_legacy_gate"]} / {total}</b>'
        "<span>旧门禁只能拦住的数量</span></div>\n"
        f'      <div class="metric good"><b>{lab["survivors"]}</b>'
        "<span>存活变异</span></div>\n"
        f'      <div class="metric good"><b>{restored} / {total}</b>'
        "<span>回滚后与基线一致</span></div>\n"
        "    </div>"
    )


def groups_block(good: dict) -> str:
    order = GROUP_ORDER
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


def benchmark_block(bench: dict) -> str:
    single = bench["single"]
    corpus = bench["corpus"]
    return (
        '<div class="metrics perf">\n'
        f'      <div class="metric good"><b>{single["p95_ms"]:.2f} ms</b>'
        "<span>P95 延迟</span></div>\n"
        f'      <div class="metric good"><b>{single["p50_ms"]:.2f} ms</b>'
        "<span>P50 延迟</span></div>\n"
        f'      <div class="metric good"><b>{single["throughput_rps"]:.0f} /s</b>'
        "<span>单核吞吐</span></div>\n"
        f'      <div class="metric good"><b>{single["peak_kb"] / 1024:.2f} MB</b>'
        f'<span>堆峰值（{corpus["cases"]} 用例扫一遍 {corpus["wall_s"]:.2f}s）</span></div>\n'
        "    </div>"
    )


def http_load_block(load: dict) -> str:
    rows = load["scenarios"]
    single = next(r for r in rows if r["workers"] == 1 and r["concurrency"] == 8)
    curve = [r for r in rows if r["concurrency"] == 8]
    best = max(curve, key=lambda r: r["rps"])
    total_ok = sum(r["ok"] for r in rows)
    total_lost = sum(r["lost_writes"] for r in rows)
    return (
        '<div class="metrics http-load">\n'
        f'      <div class="metric good"><b>{single["rps"]:.0f} /s</b>'
        "<span>真实 HTTP 单 worker 并发 8</span></div>\n"
        f'      <div class="metric good"><b>{best["workers"]} worker · {best["rps"]:.0f} /s</b>'
        "<span>本机最佳吞吐</span></div>\n"
        f'      <div class="metric good"><b>{single["p95_ms"]:.1f} ms</b>'
        "<span>单 worker P95</span></div>\n"
        f'      <div class="metric good"><b>{total_ok} / {total_ok - total_lost}</b>'
        "<span>真实请求 / 成功落库</span></div>\n"
        "    </div>"
    )


def http_note_block(load: dict) -> str:
    rows = load["scenarios"]
    single = next(r for r in rows if r["workers"] == 1 and r["concurrency"] == 8)
    curve = [r for r in rows if r["concurrency"] == 8]
    widest = max(curve, key=lambda r: r["workers"])
    ratio = widest["rps"] / single["rps"] if single["rps"] else 0.0
    best = max(curve, key=lambda r: r["rps"])
    return (
        '<p class="section-note http-note">\n'
        f'      {widest["workers"]} 个 worker 的吞吐只有单 worker 的 {ratio:.2f}×：'
        "真实服务里每个请求都要写一条运行记录，多进程抢同一个 SQLite 写锁，"
        f'瓶颈从 GIL 变成了共享状态。本机最佳是 {best["workers"]} worker。'
        "吞吐与 P95 只写进报告，不进 CI 门禁；门禁只查 0 错误、0 丢写与服务没挂住。\n"
        "    </p>"
    )


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
    mutation = _load(ROOT / "evals" / "mutation.json")
    challenge = _load(ROOT / "evals" / "case_mining.json")
    # Single-machine loopback snapshot. Throughput is displayed, never gated.
    http_load = _load(ROOT / "evals" / "http_load.json")
    # Benchmark numbers are machine-specific and cannot be regenerated identically
    # in CI, so the page reads a committed snapshot instead of evals/out/.
    # Refresh it with: python -m evals.benchmark --out evals/benchmark.json
    bench = _load(ROOT / "evals" / "benchmark.json")
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
        r'<p class="sub">.*?</p>',
        lambda m: hero_note_block(latest["good"], mutation, challenge),
        "hero note",
    )
    html = replace_block(
        html,
        r'<div class="metrics">.*?</div>\s*</header>',
        metrics_block(latest["good"], latest["naive"]) + "\n  </header>",
        "metrics",
    )
    html = replace_block(
        html,
        r'(<h2>评测结果</h2>\s*)<p class="section-note">.*?</p>',
        lambda m: m.group(1) + groups_note_block(latest["good"]),
        "groups note",
    )
    html = replace_block(
        html,
        r'<p class="section-note" style="margin-top:14px">.*?</p>',
        lambda m: naive_note_block(latest["good"], latest["naive"]),
        "control note",
    )
    html = replace_block(
        html,
        r'(<h2>评测结果</h2>.*?)\s*<tbody>.*?</tbody>',
        lambda m: m.group(1).rstrip() + "\n" + groups_block(latest["good"]),
        "groups",
    )
    html = replace_block(
        html,
        r'<div class="metrics mutation">.*?</div>\s*</section>',
        mutation_block(mutation) + "\n  </section>",
        "mutation",
    )
    html = replace_block(
        html,
        r'(<h2>修复循环.*?)\s*<div class="cards">.*?</div>\s*</section>',
        lambda m: m.group(1).rstrip() + "\n" + repair_block(repair) + "\n  </section>",
        "repair",
    )
    html = replace_block(
        html,
        r'(<h2>成本与延迟.*?)\s*<tbody>.*?</tbody>',
        lambda m: m.group(1).rstrip() + "\n" + cost_block(cost),
        "cost",
    )
    html = replace_block(
        html,
        r'(<h2>失败案例库.*?)\s*<ul class="clean">.*?</ul>',
        lambda m: m.group(1).rstrip() + "\n" + failures_block(failures),
        "failures",
    )
    html = replace_block(
        html,
        r'<div class="metrics challenge">.*?</div>\s*'
        r'<p class="section-note challenge-note">.*?</p>',
        challenge_block(challenge) + "\n    " + challenge_note_block(challenge),
        "challenge",
    )
    html = replace_block(
        html,
        r'<div class="metrics perf">.*?</div>\s*</section>',
        benchmark_block(bench) + "\n  </section>",
        "benchmark",
    )
    html = replace_block(
        html,
        r'<div class="metrics http-load">.*?</div>\s*'
        r'<p class="section-note http-note">.*?</p>',
        http_load_block(http_load) + "\n    " + http_note_block(http_load),
        "http load",
    )
    page.write_text(html, encoding="utf-8")
    print(f"wrote {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
