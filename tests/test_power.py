"""Sample-size arithmetic: cheap to get wrong, so pin the numbers."""

from __future__ import annotations

import math
import subprocess
import sys

from evals.power import binary_sd, detectable_effect, required_cases


def test_required_cases_scales_with_inverse_square_of_effect():
    sd = 0.2
    small = required_cases(0.1, sd)
    large = required_cases(0.2, sd)
    assert large < small
    # Halving the effect should roughly quadruple the requirement.
    assert 3.5 <= small / large <= 4.5


def test_pairing_cuts_the_requirement_in_half():
    sd = 0.2
    assert required_cases(0.05, sd, paired=True) * 2 == required_cases(0.05, sd)


def test_detectable_effect_is_the_inverse_of_required_cases():
    sd = 0.15
    n = 200
    effect = detectable_effect(n, sd)
    assert abs(required_cases(effect, sd) - n) <= 1
    assert math.isfinite(effect)


def test_binary_sd_is_maximal_at_a_coin_flip():
    assert binary_sd(0.5) > binary_sd(0.94)
    assert binary_sd(0.0) == binary_sd(1.0)  # clamped, never zero


def test_cli_runs_without_touching_the_network():
    proc = subprocess.run(
        [sys.executable, "-m", "evals.power", "--pass-rate", "0.94", "--paired"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "cases per arm" in proc.stdout
    assert "what 69 cases can detect" in proc.stdout
