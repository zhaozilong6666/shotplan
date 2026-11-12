"""Tests for the threads-vs-processes lab.

Absolute throughput is machine-dependent; these pin the logic that makes the
comparison meaningful: balanced chunks, real multi-process execution, and a
gate that never turns a machine-dependent speed ranking into a red build.
"""

from __future__ import annotations

from evals.scaling import (
    Arm,
    check,
    run_processes,
    run_threads,
    speed_note,
    split,
)


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


def test_process_arm_really_uses_multiple_pids():
    arm = run_processes(2, 8)
    assert arm.errors == 0
    assert arm.wall_s > 0
    assert len(arm.pids) >= 2


def test_gate_flags_failed_runs():
    arms = [Arm("threads", 1, 10, wall_s=1.0),
            Arm("processes", 2, 10, wall_s=0.5, errors=1, pids=(1, 2))]
    problems = check(arms)
    assert any("failed run" in problem for problem in problems)


def test_gate_does_not_assert_a_speed_ranking():
    # Processes are 10x slower here. That is a finding, not a CI failure.
    arms = [Arm("threads", 1, 100, wall_s=0.1),
            Arm("processes", 2, 100, wall_s=1.0, pids=(1, 2))]
    assert check(arms) == []
    note = speed_note(arms)
    assert note is not None
    assert "slower" in note and "not a gate" in note


def test_gate_flags_fake_parallelism():
    arms = [Arm("threads", 2, 100, wall_s=0.1),
            Arm("processes", 2, 100, wall_s=0.5, pids=(1234,))]
    problems = check(arms)
    assert any("parallelism was not actually exercised" in p for p in problems)


def test_gate_flags_missing_elapsed_time():
    arms = [Arm("threads", 1, 100, wall_s=0.0)]
    assert any("no elapsed time" in p for p in check(arms))


def test_single_worker_process_arm_needs_no_pid_proof():
    arms = [Arm("threads", 1, 100, wall_s=0.1),
            Arm("processes", 1, 100, wall_s=1.0)]
    assert check(arms) == []
