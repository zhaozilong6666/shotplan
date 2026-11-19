"""What breaks when you change one rule constant, derived from the code itself.

`docs/thresholds.md` answers "is this number measured or chosen". It does not
answer the next question, which is the one that actually causes outages:
**"if I change it, which copies have to move with it, and what will notice if I
miss one?"**

That answer is spread across five places: the definition, the modules that bind
the name, the probe groups that sit on its boundary, the mutation lab that patrols
for a one-sided edit, and the rule table that renders the number into user-facing
text. A hand-written checklist would be stale within a week -- the same week this
project already caught a copied `20` that the mutation lab had to be taught to
see (see `docs/mutation-report.md`).

So this page is generated. It reads the AST of `constraints.py`, the import graph
of `src/ evals/ scripts/ tests/`, the `CONSTANTS` table in `evals/sensitivity.py`,
the `GROUPS`/`run_*_group` functions in `evals/probe_lab.py`, and the `MUTANTS`
table in `evals/mutation_lab.py`. Nothing here is typed by hand.

What it cannot see: a module that copies the literal number instead of importing
the name. AST knows names, not intent. That gap is exactly why `mutation_lab`
patches module attributes and why the copy that slipped through is recorded as a
pitfall in the project ledger -- the table tells you where the guards are, not
that they are complete.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_thresholds as thresholds  # noqa: E402

SOURCE = ROOT / "src" / "shotplan" / "domain" / "constraints.py"
SENSITIVITY = ROOT / "evals" / "sensitivity.py"
PROBE_LAB = ROOT / "evals" / "probe_lab.py"
MUTATION_LAB = ROOT / "evals" / "mutation_lab.py"
PROBES = ROOT / "evals" / "probes"
REPORT = ROOT / "docs" / "constant-checklist.md"

CONSTRAINT_MODULE = "shotplan.domain.constraints"
SCAN_ROOTS = (("src", ROOT / "src"), ("evals", ROOT / "evals"),
              ("scripts", ROOT / "scripts"), ("tests", ROOT / "tests"))
MAX_SITES = 6


# ------------------------------------------------------------------ parsing ---
def assigned_value(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.Assign):
        return node.value
    if isinstance(node, ast.AnnAssign):
        return node.value
    return None


def assigned_names(node: ast.AST) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
    return [t.id for t in targets if isinstance(t, ast.Name)]


def parse(path: Path) -> ast.Module:
    # 仓库里有带 BOM 的历史文件，utf-8 会把 BOM 当正文，ast.parse 直接报错。
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def iter_sources() -> Iterable[tuple[str, Path, ast.Module]]:
    for area, base in SCAN_ROOTS:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield area, path, parse(path)


def literal(node: ast.AST | None) -> Any:
    try:
        return ast.literal_eval(node) if node is not None else None
    except (ValueError, SyntaxError):
        return None


def const_definitions() -> dict[str, int]:
    """Constant name -> definition line, for the "定义在" line."""
    tree = parse(SOURCE)
    lines: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    lines[target.id] = node.lineno
    return lines


def rule_codes() -> set[str]:
    tree = parse(SOURCE)
    codes: set[str] = set()
    for node in tree.body:
        value = assigned_value(node)
        if value is None or "RULES" not in assigned_names(node):
            continue
        for item in getattr(value, "elts", []):
            if not isinstance(item, ast.Dict):
                continue
            for key, entry in zip(item.keys, item.values, strict=False):
                if literal(key) == "code" and isinstance(literal(entry), str):
                    codes.add(str(literal(entry)))
    return codes


@dataclass
class Binding:
    area: str
    path: str
    lines: list[int] = field(default_factory=list)


def bindings() -> dict[str, list[Binding]]:
    """Every module that binds a constant **by name**, and where it uses it."""
    names = {row["name"] for row in thresholds.collect()}
    found: dict[str, list[Binding]] = {name: [] for name in names}
    for area, path, tree in iter_sources():
        if path == SOURCE:
            continue
        direct: dict[str, str] = {}
        modules: set[str] = set()
        import_lines: set[int] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "constraints" or node.module.endswith(".constraints"))
            ):
                import_lines.add(node.lineno)
                for alias in node.names:
                    if alias.name in names:
                        direct[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == CONSTRAINT_MODULE and alias.asname:
                        modules.add(alias.asname)
        used: dict[str, set[int]] = {name: set() for name in direct.values()}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in direct and node.lineno not in import_lines:
                used[direct[node.id]].add(node.lineno)
            elif (
                isinstance(node, ast.Attribute)
                and node.attr in names
                and isinstance(node.value, ast.Name)
                and node.value.id in modules
            ):
                used.setdefault(node.attr, set()).add(node.lineno)
        for name, lines in used.items():
            if lines:
                rel = path.relative_to(ROOT).as_posix()
                found[name].append(Binding(area, rel, sorted(lines)))
    return found


def sensitivity_roles() -> dict[str, str]:
    """Constant -> "hard:RULE" / "soft:RULE" / "gate:..." from the scan table."""
    tree = parse(SENSITIVITY)
    roles: dict[str, str] = {}
    for node in ast.walk(tree):
        value = assigned_value(node)
        if value is None or "CONSTANTS" not in assigned_names(node):
            continue
        for item in getattr(value, "elts", []):
            if isinstance(item, (ast.Tuple, ast.List)) and len(item.elts) >= 4:
                name, role = literal(item.elts[0]), literal(item.elts[3])
                if isinstance(name, str) and isinstance(role, str):
                    roles[name] = role
    return roles


def rule_table() -> dict[str, list[tuple[str, str]]]:
    """Constant -> [(rule code, severity)] from the f-strings in `RULES`.

    Sensitivity only lists six constants; the rule table renders most of the rest
    straight into user-facing text. Reading the f-string AST keeps the mapping from
    drifting when a rule is reworded.
    """
    tree = parse(SOURCE)
    constants = {row["name"] for row in thresholds.collect()}
    mapping: dict[str, list[tuple[str, str]]] = {}
    for node in tree.body:
        value = assigned_value(node)
        if value is None or "RULES" not in assigned_names(node):
            continue
        for item in getattr(value, "elts", []):
            if not isinstance(item, ast.Dict):
                continue
            code = severity = None
            rule_node: ast.AST | None = None
            for key, entry in zip(item.keys, item.values, strict=False):
                name = literal(key)
                if name == "code":
                    code = literal(entry)
                elif name == "severity":
                    severity = literal(entry)
                elif name == "rule":
                    rule_node = entry
            if not isinstance(code, str) or rule_node is None:
                continue
            for sub in ast.walk(rule_node):
                if isinstance(sub, ast.Name) and sub.id in constants:
                    mapping.setdefault(sub.id, []).append((code, str(severity or "?")))
    return mapping


def internal_uses(constants: set[str]) -> dict[str, list[int]]:
    """Uses inside `constraints.py` itself: functions and the `RULES` f-strings."""
    tree = parse(SOURCE)
    definitions: set[tuple[str, int]] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in constants:
                    definitions.add((target.id, node.lineno))
    uses: dict[str, set[int]] = {}
    for sub in ast.walk(tree):
        if (
            isinstance(sub, ast.Name)
            and sub.id in constants
            and (sub.id, sub.lineno) not in definitions
        ):
            uses.setdefault(sub.id, set()).add(sub.lineno)
    return {name: sorted(lines) for name, lines in uses.items()}


def role_label(name: str, roles: dict[str, str], rules: dict[str, list[tuple[str, str]]]) -> str:
    if name in roles:
        return roles[name]
    entries = rules.get(name, [])
    if entries:
        return "、".join(f"{severity}:{code}" for code, severity in entries)
    return "—"


def probe_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    if not PROBES.exists():
        return counts
    for path in sorted(PROBES.glob("*.jsonl")):
        counts[path.stem] = sum(
            1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    return counts


def probe_guards(
    codes: set[str],
    roles: dict[str, str],
    rules: dict[str, list[tuple[str, str]]],
) -> dict[str, list[str]]:
    """Constant -> probe group keys, from `Group(...)` and the forged time group."""
    tree = parse(PROBE_LAB)
    guards: dict[str, list[str]] = {}
    constants = {row["name"] for row in thresholds.collect()}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Group":
            if len(node.args) >= 2:
                key, const = literal(node.args[0]), literal(node.args[1])
                if isinstance(key, str) and const in constants:
                    guards.setdefault(str(const), []).append(key)
    # Groups that forge the output instead of patching a constant (currently
    # `run_time_group`): the function body names the rule code it exercises, so
    # map code -> constant through the sensitivity table.
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("run_"):
            continue
        key = node.name[len("run_"): -len("_group")] if node.name.endswith("_group") else ""
        key = key.replace("_", "-")
        if not key:
            continue
        segment = ast.get_source_segment(PROBE_LAB.read_text(encoding="utf-8-sig"), node) or ""
        for code in codes:
            if code not in segment:
                continue
            for name, role in roles.items():
                if role.endswith(f":{code}"):
                    guards.setdefault(name, []).append(key)
            for name, entries in rules.items():
                if any(entry[0] == code for entry in entries):
                    guards.setdefault(name, []).append(key)
    return guards


def mutant_guards() -> dict[str, list[str]]:
    """Constant -> mutation keys that patch it and must turn red."""
    tree = parse(MUTATION_LAB)
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    guards: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        value = assigned_value(node)
        if value is None or "MUTANTS" not in assigned_names(node):
            continue
        for item in getattr(value, "elts", []):
            if not isinstance(item, ast.Call) or len(item.args) < 4:
                continue
            key = literal(item.args[0])
            func = item.args[3]
            if not isinstance(key, str) or not isinstance(func, ast.Name):
                continue
            body = functions.get(func.id)
            if body is None:
                continue
            for call in ast.walk(body):
                if not isinstance(call, ast.Call):
                    continue
                if not (isinstance(call.func, ast.Attribute) and call.func.attr == "object"):
                    continue
                if len(call.args) >= 2 and isinstance(literal(call.args[1]), str):
                    guards.setdefault(str(literal(call.args[1])), []).append(key)
    return guards


# ----------------------------------------------------------------- rendering ---
def area_counts(items: list[Binding]) -> str:
    counts = {area: 0 for area in ("src", "evals", "scripts", "tests")}
    for item in items:
        counts[item.area] = counts.get(item.area, 0) + 1
    parts = [f"{counts[a]} {a}" for a in ("src", "evals", "tests") if counts[a]]
    if counts.get("scripts"):
        parts.append(f"{counts['scripts']} scripts")
    return " / ".join(parts) if parts else "0"


def render() -> str:
    rows = thresholds.collect()
    defs = const_definitions()
    bound = bindings()
    roles = sensitivity_roles()
    rules = rule_table()
    guards = probe_guards(rule_codes(), roles, rules)
    mutants = mutant_guards()
    probe_n = probe_counts()
    internal = internal_uses({row["name"] for row in rows})

    lines = [
        "> 由 `python scripts/build_constant_checklist.py --write` 生成，请勿手工编辑。",
        "",
        "# 改一个规则常量之前，先看这张表",
        "",
        "`docs/thresholds.md` 回答「这个数是量出来的还是拍的」；这张表回答下一个问题，"
        "也是真正会出事的问题：**改它的时候，哪些地方必须跟着动，漏了会被谁抓到。**",
        "",
        "答案分散在五处：定义、按名绑定它的模块、卡在它边界上的探针组、专门抓「只改一侧」"
        "的变异实验，以及把数字渲染进规则文本的 `RULES` 表。手写清单一周就会过期——"
        "这个项目已经抓到过一次复制出来的 `20`，所以这里全部从代码读。",
        "",
        "## 怎么用",
        "",
        "1. 只改 `constraints.py` 里的定义。下表里的模块都是**按名绑定**，改源即生效。",
        "2. 新模块要用这个数，就 `from shotplan.domain.constraints import ...`，不要写字面量。",
        "3. 改完跑 `make gates`。有探针的常量会在对应组里被检查：`transfer` / `budget` 用"
        "one-sided 组模拟「只改校验端、忘了生成端」，`time` 则直接伪造一个超时方案。",
        "4. 如果改了值，`RULES` 里的规则文本会跟着变（f-string），但 `docs/` 下的解释文字"
        "不会——`scripts/check_doc_claims.py` 会盯住其中一部分。",
        "",
        "## 总览",
        "",
        "| 常量 | 值 | 依据 | 按名绑定 | 规则 | 探针 | 变异 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda item: item["name"]):
        name = row["name"]
        role = role_label(name, roles, rules)
        probe = "、".join(sorted(set(guards.get(name, [])))) or "—"
        mutant = "、".join(sorted(set(mutants.get(name, [])))) or "—"
        basis = "实测" if row["measured"] else "设计选择"
        lines.append(
            f"| `{name}` | {row['value']} | {basis} | {area_counts(bound[name])} "
            f"| `{role}` | {probe} | {mutant} |"
        )
    lines.append("")

    lines += ["## 逐个常量", ""]
    for row in sorted(rows, key=lambda item: item["name"]):
        name = row["name"]
        items = bound[name]
        role = role_label(name, roles, rules)
        probe_keys = sorted(set(guards.get(name, [])))
        mutant_keys = sorted(set(mutants.get(name, [])))
        lines.append(f"### `{name} = {row['value']}`")
        lines.append("")
        lines.append(f"- 定义：`src/shotplan/domain/constraints.py:{defs.get(name, '?')}`")
        if row["note"]:
            lines.append(f"- 注释：{row['note']}")
        if items:
            sites = sum(len(item.lines) for item in items)
            listing = "；".join(
                f"`{item.path}`（{', '.join(f'L{line}' for line in item.lines[:4])}"
                f"{'…' if len(item.lines) > 4 else ''}）"
                for item in items
            )
            lines.append(f"- 按名绑定（{len(items)} 个模块 / {sites} 处使用）：{listing}")
        else:
            lines.append("- 按名绑定：无（只在本文件内使用）")
        sites_in_file = internal.get(name, [])
        if sites_in_file:
            refs = ", ".join(f"L{line}" for line in sites_in_file[:6])
            more = "…" if len(sites_in_file) > 6 else ""
            lines.append(f"- 同文件内使用：{refs}{more}")
        lines.append(f"- 规则：`{role}`")
        if probe_keys:
            detail = "、".join(f"`{key}`（{probe_n.get(key, '?')} 条 brief）" for key in probe_keys)
            lines.append(f"- 探针：{detail}")
        else:
            lines.append("- 探针：无")
        if mutant_keys:
            lines.append(f"- 变异：{'、'.join(f'`{key}`' for key in mutant_keys)}")
        else:
            lines.append("- 变异：无")

        if probe_keys:
            verdict = (
                "只改一侧（比如只收紧校验端）时，`probe_lab` 的 one-sided 组会掉通过率——"
                "这正是「漏改一处」的典型症状。"
            )
        elif mutant_keys:
            verdict = "`mutation_lab` 会对这个常量做单侧变异，护栏失效时报告会红。"
        else:
            verdict = "没有专门的探针或变异；改动只能靠全量评测与测试兜底。"
        lines.append(f"- 漏改会怎样：{verdict}")
        lines.append("")

    uncovered = [
        row["name"]
        for row in rows
        if not guards.get(row["name"]) and not mutants.get(row["name"])
    ]
    lines += [
        "## 没有探针或变异的常量",
        "",
        "这些数改了之后，没有任何一组探针或变异会专门变红。它们不是不能改，"
        "而是改完只能看全量评测——先开一条实验、写下预测，再动。注意：这不等于"
        "「没有测试」，只是本表的探针/变异两列都不覆盖它。",
        "",
    ]
    if uncovered:
        for name in sorted(uncovered):
            lines.append(f"- `{name}`")
    else:
        lines.append("没有。")
    lines.append("")

    lines += [
        "## 这张表看不见的",
        "",
        "- **复制成字面量的副本**：AST 只认名字。如果某个模块自己写了 `FIXED_MINUTES = 20`，"
        "它不会出现在「按名绑定」里。`mutation_lab` 的变异能抓到其中一部分，抓不到全部。",
        "- **文档里的数字**：`docs/` 有些正文仍然手写阈值。`check_doc_claims.py` 只覆盖"
        "它能解析的那部分声明。",
        "- **运行时才绑定的名字**：`patch.object(module, \"NAME\", ...)` 这类字符串引用"
        "不算 import，只有出现在 `tests/` 或 `evals/` 的补丁里时才会被本表看到。",
        "- **单元测试里的边界断言**：对 `min_budget_cny` 这类派生值的断言会挡住改动，但它"
        "既不是探针也不是变异，不会出现在上表的两列里。`变异` 列只表示 mutation_lab 的 "
        "80 条评测语料看不看得见这个常量。",
        "",
        f"统计：{len(rows)} 个标量常量，{len([r for r in rows if guards.get(r['name'])])} 个有探针，"
        f"{len([r for r in rows if mutants.get(r['name'])])} 个有变异，{len(uncovered)} 个两者都没有。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    text = render()
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT).as_posix()}")
    if "--check" in sys.argv or "--write" in sys.argv:
        if not REPORT.exists():
            print(f"缺少 {REPORT.name}，运行 --write", file=sys.stderr)
            return 1
        if REPORT.read_text(encoding="utf-8") != text:
            print(f"{REPORT.name} 与代码不一致，运行 --write", file=sys.stderr)
            return 1
    print(f"constant checklist: {len(thresholds.collect())} constants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
