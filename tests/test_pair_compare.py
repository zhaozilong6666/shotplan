"""Paired comparison behaviour, including the case that must *not* pass."""

from __future__ import annotations

import json
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
        timeout=120,
    )
    assert proc.returncode == 2
