"""The threshold page is only useful if it reads the right numbers from the right place."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_thresholds as bt  # noqa: E402


def test_the_tunable_numbers_are_collected():
    names = {row["name"] for row in bt.collect()}
    assert {"MIN_TRANSFER_MINUTES", "MIN_SHOOT_BLOCK_MINUTES", "STYLING_FLOOR_MINUTES"} <= names
    assert "RULES" not in names, "the rule list is a table, not a threshold"
    assert len(names) >= 10


def test_a_number_whose_comment_cites_a_case_is_marked_measured():
    rows = {row["name"]: row for row in bt.collect()}
    # STYLING_SHARE's comment names C46; MIN_TRANSFER_MINUTES' does not.
    assert rows["STYLING_SHARE"]["measured"] is True
    assert rows["MIN_TRANSFER_MINUTES"]["measured"] is False


def test_values_come_from_the_code_not_from_a_copy():
    rows = {row["name"]: row for row in bt.collect()}
    assert rows["MIN_TRANSFER_MINUTES"]["value"] == 20
    assert rows["MIN_FEASIBLE_HOURS"]["value"] == 1.5
