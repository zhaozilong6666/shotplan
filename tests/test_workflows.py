"""Workflow YAML must parse before GitHub ever gets to run it.

An unquoted colon in a step name (``name: gates (source: Makefile)``) is valid
prose and invalid YAML. GitHub rejected the workflow at startup, so every run
reported failure with zero jobs while all local gates stayed green. This test
moves that failure into the test suite, where it is a one-second fix.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def test_every_workflow_parses_and_has_jobs():
    paths = sorted(WORKFLOWS.glob("*.yml"))
    assert paths, "no workflow files found"
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name}: expected a mapping"
        jobs = data.get("jobs")
        assert isinstance(jobs, dict) and jobs, f"{path.name}: no jobs"
        for name, job in jobs.items():
            assert isinstance(job, dict), f"{path.name}/{name}: job must be a mapping"
            assert "runs-on" in job or "uses" in job, f"{path.name}/{name}: no runner"
