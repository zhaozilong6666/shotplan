"""Pin the two defaults that are easy to confuse in docs and units."""

from pathlib import Path

from shotplan.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_code_default_is_sync():
    assert Settings().persist_mode == "sync"


def test_unit_is_batch_one_worker_and_never_suggests_workers_n():
    text = (ROOT / "deploy" / "shotplan.service").read_text(encoding="utf-8")
    assert "Environment=SHOTPLAN_WORKERS=1" in text
    assert "Environment=SHOTPLAN_PERSIST_MODE=batch" in text
    lowered = text.lower()
    assert "workers=4" not in lowered
    assert "shotplan_workers=4" not in lowered


def test_deploy_readme_distinguishes_code_and_unit_defaults():
    text = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    assert "代码/CLI 的默认仍是" in text
    assert "batch` + 1 worker" in text
    assert "独立端口" in text
