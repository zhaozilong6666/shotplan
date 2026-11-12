import pytest

from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan
from shotplan.storage import RunStore


def test_store_roundtrip(tmp_path):
    store = RunStore(tmp_path / "nested" / "runs.db")
    result = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    run_id = store.save(result)

    assert store.get(run_id)["trace"]["run_id"] == run_id
    assert store.get("missing") is None
    rows = store.recent()
    assert rows[0]["run_id"] == run_id
    assert rows[0]["hard_pass"] == 1


def test_summary_aggregates(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    for mode in ("good", "naive"):
        store.save(run_plan(PlanRequest(), provider=MockProvider(mode), max_repairs=0))
    summary = store.summary()
    assert summary["total_runs"] == 2
    assert summary["hard_pass_rate"] == 0.5
    assert 0 < summary["avg_score"] < 1
    assert summary["avg_duration_ms"] > 0


def test_save_is_idempotent(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    result = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    store.save(result)
    store.save(result)
    assert store.summary()["total_runs"] == 1


def test_recent_respects_limit(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    for i in range(5):
        result = run_plan(PlanRequest(hours=4 + i),
                          provider=MockProvider("good"), max_repairs=0)
        store.save(result)
    assert len(store.recent(2)) == 2


def test_invalid_path_raises_storage_error(tmp_path):
    from shotplan.domain.errors import StorageError

    bad_file = tmp_path / "not_a_dir"
    bad_file.write_text("x", encoding="utf-8")
    with pytest.raises(StorageError):
        RunStore(bad_file / "child" / "runs.db")
