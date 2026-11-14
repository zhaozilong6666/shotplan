"""Sub-sampling must be a smaller version of the same experiment.

The first-N rule looked harmless and was not: the case file is ordered by
group, so a 30-case run contained zero `normal` briefs and quietly measured a
different population.
"""

from __future__ import annotations

from collections import Counter

from evals.real_model_lab import stratified_sample
from evals.run_eval import load_cases


def test_stratified_sample_keeps_every_group():
    cases = load_cases()
    picked = stratified_sample(cases, 30)
    groups = Counter(case["group"] for case in picked)
    assert len(picked) == 30
    assert set(groups) == {case["group"] for case in cases}, "no group may vanish"


def test_stratified_sample_keeps_every_city():
    cases = load_cases()
    # 22 group×city strata: 30 is the smallest round budget that can cover all.
    picked = stratified_sample(cases, 30)
    cities = {case["request"]["city"] for case in picked}
    assert cities == {case["request"]["city"] for case in cases}


def test_budget_below_the_stratum_count_still_spreads_out():
    cases = load_cases()
    picked = stratified_sample(cases, 12)
    assert len(picked) == 12
    assert len({case["request"]["city"] for case in picked}) >= 4
    assert len({case["group"] for case in picked}) >= 3


def test_first_n_would_have_missed_a_whole_group():
    """Guard the regression: this is what the old behaviour produced."""
    cases = load_cases()
    head = {case["group"] for case in cases[:30]}
    assert "normal" not in head
    assert "normal" in {case["group"] for case in stratified_sample(cases, 30)}


def test_stratified_sample_is_deterministic_and_unique():
    cases = load_cases()
    first = [case["id"] for case in stratified_sample(cases, 25)]
    second = [case["id"] for case in stratified_sample(cases, 25)]
    assert first == second
    assert len(set(first)) == 25


def test_no_limit_returns_everything_untouched():
    cases = load_cases()
    assert [c["id"] for c in stratified_sample(cases, 0)] == [c["id"] for c in cases]
    assert [c["id"] for c in stratified_sample(cases, 999)] == [c["id"] for c in cases]
