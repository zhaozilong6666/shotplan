"""Shape checks for the sealed P1 set; do not assert its score here."""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "evals" / "multi_turn" / "p1_sealed.jsonl"


def test_sealed_set_has_30_cases_and_six_guards():
    rows = [json.loads(line) for line in PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 30
    assert sum(row.get("kind") == "guard" for row in rows) == 6
    assert len({row["id"] for row in rows}) == 30
    for row in rows:
        assert row.get("turns") and "expect" in row
