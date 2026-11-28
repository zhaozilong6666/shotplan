"""Paired comparison behaviour, including the case that must *not* pass."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from evals.pair_compare import build_report, paired_diff


def _snapshot(scores: dict[str, float], passes: dict[str, bool]) -> dict:
    return {
        "model": "fake",
        "per_case": [
            {"id": cid, "group": "normal", "hard_pass": passes[cid], "score": scores[cid]}
            for cid in scores
        ],
    }


def test_paired_diff_detects_a_consistent_improvement():
    ids = [f"C{i:02d}" for i in range(1, 21)]
    a = {cid: {"pass": 0.0, "score": 0.60} for cid in ids}
    b = {cid: {"pass": 1.0, "score": 0.90} for cid in ids}
    stats = paired_diff(a, b, "pass")
    assert stats["mean_diff"] == 1.0
    assert stats["ci_low"] > 0, "a uniform win must exclude zero"
    assert stats["wins"] == 20 and stats["losses"] == 0


def test_paired_diff_refuses_to_call_noise_a_win():
    # Same distribution, opposite directions: the CI must straddle zero.
    ids = [f"C{i:02d}" for i in range(1, 21)]
    a = {cid: {"pass": 0.5, "score": 0.80} for cid in ids}
    b = {
        cid: {"pass": 1.0 if i % 2 else 0.0, "score": 0.80}
        for i, cid in enumerate(ids)
    }
    stats = paired_diff(a, b, "pass")
    assert stats["ci_low"] <= 0 <= stats["ci_high"]


def test_report_separates_wins_from_conclusions():
    stats = {"n": 20, "mean_diff": 0.05, "ci_low": -0.02, "ci_high": 0.12, "wins": 9, "losses": 7}
    report = build_report("a.json", "b.json", stats, stats)
    assert "测不出差异（区间跨 0）" in report
    assert "不要写「B 略好」" in report


def test_cli_exit_code_reflects_evidence(tmp_path):
    ids = [f"C{i:02d}" for i in range(1, 11)]
    a = _snapshot({cid: 0.6 for cid in ids}, {cid: False for cid in ids})
    b = _snapshot({cid: 0.9 for cid in ids}, {cid: True for cid in ids})
    a_path, b_path = tmp_path / "a.json", tmp_path / "b.json"
    a_path.write_text(json.dumps(a), encoding="utf-8")
    b_path.write_text(json.dumps(b), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "evals.pair_compare", "--a", str(a_path), "--b", str(b_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
    )
    assert proc.returncode == 0
    assert "B 更好" in proc.stdout


def test_cli_rejects_mismatched_case_sets(tmp_path):
    a = _snapshot({"C01": 0.5}, {"C01": True})
    b = _snapshot({"C02": 0.5}, {"C02": True})
    a_path, b_path = tmp_path / "a.json", tmp_path / "b.json"
    a_path.write_text(json.dumps(a), encoding="utf-8")
    b_path.write_text(json.dumps(b), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "evals.pair_compare", "--a", str(a_path), "--b", str(b_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
    )
    assert proc.returncode == 2


def test_noninferiority_requires_ci_lower_bound_above_margin():
    from evals.pair_compare import noninferior

    ok, _ = noninferior(
        {"n": 20, "mean_diff": 0.0, "ci_low": -0.02, "ci_high": 0.02, "wins": 0, "losses": 0},
        3.0,
    )
    assert ok
    bad, _ = noninferior(
        {"n": 20, "mean_diff": -0.05, "ci_low": -0.09, "ci_high": -0.01, "wins": 0, "losses": 20},
        3.0,
    )
    assert not bad


def test_regression_cli_is_green_when_identical_and_red_when_worse(tmp_path):
    ids = [f"C{i:02d}" for i in range(1, 21)]
    a = _snapshot({cid: 0.8 for cid in ids}, {cid: True for cid in ids})
    b_same = _snapshot({cid: 0.8 for cid in ids}, {cid: True for cid in ids})
    b_worse = _snapshot({cid: 0.8 for cid in ids}, {cid: i > 10 for i, cid in enumerate(ids)})
    a_path, same_path, worse_path = tmp_path / "a.json", tmp_path / "same.json", tmp_path / "worse.json"
    a_path.write_text(json.dumps(a), encoding="utf-8")
    same_path.write_text(json.dumps(b_same), encoding="utf-8")
    worse_path.write_text(json.dumps(b_worse), encoding="utf-8")
    base = [sys.executable, "-m", "evals.pair_compare", "--a", str(a_path),
            "--regression-margin-pp", "3", "--b"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    same = subprocess.run(base + [str(same_path)], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=120)
    worse = subprocess.run(base + [str(worse_path)], capture_output=True, text=True,
                           encoding="utf-8", env=env, timeout=120)
    assert same.returncode == 0
    assert "通过" in same.stdout
    assert worse.returncode == 1


def test_report_says_no_difference_for_identical_pairs():
    stats = {"n": 10, "mean_diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "wins": 0, "losses": 0}
    report = build_report("a", "b", stats, stats)
    assert "无差异（配对完全一致）" in report
