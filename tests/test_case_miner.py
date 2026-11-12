"""The holdout challenge set must stay deterministic, unseen and discriminating."""

from __future__ import annotations

import json

from evals import CASES_DIR, case_miner
from evals.run_eval import load_cases


def _write_artifacts(result: dict, directory) -> None:
    (directory / "cases.jsonl").write_text(
        case_miner.cases_text(result["cases"]), encoding="utf-8"
    )
    (directory / "snapshot.json").write_text(
        json.dumps(result["snapshot"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (directory / "report.md").write_text(
        case_miner.build_report(result), encoding="utf-8"
    )


def _patch_paths(monkeypatch, directory) -> None:
    monkeypatch.setattr(case_miner, "CHALLENGE_PATH", directory / "cases.jsonl")
    monkeypatch.setattr(case_miner, "SNAPSHOT_PATH", directory / "snapshot.json")
    monkeypatch.setattr(case_miner, "REPORT_PATH", directory / "report.md")


def test_candidate_grid_is_deterministic_and_complete():
    first = case_miner.candidate_briefs()
    second = case_miner.candidate_briefs()
    assert first == second
    assert len(first) == 5 * 5 * len(case_miner.PRESSURES)
    assert len({case_miner.canonical(c["brief"]) for c in first}) == len(first)
    assert {c["season"] for c in first} == {name for name, _ in case_miner.SEASONS}
    assert {c["pressure"] for c in first} == set(case_miner.PRESSURES)
    assert {c["city"] for c in first} == set(
        case_miner.mine()["snapshot"]["grid"]["city_ids"]
    )


def test_canonical_ignores_key_order_case_and_integer_float():
    left = {
        "city": "xian", "date": "2026-10-04", "hours": 6,
        "budget_cny": 1500, "styles": ["tang"],
    }
    right = {
        "styles": ["TANG"], "budget_cny": 1500, "hours": 6.0,
        "date": "2026-10-04", "city": "XIAN",
    }
    assert case_miner.canonical(left) == case_miner.canonical(right)
    assert case_miner.canonical({**left, "budget_cny": 1501}) != case_miner.canonical(left)


def test_committed_cases_are_unseen_and_well_formed():
    suite = {case_miner.canonical(c["request"]) for c in load_cases(CASES_DIR)}
    cases = [
        json.loads(line)
        for line in case_miner.CHALLENGE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(cases) >= case_miner.MIN_ACCEPTED
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({case_miner.canonical(c["request"]) for c in cases}) == len(cases)
    assert not suite & {case_miner.canonical(c["request"]) for c in cases}
    for case in cases:
        assert case["group"] == "challenge"
        assert case["expect_hard_pass"] is True
        assert case["expect_min_score"] == case_miner.CHALLENGE_MIN_SCORE
        assert case["provenance"]["new_combination"] in (True, False)


def test_committed_artifacts_match_a_fresh_mine():
    result = case_miner.mine()
    assert case_miner.cases_text(result["cases"]) == (
        case_miner.CHALLENGE_PATH.read_text(encoding="utf-8")
    )
    assert json.loads(case_miner.SNAPSHOT_PATH.read_text(encoding="utf-8")) == (
        result["snapshot"]
    )
    assert case_miner.REPORT_PATH.read_text(encoding="utf-8") == (
        case_miner.build_report(result)
    )
    assert case_miner.check(result) == []


def test_negative_controls_and_coverage_invariants():
    result = case_miner.mine()
    snapshot = result["snapshot"]
    expected_negative = snapshot["grid"]["cities"] * snapshot["grid"]["styles"]
    assert snapshot["rejected"] == expected_negative
    assert all(item["pressure"] == "conflicting" for item in result["rejected"])
    assert snapshot["coverage_before"] < snapshot["coverage_total"]
    assert snapshot["coverage_after"] == snapshot["coverage_total"]
    assert snapshot["good"]["hard_pass_rate"] == 1.0
    assert snapshot["naive"]["hard_pass_rate"] == 0.0
    assert snapshot["min_gap"] >= snapshot["min_gap_required"]
    assert snapshot["unseen_briefs"] == snapshot["accepted"]


def test_check_detects_hand_edited_artifacts(tmp_path, monkeypatch):
    result = case_miner.mine()
    _patch_paths(monkeypatch, tmp_path)
    _write_artifacts(result, tmp_path)
    assert case_miner.check(result) == []

    path = tmp_path / "cases.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    problems = case_miner.check(result)
    assert any("cases artifact is stale" in problem for problem in problems)


def test_check_fails_when_negative_controls_disappear(tmp_path, monkeypatch):
    result = case_miner.mine()
    result["snapshot"]["rejected"] = 0
    _patch_paths(monkeypatch, tmp_path)
    _write_artifacts(result, tmp_path)
    problems = case_miner.check(result)
    assert any("negative controls disappeared" in p for p in problems)


def test_check_fails_when_challenge_set_shrinks(tmp_path, monkeypatch):
    result = case_miner.mine()
    result["cases"] = result["cases"][:5]
    result["snapshot"]["accepted"] = 5
    result["snapshot"]["unseen_briefs"] = 5
    _patch_paths(monkeypatch, tmp_path)
    _write_artifacts(result, tmp_path)
    problems = case_miner.check(result)
    assert any("accepted challenge cases" in p for p in problems)
