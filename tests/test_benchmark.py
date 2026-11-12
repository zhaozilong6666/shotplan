"""Tests for the benchmark harness.

The numbers themselves are machine-dependent, so these assert the *shape*
(ordering, ranges) and the gate logic — not absolute performance.
"""

from __future__ import annotations

from evals.benchmark import check, measure, percentile, sweep


def test_percentile_handles_empty_and_single_values():
    assert percentile([], 95) == 0.0
    assert percentile([3.0], 50) == 3.0


def test_percentile_is_monotonic_and_bounded():
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0
    assert percentile(values, 99) == 99.0
    assert percentile(values, 100) == 100.0


def test_measure_reports_ordered_percentiles():
    metrics = measure(20).metrics()
    assert metrics["iterations"] == 20
    assert 0 < metrics["p50_ms"] <= metrics["p95_ms"] <= metrics["p99_ms"]
    assert metrics["throughput_rps"] > 0
    assert metrics["peak_kb"] > 0


def test_sweep_covers_every_eval_case():
    corpus = sweep()
    assert corpus["cases"] >= 60
    assert sum(corpus["status_counts"].values()) == corpus["cases"]
    assert corpus["p95_ms"] >= corpus["p50_ms"] >= 0


def test_gate_passes_on_real_numbers_and_fails_on_impossible_ones():
    metrics = measure(20).metrics()
    corpus = sweep()
    assert check(metrics, corpus, max_p95_ms=10_000, min_throughput=1, max_peak_mb=1024) == []
    problems = check(metrics, corpus, max_p95_ms=0.0001, min_throughput=10**9, max_peak_mb=0.0001)
    # p95, throughput, peak heap and the corpus p95 ceiling all trip.
    assert len(problems) == 4
