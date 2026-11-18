"""The knowledge-base check is itself a measurement, so it needs its own checks.

Three failure modes decide whether this gate survives contact with a real
repository: a parser that flags prose as code (the first version had an 86%
false-positive rate), a report that changes between runs (which makes `--check`
useless), and a decision index that accepts "revisit someday" as a trigger.
Each is tested directly rather than inferred from coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import kb  # noqa: E402


def test_citations_are_split_into_three_resolvable_kinds():
    body = (
        "见 `src/shotplan/timeline.py` 与 `align_light_window()`，结论来自 `E01`。"
        "配置项 `prompt_version` 不是引用。"
    )
    paths, calls, experiments = kb.parse_citations(body)
    assert paths == ("src/shotplan/timeline.py",)
    assert calls == ("align_light_window",)
    assert experiments == ("E01",)


def test_a_range_ellipsis_is_not_a_broken_path():
    # `v1..v6-naive.json` names six files; it was a false positive on run one.
    paths, _, _ = kb.parse_citations("`evals/baselines/v1..v6-naive.json`")
    assert paths == ()


def test_gitignored_build_output_is_not_a_citation():
    paths, _, _ = kb.parse_citations("`evals/out/prompt-ab.md`")
    assert paths == ()


def test_builtins_and_promql_are_not_repo_functions():
    _, calls, _ = kb.parse_citations("`sum()` `int()` `rate()` `histogram_quantile()` `cpu_count()`")
    assert calls == ()


def test_a_bare_word_is_not_a_citation():
    _, calls, experiments = kb.parse_citations("`provider` `infeasible` `hard_pass_rate`")
    assert calls == ()
    assert experiments == ()


def test_missing_targets_produce_one_issue_each(tmp_path, monkeypatch):
    doc = tmp_path / "docs" / "note.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("`src/shotplan/gone.py` `gone_function()` `E99`", encoding="utf-8")
    facts = [kb.Facts(path="docs/note.md", kind="guide", paths=("src/shotplan/gone.py",),
                      calls=("gone_function",), experiments=("E99",))]
    issues = kb.cite_issues(facts, names={"exists"}, experiment_ids={"E01"})
    assert {issue.kind for issue in issues} == {"path", "call", "experiment"}
    assert len(issues) == 3


def test_an_experiment_naming_a_document_counts_as_evidence():
    """The relation runs both ways: `E01 -> docs/x.md` supports that document.

    Scoring only "document cites experiment id" made five measured reports look
    unsupported and put them in a queue telling the reader to go write evidence
    that already existed.
    """
    doc = kb.Facts(path="docs/a.md", kind="guide")
    verdicts = {"E01": "confirmed"}
    assert kb.evidence_grade(doc, verdicts) == "narrative"
    enriched = kb.enrich([doc], [], [{"id": "E01", "verdict": "confirmed", "evidence": "docs/a.md"}])
    assert enriched[0].cited_by == ("E01",)
    assert kb.evidence_grade(enriched[0], verdicts) == "empirical"


def test_a_decision_index_supplies_evidence_an_adr_omits():
    adr = kb.Facts(path="docs/adr/0002-x.md", kind="adr")
    decisions = [{"id": "D2", "adr": "docs/adr/0002-x.md", "evidence": ["E01"]}]
    enriched = kb.enrich([adr], decisions, [{"id": "E01", "verdict": "confirmed"}])
    assert enriched[0].experiments == ("E01",)


def test_evidence_grade_prefers_a_decided_experiment():
    decided = kb.Facts(path="docs/a.md", kind="guide", experiments=("E01",))
    referential = kb.Facts(path="docs/b.md", kind="guide", paths=("src/shotplan/timeline.py",))
    narrative = kb.Facts(path="docs/c.md", kind="guide")
    verdicts = {"E01": "confirmed", "E02": "open"}
    assert kb.evidence_grade(decided, verdicts) == "empirical"
    assert kb.evidence_grade(referential, verdicts) == "referential"
    assert kb.evidence_grade(narrative, verdicts) == "narrative"
    # An open experiment is not evidence yet.
    assert kb.evidence_grade(kb.Facts(path="docs/d.md", kind="guide", experiments=("E02",)), verdicts) == "narrative"


def test_measurement_anchors_are_eval_scripts_and_snapshots():
    doc = kb.Facts(
        path="docs/a.md",
        kind="guide",
        paths=("evals/benchmark.json", "evals/benchmark.py", "docs/x.md", "evals/challenge/cases.jsonl"),
    )
    assert kb.measurement_anchors(doc) == ("evals/benchmark.json", "evals/benchmark.py")


def test_a_decision_without_a_trigger_is_rejected():
    problems = kb.decision_problems(
        [{"id": "D9", "adr": "docs/adr/0001-core-zero-dependency.md", "decision": "x",
          "breaks_if": [], "status": "active", "evidence": []}],
        adrs={"docs/adr/0001-core-zero-dependency.md"},
        experiment_ids={"E01"},
    )
    assert any("breaks_if" in problem for problem in problems)


def test_a_decision_citing_an_unknown_experiment_is_rejected():
    problems = kb.decision_problems(
        [{"id": "D9", "adr": "docs/adr/0001-core-zero-dependency.md", "decision": "x",
          "breaks_if": ["某个可观测事件发生了"], "status": "active", "evidence": ["E77"]}],
        adrs={"docs/adr/0001-core-zero-dependency.md"},
        experiment_ids={"E01"},
    )
    assert any("E77" in problem for problem in problems)


def test_a_vague_trigger_is_reported_but_not_blocking():
    rows = [{"id": "D9", "breaks_if": ["以后再说", "数据量涨到 500 条且召回率低于 80%"]}]
    found = kb.vague_triggers(rows)
    assert len(found) == 1
    assert "以后再说" in found[0]


def test_a_waiver_that_no_longer_matches_anything_is_reported():
    issues = [kb.Issue("docs/a.md", "src/x.py", "path", "missing")]
    allowed = {"docs/a.md::src/x.py": "actual debt", "docs/b.md::gone()": "stale"}
    assert kb.unused_waivers(allowed, issues) == ["docs/b.md::gone()"]


def test_a_decision_leaning_on_a_refuted_experiment_is_flagged():
    decisions = [
        {"id": "D1", "adr": "docs/adr/0001-x.md", "evidence": ["E03", "E01"]},
        {"id": "D2", "adr": "docs/adr/0002-y.md", "evidence": ["E01"]},
    ]
    verdicts = {"E03": "refuted", "E01": "confirmed"}
    assert kb.refuted_dependencies(decisions, verdicts) == [("D1", "docs/adr/0001-x.md", "E03")]


def test_an_experiment_cited_as_a_case_is_not_evidence():
    """D08 points at E03 because it lost -- that is the illustration, not support.

    One field for both relations made the refutation check flag its own best
    example, which is a modelling bug rather than a finding.
    """
    decisions = [{"id": "D8", "adr": "docs/adr/0004-x.md", "evidence": [], "examples": ["E03"]}]
    assert kb.refuted_dependencies(decisions, {"E03": "refuted"}) == []


def test_examples_must_also_exist_in_the_ledger():
    problems = kb.decision_problems(
        [{"id": "D9", "adr": "docs/adr/0001-core-zero-dependency.md", "decision": "x",
          "breaks_if": ["某个可观测事件发生了"], "status": "active",
          "evidence": [], "examples": ["E77"]}],
        adrs={"docs/adr/0001-core-zero-dependency.md"},
        experiment_ids={"E01"},
    )
    assert any("examples" in problem and "E77" in problem for problem in problems)


def test_generated_pages_are_recognised_by_their_signature():
    # The detector is shared with the report audit on purpose; two copies drift.
    fuzz_header = "> 由 `python -m evals.fuzz --write-report` 生成，请勿手工编辑。"
    assert kb.classify(Path("docs/x.md"), fuzz_header) == "generated"
    assert kb.classify(Path("docs/adr/0001-x.md"), "# ADR 0001") == "adr"
    assert kb.classify(Path("docs/real-model-full-2026-11-16.md"), "# 报告") == "snapshot"
    assert kb.classify(Path("docs/cost.md"), "# 成本") == "guide"


def test_rendering_is_independent_of_input_order():
    """Same data in a different order must produce the same page.

    A Windows/Linux sort difference once made this repository's audit page
    disagree with itself across CI; the lesson is applied here up front.
    """
    facts = [
        kb.Facts(path="docs/b.md", kind="guide", paths=("src/shotplan/timeline.py",)),
        kb.Facts(path="docs/a.md", kind="guide"),
    ]
    args = dict(
        issues=[], allowed={}, verdicts={"E01": "confirmed"},
        ledger=[{"id": "E01", "question": "q", "verdict": "confirmed", "expect": "e"}],
        decisions=[], decision_issues=[],
        # A made-up code on purpose: `build_rule_map.py` treats "a test mentions
        # a rule code" as coverage, and a real code here would make the rule map
        # claim this rendering test covers that rule.
        mentions={"ANY_RULE_CODE": ["docs/a.md"]},
        adr_total=1, gate_count=3,
    )
    first = kb.render_health(facts, **args)
    second = kb.render_health(list(reversed(facts)), **args)
    assert first == second


def test_the_repository_passes_its_own_gate():
    """End-to-end: the committed pages match the committed data."""
    assert kb.main([]) == 0
