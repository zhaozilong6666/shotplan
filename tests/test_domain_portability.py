"""Keep the portability claim honest as rules are edited."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

import domain_portability as dp  # noqa: E402


def test_every_rule_is_classified():
    """A new rule must be judged, not silently assumed portable."""
    codes = {r["code"] for r in dp.RULES}
    judged = set(dp.LOGIC_PORTABLE) | set(dp.DOMAIN_LOGIC)
    assert codes - judged == set(), f"unclassified rules: {sorted(codes - judged)}"


def test_the_portability_claim_still_holds():
    """docs/generality.md promises >= 90% of rule logic is reusable."""
    portable = sum(
        1 for rule in dp.RULES
        if rule["code"] not in dp.DOMAIN_LOGIC
    )
    assert portable / len(dp.RULES) >= 0.9


def test_only_style_matching_is_declared_domain_specific():
    assert set(dp.DOMAIN_LOGIC) == {"STYLE_MISMATCH"}
