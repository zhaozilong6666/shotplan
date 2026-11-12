"""Tests for the fuzz harness itself.

A fuzzer that silently passes is worse than none, so these tests check the
harness detects violations: a crashing case must be recorded as a crash, a
valid case must not, and every case must be reproducible from its seed.
"""

from __future__ import annotations

from evals.fuzz import contract_problems, fuzz, mutate, run_case

from shotplan.domain.models import PlanRequest


def test_mutation_changes_the_brief():
    import random

    base = PlanRequest().to_dict()
    rng = random.Random(7)
    mutated = mutate(rng, base)
    assert mutated != base


def test_contract_problems_flags_a_crash():
    report = run_case({"hours": "not-a-number"}, case_seed=1)
    report.status = "raised"
    report.error = "boom"
    assert "raised an uncaught exception" in contract_problems(report)


def test_contract_problems_rejects_unknown_status():
    report = run_case({}, case_seed=2)
    report.status = "exploded"
    assert any("unknown status" in p for p in contract_problems(report))


def test_fuzz_run_has_no_crashes_and_is_deterministic():
    first, _ = fuzz(iterations=60, seed=4242)
    second, _ = fuzz(iterations=60, seed=4242)
    assert first.crashes == []
    assert first.unserializable == []
    assert first.by_status == second.by_status
    assert sum(first.by_status.values()) == 60


def test_fuzz_can_replay_a_single_seed():
    summary, _ = fuzz(iterations=40, seed=99)
    seed = summary.worst[0]["case_seed"]
    replayed, reports = fuzz(iterations=40, seed=99, only_seed=seed)
    assert len(reports) == 1
    assert reports[0].case_seed == seed
    assert replayed.crashes == []


def test_fuzz_covers_both_feasible_and_refused_briefs():
    summary, _ = fuzz(iterations=120, seed=2026)
    assert summary.by_status.get("ok", 0) > 0
    assert summary.by_status.get("infeasible", 0) > 0
