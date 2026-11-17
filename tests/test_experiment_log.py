"""The ledger is only worth reading if its rules are enforced.

The interesting failure is not a wrong verdict -- a human fills that in -- but a
conclusion whose evidence has been deleted, or a page that describes a ledger
someone has since edited. Both are checked here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

import experiment_log as el  # noqa: E402


def test_every_entry_declares_the_four_things_needed_to_judge_it():
    for row in el.load(el.DEFAULT_LEDGER):
        assert row["question"].strip()
        assert row["expect"].strip()
        assert row["area"].strip()


def test_evidence_must_resolve():
    """A conclusion nobody can click through to is a rumour."""
    for row in el.load(el.DEFAULT_LEDGER):
        if row.get("evidence"):
            assert (ROOT / row["evidence"]).exists(), row["id"]


def test_open_entries_are_allowed_to_have_no_evidence():
    """E08 records a belief that was never tested; that is the point of it."""
    rows = el.load(el.DEFAULT_LEDGER)
    open_rows = [row for row in rows if row["verdict"] == "open"]
    assert open_rows, "the ledger should carry at least one untested belief"
    for row in open_rows:
        assert not row.get("evidence")


def test_a_missing_evidence_file_is_rejected(tmp_path: Path):
    ledger = tmp_path / "bad.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "id": "E99",
                "date": "2026-11-17",
                "area": "docs",
                "question": "q",
                "expect": "e",
                "verdict": "confirmed",
                "evidence": "docs/does-not-exist.md",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="evidence not found"):
        el.load(ledger)


def test_an_unknown_verdict_is_rejected(tmp_path: Path):
    ledger = tmp_path / "bad.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "id": "E99",
                "date": "2026-11-17",
                "area": "docs",
                "question": "q",
                "expect": "e",
                "verdict": "probably-fine",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="verdict must be"):
        el.load(ledger)


def test_small_samples_get_no_ratio_verdict():
    """Sixteen entries is not a calibration curve; the wording must say so."""
    rows = [{"verdict": "confirmed", "question": "q", "expect": "e"} for _ in range(3)]
    lines = el.calibration(rows)
    assert any("样本太少" in line for line in lines)


def test_a_low_refutation_rate_is_called_out():
    rows = [{"verdict": "confirmed", "question": "q", "expect": "e"} for _ in range(5)]
    rows.append({"verdict": "refuted", "question": "q", "expect": "e"})
    lines = el.calibration(rows)
    assert any("偏低" in line for line in lines)


def test_the_page_matches_the_ledger():
    body = el.render(el.load(el.DEFAULT_LEDGER))
    on_disk = el.DEFAULT_REPORT.read_text(encoding="utf-8")
    assert on_disk.strip() == body.strip()


def test_rendering_twice_gives_the_same_text():
    rows = el.load(el.DEFAULT_LEDGER)
    assert el.render(rows) == el.render(rows)
