"""Sample-size arithmetic for the evaluation suite.

The lab reports intervals; this module answers the question that follows:
"how many cases would I need before a 3-point difference means anything?"

Two designs, because they cost different amounts:

* **unpaired** — two independent runs (different models, or different prompts
  evaluated separately). Variance is 2σ².
* **paired** — the same cases under both conditions, so case difficulty cancels
  out. Variance is σ²_d, which for pass/fail data is much smaller than 2σ².
  Pairing is the cheapest way to buy statistical power, and the lab already
  supports it by construction (same case set).

Standard-library only: `statistics.NormalDist` gives the z quantiles.
"""

from __future__ import annotations

import argparse
import math
from statistics import NormalDist

Z_ALPHA = NormalDist().inv_cdf(0.975)  # two-sided alpha = 0.05
Z_POWER = NormalDist().inv_cdf(0.80)  # 80% power
_FACTOR = (Z_ALPHA + Z_POWER) ** 2  # ≈ 7.85


def required_cases(effect: float, sd: float, paired: bool = False) -> int:
    """Cases per arm needed to detect `effect` with alpha=0.05, power=0.8."""
    if effect <= 0:
        raise ValueError("effect must be positive")
    if sd <= 0:
        raise ValueError("sd must be positive")
    variance = sd**2 if paired else 2 * sd**2
    return math.ceil(_FACTOR * variance / effect**2)


def detectable_effect(n: int, sd: float, paired: bool = False) -> float:
    """Inverse: the smallest effect `n` cases can detect at the same settings."""
    if n <= 0:
        raise ValueError("n must be positive")
    variance = sd**2 if paired else 2 * sd**2
    return math.sqrt(_FACTOR * variance / n)


def binary_sd(pass_rate: float) -> float:
    """Standard deviation of a 0/1 outcome with the given mean."""
    return math.sqrt(max(pass_rate * (1 - pass_rate), 1e-9))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="sample-size calculator for the eval suite")
    parser.add_argument("--pass-rate", type=float, default=0.94, help="observed pass rate")
    parser.add_argument("--score-sd", type=float, default=0.08, help="std dev of case scores")
    parser.add_argument("--paired", action="store_true", help="assume the same cases under both arms")
    args = parser.parse_args(argv)

    sd_rate = binary_sd(args.pass_rate)
    print(f"pass rate p={args.pass_rate:.2f}  sd={sd_rate:.3f}  paired={args.paired}")
    print("\ncases per arm to detect a difference in pass rate:")
    for delta in (0.02, 0.03, 0.05, 0.10):
        print(f"  {delta * 100:4.0f} pp -> {required_cases(delta, sd_rate, args.paired):6d}")
    print("\ncases per arm to detect a difference in mean score:")
    for delta in (0.01, 0.02, 0.05):
        print(f"  {delta:.2f}    -> {required_cases(delta, args.score_sd, args.paired):6d}")
    print("\nwhat 69 cases can detect:")
    print(f"  pass rate: {detectable_effect(69, sd_rate, args.paired) * 100:.1f} pp")
    print(f"  score:     {detectable_effect(69, args.score_sd, args.paired):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
