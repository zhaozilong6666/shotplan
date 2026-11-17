"""The audit is itself a measurement, so the measurement needs checks.

Two failure modes matter more than the report's content: a detector that cries
wolf (and teaches the reader to skip a section), and a report that silently
describes yesterday's corpus. Both are tested directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_reports as ar  # noqa: E402


def test_the_two_generator_signatures_are_both_recognised():
    assert (
        ar.generator_of("命令：`python -m evals.real_model_lab --write` ｜ 数据源：x")
        == "evals.real_model_lab"
    )
    assert ar.generator_of("由 `scripts/audit_reports.py` 生成，勿手改。") == (
        "scripts/audit_reports.py"
    )


def test_an_authored_document_has_no_generator():
    assert ar.generator_of("# 报告治理审计\n\n这是人写的一段分析。") == ""


def test_bare_filenames_are_not_treated_as_broken_references():
    """`rooms.json` is a planned file; flagging it makes the section noise."""
    assert not ar.worth_checking("rooms.json")
    assert not ar.worth_checking("0007-preflight-feasibility.md")
    assert not ar.worth_checking("v1..v6-naive.json")


def test_generated_output_is_exempt():
    """`evals/out/` is cleaned between runs by design."""
    assert not ar.worth_checking("evals/out/prompt-ab.md")
    assert ar.worth_checking("docs/ROADMAP.md")
    assert ar.worth_checking("scripts/audit_reports.py")


def test_chinese_is_tokenised_as_bigrams():
    assert ar.tokens("拍摄方案") == ["拍摄", "摄方", "方案"]


def test_code_fences_are_not_part_of_the_argument():
    """Two documents sharing a snippet are not repeating each other."""
    with_code = ar.tokens("结论如下\n```\nprint('same snippet')\n```\n")
    without = ar.tokens("结论如下\n")
    assert with_code == without


def _render_current() -> str:
    text_by_name, external = ar.collect()
    tokens_by_name = {name: ar.tokens(body) for name, body in text_by_name.items()}
    return ar.render(tokens_by_name, text_by_name, external)


def test_the_report_on_disk_matches_the_corpus():
    """Same contract as `--check`: a stale audit is a wrong audit."""
    on_disk = ar.DEFAULT_REPORT.read_text(encoding="utf-8")
    assert on_disk.strip() == _render_current().strip()


def test_rendering_twice_gives_the_same_text():
    assert _render_current() == _render_current()


def test_the_audit_excludes_itself():
    """Otherwise every run reports a 100%-similar duplicate of the last one."""
    text_by_name, _ = ar.collect()
    assert ar.DEFAULT_REPORT.name not in text_by_name


def test_untracked_drafts_are_not_part_of_the_corpus():
    """An untracked file in docs/ made --check pass locally and fail on CI.

    The corpus being audited is the repository, not the working directory.
    """
    corpus = {"report-audit.md": "x", "__untracked_draft__.md": "y"}
    assert list(ar.tracked_only(corpus)) == ["report-audit.md"]
