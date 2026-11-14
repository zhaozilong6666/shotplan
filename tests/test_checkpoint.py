"""Resuming must be safe: same model, same prompt, no duplicated calls."""

from __future__ import annotations

import pytest
from evals.real_model_lab import append_checkpoint, load_checkpoint
from evals.scorers import CaseOutcome

META = {"model": "fake-model", "prompt_version": "planner-v2", "repeat": 3}


def _outcome(case_id: str, score: float = 0.9) -> CaseOutcome:
    return CaseOutcome(
        case_id=case_id,
        group="normal",
        description="",
        provider="fake",
        hard_pass=True,
        score=score,
        duration_ms=10.0,
    )


def test_round_trip_keeps_every_finished_call(tmp_path):
    path = tmp_path / "cp.jsonl"
    append_checkpoint(path, META, "C01", 0, _outcome("C01", 0.91))
    append_checkpoint(path, META, "C02", 1, _outcome("C02", 0.77))

    done = load_checkpoint(path, META)
    assert set(done) == {("C01", 0), ("C02", 1)}
    assert done[("C02", 1)].score == pytest.approx(0.77)


def test_missing_checkpoint_is_an_empty_start(tmp_path):
    assert load_checkpoint(tmp_path / "nope.jsonl", META) == {}


def test_a_different_run_is_refused(tmp_path):
    path = tmp_path / "cp.jsonl"
    append_checkpoint(path, META, "C01", 0, _outcome("C01"))
    with pytest.raises(ValueError, match="refusing to mix"):
        load_checkpoint(path, {**META, "prompt_version": "planner-v3"})


def test_empty_file_is_not_a_crash(tmp_path):
    path = tmp_path / "cp.jsonl"
    path.write_text("", encoding="utf-8")
    assert load_checkpoint(path, META) == {}
