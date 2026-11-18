"""The four estimators, and the one property that matters: they stay in the bracket."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shotplan.agents.mock import MockProvider  # noqa: E402
from shotplan.domain.constraints import MIN_TRANSFER_MINUTES  # noqa: E402
from shotplan.domain.transfer import STRATEGIES, estimate  # noqa: E402


def test_every_strategy_sits_between_the_bounds():
    a, b = 20, 30
    lower = estimate(a, b, "lower")
    upper = estimate(a, b, "sum")
    assert lower == 10
    assert upper == 50
    for strategy in STRATEGIES:
        value = estimate(a, b, strategy)
        assert lower <= value <= upper, f"{strategy} escaped the bracket"


def test_rms_is_the_root_mean_square_not_the_average():
    # sqrt(20^2 + 30^2) = 36.06, above the arithmetic mean of 25.
    assert estimate(20, 30, "rms") == pytest.approx(36.0555, abs=1e-3)
    assert estimate(20, 30, "mean") == 25


def test_fixed_reads_the_constant_instead_of_a_copy():
    """A copy would survive a mutation that zeroes MIN_TRANSFER_MINUTES."""
    assert estimate(0, 0, "fixed") == MIN_TRANSFER_MINUTES


def test_an_unknown_strategy_is_refused_loudly():
    with pytest.raises(ValueError):
        estimate(10, 10, "euclidean")


def test_the_offline_planner_accepts_a_strategy():
    provider = MockProvider(mode="good", transfer_strategy="rms")
    assert provider.transfer_strategy == "rms"
    # Default stays unchanged, so nothing moves unless someone asks.
    assert MockProvider(mode="good").transfer_strategy == "fixed"
