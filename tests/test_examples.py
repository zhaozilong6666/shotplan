"""Tests for the generated example gallery.

The gallery is committed and CI diffs it, so determinism is the contract: the
document must not contain ids or timings, and regenerating it twice must produce
identical bytes.
"""

from __future__ import annotations

from evals.examples import build_document, main


def test_document_is_deterministic():
    assert build_document() == build_document()


def test_document_has_no_nondeterministic_fields():
    document = build_document()
    for marker in ("plan_id", "request_id", "run_id", "created_at", "duration_ms"):
        assert marker not in document


def test_document_covers_all_four_situations():
    document = build_document()
    assert document.count("## ") == 4
    assert "status=infeasible" in document
    assert "BUDGET_BELOW_MINIMUM" in document
    assert "硬约束通过" in document


def test_check_gate_detects_stale_file(tmp_path):
    target = tmp_path / "examples.md"
    assert main(["--write", "--out", str(target)]) == 0
    assert main(["--check", "--out", str(target)]) == 0
    target.write_text("stale\n", encoding="utf-8")
    assert main(["--check", "--out", str(target)]) == 1


def test_check_gate_detects_missing_file(tmp_path):
    assert main(["--check", "--out", str(tmp_path / "missing.md")]) == 1
