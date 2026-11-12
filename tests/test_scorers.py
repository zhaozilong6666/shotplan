from evals.run_eval import load_cases
from evals.scorers import CaseOutcome, aggregate, regression_check


def _outcome(case_id, score, hard, passed, group="normal"):
    return CaseOutcome(
        case_id=case_id, group=group, description="", provider="mock-good",
        hard_pass=hard, score=score, scores={"constraints": score},
        passed=passed,
    )


def test_aggregate_computes_expected_values():
    outcomes = [_outcome("A", 0.9, True, True), _outcome("B", 0.7, False, False, "edge")]
    metrics = aggregate(outcomes)
    assert metrics["case_count"] == 2
    assert metrics["avg_score"] == 0.8
    assert metrics["hard_pass_rate"] == 0.5
    assert metrics["cases_passed"] == 1
    assert metrics["cases_failed"] == 1
    assert set(metrics["by_group"]) == {"normal", "edge"}


def test_aggregate_handles_empty_suite():
    assert aggregate([]) == {"case_count": 0}


def test_regression_check_catches_low_score():
    metrics = aggregate([_outcome("A", 0.5, True, True)])
    problems = regression_check(metrics, min_score=0.8)
    assert any("avg_score" in p for p in problems)


def test_regression_check_catches_failed_cases():
    metrics = aggregate([_outcome("A", 0.95, True, False)])
    assert any("case(s) failed" in p for p in regression_check(metrics, 0.8))


def test_regression_check_passes_clean_suite():
    metrics = aggregate([_outcome("A", 0.95, True, True)])
    assert regression_check(metrics, 0.8) == []


def test_eval_cases_are_well_formed_and_unique():
    cases = load_cases()
    assert len(cases) >= 10
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert "request" in case and "expect_hard_pass" in case
        assert "expect_min_score" in case
        assert case["group"] in {"normal", "conflict", "edge"}
