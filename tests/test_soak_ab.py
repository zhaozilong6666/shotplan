"""The A/B report's judgement must follow the data, not the narrative."""

from __future__ import annotations

from evals.soak_ab import _aggregate, verdict


def _arm(
    workers: int,
    *,
    round_no: int = 1,
    errors: int = 0,
    slow_5s: int = 0,
    rps: float = 10.0,
) -> dict:
    return {
        "round": round_no,
        "workers": workers,
        "requests": 100,
        "ok": 100 - errors,
        "errors": errors,
        "lost": 0,
        "rps": rps,
        "p95_ms": 50.0,
        "p999_ms": 100.0,
        "max_ms": 120.0,
        "slow_1s": slow_5s,
        "slow_5s": slow_5s,
    }


def test_aggregate_sums_the_tail_not_just_the_mean():
    arms = [
        _arm(1, rps=9.0),
        _arm(1, round_no=2, errors=1, slow_5s=2, rps=11.0),
        _arm(4, errors=3, slow_5s=1, rps=12.0),
    ]
    one = _aggregate(arms, 1)
    assert one["rounds"] == 2
    assert one["requests"] == 200
    assert one["errors"] == 1
    assert one["slow_5s"] == 2
    assert one["rps_median"] == 10.0
    assert one["max_worst"] == 120.0


def test_verdict_supports_the_hypothesis_only_when_4w_stalls_every_round():
    arms = [
        _arm(1),
        _arm(1, round_no=2),
        _arm(4, errors=2, slow_5s=1),
        _arm(4, round_no=2, slow_5s=1),
    ]
    assert "支持" in verdict(arms, [1, 4])


def test_verdict_calls_a_one_round_stall_partial_not_causal():
    arms = [_arm(1), _arm(1, round_no=2), _arm(4, errors=2), _arm(4, round_no=2)]
    text = verdict(arms, [1, 4])
    assert "部分复现" in text
    assert "不足以把 worker 数定为原因" in text


def test_verdict_refuses_to_blame_workers_when_nothing_stalled():
    arms = [_arm(1), _arm(1, round_no=2), _arm(4), _arm(4, round_no=2)]
    assert "未复现" in verdict(arms, [1, 4])


def test_verdict_reports_the_opposite_direction_instead_of_hiding_it():
    arms = [_arm(1, errors=2), _arm(1, round_no=2), _arm(4), _arm(4, round_no=2)]
    assert "不一致" in verdict(arms, [1, 4])
