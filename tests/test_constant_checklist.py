"""The checklist has to be derived from code, not asserted by hand.

Each test pins one link in the chain: relative imports are seen, probe groups are
mapped from both the patching and the forging lab, mutations are attributed to the
constant they patch, and the rendered page is portable and complete.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_constant_checklist as bc  # noqa: E402


def test_bindings_see_relative_imports():
    bound = bc.bindings()
    paths = {item.path for item in bound["MIN_TRANSFER_MINUTES"]}
    assert "src/shotplan/agents/mock.py" in paths
    assert "src/shotplan/agents/validator.py" in paths
    assert any(path.startswith("tests/") for path in paths)


def test_probe_guards_cover_patched_and_forged_groups():
    roles = bc.sensitivity_roles()
    guards = bc.probe_guards(bc.rule_codes(), roles, bc.rule_table())
    assert "transfer" in guards["MIN_TRANSFER_MINUTES"]
    assert "budget" in guards["BUDGET_TOLERANCE_CNY"]
    assert "budget-floor" in guards["MIN_TRANSFER_COST_CNY"]
    assert "hours-floor" in guards["MIN_FEASIBLE_HOURS"]
    assert "shoot-floor" in guards["MIN_SHOOT_BLOCK_FLOOR"]
    assert "shoot-floor" in guards["MIN_SHOOT_BLOCK_MINUTES"]
    # run_time_group does not patch a constant; it forges an overrun. The mapping
    # has to go through the rule code, or this constant would look unguarded.
    assert "time" in guards["MAX_OVERRUN_MINUTES"]


def test_mutants_are_attributed_to_the_constant_they_patch():
    mutants = bc.mutant_guards()
    assert {"planner/no-transfer", "validator/no-transfer-rule"} <= set(
        mutants["MIN_TRANSFER_MINUTES"]
    )
    assert "validator/no-time-limit" in mutants["MAX_OVERRUN_MINUTES"]


def test_rule_table_reads_the_fstrings_not_a_second_list():
    rules = bc.rule_table()
    assert ("NO_ALTERNATIVE", "soft") in rules["MIN_ALTERNATIVES"]
    assert ("SHOOT_TOO_SHORT", "soft") in rules["MIN_SHOOT_BLOCK_FLOOR"]


def test_report_is_portable_and_names_every_constant():
    text = bc.render()
    assert "src\\" not in text, "Windows separators would make the generated page differ on CI"
    for row in bc.thresholds.collect():
        assert f"`{row['name']} = " in text


def test_committed_report_is_current():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_constant_checklist.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
