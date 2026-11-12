"""Tests for the threads-vs-processes lab.

Absolute throughput is machine-dependent; these pin the logic that makes the
comparison meaningful: balanced chunks, and a gate that notices failures or a
process arm that is slower than threads.
"""

from __future__ import annotations

from evals.scaling import Arm, check, run_threads, split


def test_split_covers_every_request_without_empty_jobs():
    for total, parts in ((100, 4), (100, 3), (7, 8), (1, 4)):
        jobs = split(total, parts)
        assert sum(count for _, count in jobs) == total
        assert all(count > 0 for _, count in jobs)
        assert len(jobs) <= max(1, min(parts, total))


def test_split_is_balanced():
    jobs = split(103, 4)
    counts = [count for _, count in jobs]
    assert max(counts) - min(counts) <= 1


def test_threads_run_every_request():
    arm = run_threads(2, 8)
    assert arm.requests == 8
    assert arm.errors == 0
    assert arm.wall_s > 0


def test_gate_flags_failed_runs():
    arms = [Arm("threads", 1, 10, wall_s=1.0), Arm("processes", 2, 10, wall_s=0.5, errors=1)]
    problems = check(arms, cpu_count=4)
    assert any("failed run" in problem for problem in problems)


def test_gate_flags_processes_slower_than_threads():
    arms = [Arm("threads", 1, 100, wall_s=0.1), Arm("processes", 2, 100, wall_s=1.0)]
    problems = check(arms, cpu_count=4)
    assert any("slower than threads" in problem for problem in problems)


def test_gate_skips_speed_assertion_on_a_single_core():
    arms = [Arm("threads", 1, 100, wall_s=0.1), Arm("processes", 1, 100, wall_s=1.0)]
    assert check(arms, cpu_count=1) == []
