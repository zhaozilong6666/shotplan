"""The perturbation has to keep the type, or the scan measures a bug instead."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import evals.sensitivity as sens  # noqa: E402


def test_scaled_keeps_an_integer_integer():
    """20 * 0.5 is 10.0; a float minute broke every downstream comparison."""
    result = sens.scaled(20, 0.5)
    assert result == 10
    assert isinstance(result, int)


def test_scaled_keeps_a_fraction_fractional():
    assert sens.scaled(0.35, 1.5) == pytest.approx(0.525)


def test_scaled_rounds_instead_of_truncating():
    assert sens.scaled(15, 1.5) == 22
    assert sens.scaled(15, 0.5) == 8
