"""The annotation kit must be correct before any human time is spent on it."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from evals.annotation_kit import (
    N_ITEMS,
    cohens_kappa,
    quadratic_weighted_kappa,
    score_forms,
    self_test,
)

ROOT = Path(__file__).resolve().parents[1]


def test_self_test_assertions_hold():
    values = self_test()
    assert values["perfect"] == 1.0
    assert values["discordant"] == -1.0
    assert values["weighted_perfect"] == 1.0
    assert values["weighted_near"] > values["weighted_wild"]


def test_calculators_reject_malformed_input():
    with pytest.raises(ValueError):
        cohens_kappa([1, 2], [1])
    with pytest.raises(ValueError):
        quadratic_weighted_kappa([], [])


def test_blind_forms_exist_and_hide_the_validator_verdict():
    items = (ROOT / "evals/annotation/items.jsonl").read_text(encoding="utf-8")
    assert items.count('"item_id"') == N_ITEMS
    for rater in ("A", "B"):
        text = (ROOT / f"evals/annotation/rater_{rater}.csv").read_text(encoding="utf-8")
        header = text.splitlines()[0]
        assert "hard_pass" not in header and "score" not in header
        assert len(text.strip().splitlines()) == N_ITEMS + 1


def test_score_forms_reports_incomplete_and_disagreements(tmp_path):
    def write(name: str, rows: list[list[str]]) -> Path:
        path = tmp_path / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["item_id", "executable", "time_ok", "budget_ok", "usable"])
            writer.writerows(rows)
        return path

    a = write("a.csv", [["A01", "5", "是", "是", "是"], ["A02", "1", "否", "否", "否"]])
    b = write("b.csv", [["A01", "4", "是", "是", "是"], ["A02", "1", "否", "否", "是"]])
    result = score_forms(a, b)
    assert result["n_items"] == 2
    assert result["questions"]["executable"]["quadratic_weighted_kappa"] > 0
    assert [d["item_id"] for d in result["disagreements"]] == ["A01", "A02"]

    # Completeness is per question: a blank executable rating must not
    # silently discard the other three questions that were filled in.
    blank = write("blank.csv", [["A01", "", "是", "是", "是"], ["A02", "1", "否", "否", "否"]])
    incomplete = score_forms(a, blank)["questions"]
    assert incomplete["executable"]["complete"] is False
    assert incomplete["usable"]["complete"] is True
