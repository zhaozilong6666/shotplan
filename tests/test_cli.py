"""CLI contract tests: exit codes, JSON output, persistence and delegation."""

from __future__ import annotations

import json

from shotplan import cli


def test_plan_json_prints_a_plan(capsys):
    code = cli.main(["plan", "--mock-mode", "good", "--json", "--hours", "6"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(data["plan"]["slots"]) >= 3
    assert data["validation"]["hard_pass"] is True


def test_plan_infeasible_returns_exit_code_three(capsys):
    code = cli.main(["plan", "--budget", "100"])
    out = capsys.readouterr().out
    assert code == 3
    assert "需求不可满足" in out
    assert "BUDGET_BELOW_MINIMUM" in out


def test_plan_can_write_json_to_file(tmp_path, capsys):
    target = tmp_path / "result.json"
    code = cli.main(["plan", "--mock-mode", "good", "--json", "--out", str(target)])
    assert code == 0
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "ok"


def test_rules_config_history_and_persistence(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "cli.db"))
    assert cli.main(["rules"]) == 0
    assert "TIME_OVERLAP" in capsys.readouterr().out

    assert cli.main(["config"]) == 0
    config = json.loads(capsys.readouterr().out)
    assert config["api_key_set"] is False

    assert cli.main(["plan", "--mock-mode", "good", "--persist"]) == 0
    capsys.readouterr()
    assert cli.main(["history", "--limit", "5"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1


def test_stats_aggregates_persisted_runs(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "stats.db"))
    assert cli.main(["plan", "--mock-mode", "good", "--persist"]) == 0
    assert cli.main(["plan", "--budget", "100", "--persist"]) == 3
    capsys.readouterr()
    assert cli.main(["stats", "--limit", "10"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_runs"] == 2
    assert data["by_status"]["infeasible"] == 1
    assert data["groups"]


def test_eval_command_delegates_to_the_harness(monkeypatch):
    import evals.run_eval as run_eval

    calls = []
    monkeypatch.setattr(run_eval, "main", lambda argv=None: calls.append(argv) or 0)
    assert cli.main(["eval", "--write-report"]) == 0
    assert calls == [["--write-report"]]


def test_serve_delegates_to_uvicorn(monkeypatch):
    import uvicorn

    called = {}
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: called.setdefault("ok", True))
    assert cli.main(["serve", "--port", "9999"]) == 0
    assert called["ok"] is True
