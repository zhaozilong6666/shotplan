"""Keep the headline numbers in README truthful.

The repo already gates generated reports, gate counts, knowledge freshness and
snapshot hygiene. But claims like "80 cases / 200 blind briefs / 15 rules" were
still prose that nothing checked. This script derives each number from its
source of truth and fails if README drifts.

    python scripts/check_doc_claims.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
sys.path.insert(0, str(ROOT / "src"))


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def actuals() -> dict[str, int]:
    from shotplan.domain.constraints import RULES

    cases = [row for path in sorted((ROOT / "evals/cases").glob("*.jsonl")) for row in _jsonl(path)]
    challenge = [row for path in sorted((ROOT / "evals/challenge").glob("*.jsonl")) for row in _jsonl(path)]
    failures = _jsonl(ROOT / "evals/failures/cases.jsonl")
    locations = json.loads(
        (ROOT / "src/shotplan/knowledge/data/locations.json").read_text(encoding="utf-8")
    )
    adrs = len(list((ROOT / "docs/adr").glob("*.md")))
    infeasible = sum(1 for case in cases if case["group"] == "infeasible")
    return {
        "cases": len(cases),
        "planning_cases": len(cases) - infeasible,
        "infeasible_cases": infeasible,
        "challenge": len(challenge),
        "failures": len(failures),
        "rules": len(RULES),
        "hard_rules": sum(1 for rule in RULES if rule["severity"] == "hard"),
        "soft_rules": sum(1 for rule in RULES if rule["severity"] == "soft"),
        "cities": len(locations["cities"]),
        "adrs": adrs,
    }


def claims(text: str) -> list[tuple[str, int, str]]:
    """(claim name, declared number, matched text)."""
    # Patterns are deliberately specific. A loose `(\d+) 个用例` also matches
    # "在这 11 个用例上" and "其余 69 个用例", and `(\d+) 硬` matches a score
    # table cell "0.975 硬约束" -- both produced false failures in the first
    # version of this gate.
    patterns = {
        "cases": r"(?:建了|用了)?\s*(\d+)\s*个回归用例|(\d+)\s*个用例分四组",
        "planning_cases": r"其余\s*(\d+)\s*个用例|(\d+)\s*个用例需要生成方案",
        "infeasible_cases": r"(\d+)\s*个不可行用例|在这\s*(\d+)\s*个用例上",
        "challenge": r"(\d+)\s*个盲测\s*brief",
        "failures": r"(\d+)\s*条失败记录",
        "rules": r"(\d+)\s*条(?:确定性|可测试的)?规则",
        "hard_soft": r"(\d+)\s*条硬约束\s*\+\s*(\d+)\s*项软评分",
        "cities": r"城市从\s*(\d+)\s*个",
        "adrs": r"(\d+)\s*份\s*ADR",
    }
    found: list[tuple[str, int, str]] = []
    for name, pattern in patterns.items():
        for match in re.finditer(pattern, text):
            groups = [int(g) for g in match.groups() if g is not None]
            if name == "hard_soft":
                found.append(("hard_rules", groups[0], match.group(0)))
                found.append(("soft_rules", groups[1], match.group(0)))
            else:
                found.append((name, groups[0], match.group(0)))
    return found


def main() -> int:
    values = actuals()
    text = README.read_text(encoding="utf-8")
    found = claims(text)
    problems = []
    # A test count is self-referential: every new test changes it. Ban the
    # handwritten number instead of chasing it forever.
    handwritten_tests = re.findall(r"\d+\s*个单元测试", text)
    if handwritten_tests:
        problems.append(
            f"do not hardcode the test count ({handwritten_tests}); "
            "read it with `pytest --collect-only` instead"
        )
    for name, declared, raw in found:
        if declared != values[name]:
            problems.append(f"{raw!r}: README says {declared}, source says {values[name]}")

    print("doc claims (source of truth): " + ", ".join(f"{k}={v}" for k, v in sorted(values.items())))
    print(f"checked {len(found)} numeric claims in README.md")
    for problem in problems:
        print(f"  FAIL {problem}", file=sys.stderr)
    if not found:
        print("  FAIL no numeric claims found -- did README change shape?", file=sys.stderr)
        return 1
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
