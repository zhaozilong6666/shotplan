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


def test_open_entries_are_allowed_to_have_no_evidence(tmp_path: Path):
    """An untested belief is legal; a confirmed one without evidence is not.

    This used to assert that the real ledger contained an open entry. That
    turned a data state into a requirement, and it failed the day the last
    open item (E08) was closed. The rule belongs on a fixture, where it tests
    the loader rather than today's ledger.
    """
    ledger = tmp_path / "open.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "id": "E99",
                "date": "2026-11-17",
                "area": "docs",
                "question": "q",
                "expect": "e",
                "verdict": "open",
                "evidence": None,
            }
        ),
        encoding="utf-8",
    )
    rows = el.load(ledger)
    assert rows[0]["verdict"] == "open"
    assert not rows[0].get("evidence")


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


def _ledger(tmp_path: Path, *rows: dict) -> Path:
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def _base_row(**overrides) -> dict:
    row = {
        "id": "E07",
        "date": "2026-11-17",
        "area": "docs",
        "question": "q",
        "expect": "e",
        "verdict": "confirmed",
        "evidence": None,
    }
    row.update(overrides)
    return row


def _args(ledger: Path, report: Path, *extra: str) -> list[str]:
    return ["--ledger", str(ledger), "--report-path", str(report), *extra]


def test_open_assigns_the_next_id_and_refreshes_the_page(tmp_path: Path):
    """Recording is a command, not an edit: the id, the date and the page follow."""
    ledger = _ledger(tmp_path, _base_row())
    report = tmp_path / "experiments.md"
    code = el.main(
        _args(
            ledger, report,
            "--open", "--area", "rules", "--question", "新规则会不会误伤",
            "--expect", "误伤 0 条", "--metric", "被拒绝的条数", "--date", "2026-11-18",
        )
    )
    assert code == 0
    rows = el.load(ledger)
    assert rows[-1]["id"] == "E08"
    assert rows[-1]["verdict"] == "open"
    assert rows[-1]["date"] == "2026-11-18"
    assert report.exists(), "the page must be refreshed by the same command"


def test_open_refuses_a_prediction_with_two_metrics(tmp_path: Path):
    """E08 stayed open for three days because its prediction said 「或」."""
    ledger = _ledger(tmp_path, _base_row())
    code = el.main(
        _args(
            ledger, tmp_path / "r.md",
            "--open", "--area", "data", "--question", "q",
            "--expect", "通过率或覆盖度提升", "--metric", "m",
        )
    )
    assert code == 2
    assert len(el.load(ledger)) == 1


def test_open_refuses_a_missing_metric(tmp_path: Path):
    ledger = _ledger(tmp_path, _base_row())
    code = el.main(
        _args(ledger, tmp_path / "r.md", "--open", "--area", "data",
              "--question", "q", "--expect", "e")
    )
    assert code == 2
    assert len(el.load(ledger)) == 1


def test_close_needs_an_evidence_file_that_exists(tmp_path: Path):
    ledger = _ledger(tmp_path, _base_row(id="E09", verdict="open"))
    report = tmp_path / "r.md"
    assert el.main(_args(ledger, report, "--close", "E09", "--verdict", "confirmed",
                         "--to", "0", "--evidence", "docs/nowhere.md")) == 2
    assert el.load(ledger)[0]["verdict"] == "open"

    evidence = tmp_path / "evidence.md"
    evidence.write_text("measured", encoding="utf-8")
    assert el.main(_args(ledger, report, "--close", "E09", "--verdict", "confirmed",
                         "--to", "0", "--evidence", str(evidence))) == 0
    row = el.load(ledger)[0]
    assert row["verdict"] == "confirmed"
    assert row["to"] == 0


def test_close_refuses_to_rejudge_a_decided_entry(tmp_path: Path):
    """Flipping a refuted result has to be a visible hand edit, not a flag."""
    ledger = _ledger(tmp_path, _base_row(id="E10", verdict="refuted"))
    code = el.main(_args(ledger, tmp_path / "r.md", "--close", "E10",
                         "--verdict", "confirmed", "--evidence", "docs/a.md"))
    assert code == 2
    assert el.load(ledger)[0]["verdict"] == "refuted"
