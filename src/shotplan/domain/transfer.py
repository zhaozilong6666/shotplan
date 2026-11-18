"""How long it takes to move between two places, from one number per place.

The knowledge base records, for each place, the minutes it takes to reach from the
city centre. That is a radius, not a position: two places at radii `a` and `b`,
separated by an unknown angle, sit somewhere between `|a-b|` and `a+b` apart. The
scheduler currently ignores this and uses a constant 20.

Four estimators, optimistic to pessimistic:

- `lower`: `|a-b|`, the tightest consistent bound. Exact only if both places lie
  on the same ray out of the centre.
- `rms`: `sqrt(a^2 + b^2)`. If the angle is uniform over the circle, `E[cos] = 0`,
  so `E[d^2] = a^2 + b^2` and the root-mean-square distance is exactly this. It is
  the natural "typical" value rather than a bound.
- `mean`: `(a+b)/2`. An arithmetic compromise; always below `rms` for `a != b`.
- `sum`: `a+b`, the loosest consistent bound, exact only on opposite rays.

`fixed` keeps today's behaviour and is the default everywhere, so importing this
module changes nothing. Which strategy is right is a question for a measurement,
not for this docstring.
"""

from __future__ import annotations

import math

from .constraints import MIN_TRANSFER_MINUTES

STRATEGIES = ("fixed", "lower", "rms", "mean", "sum")


def estimate(a: int, b: int, strategy: str = "fixed") -> float:
    """Minutes to move between a place at radius `a` and one at radius `b`."""
    if strategy == "fixed":
        # Reference the one constant, never a copy: a mutation that zeroes
        # MIN_TRANSFER_MINUTES has to reach this branch too, or the gate stops
        # noticing that transfers disappeared.
        return MIN_TRANSFER_MINUTES
    if strategy == "lower":
        return float(abs(a - b))
    if strategy == "rms":
        # Uniform angle gives E[d^2] = a^2 + b^2. Not a bound: a typical value.
        return math.sqrt(a * a + b * b)
    if strategy == "mean":
        return (a + b) / 2
    if strategy == "sum":
        return float(a + b)
    raise ValueError(f"unknown transfer strategy: {strategy}")
