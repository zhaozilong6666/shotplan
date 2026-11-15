"""The fault-injection matrix is itself a gate, and must stay honest."""

from __future__ import annotations

import re
from pathlib import Path

from evals.fault_injection import render, run_matrix


def test_every_scenario_passes():
    rows = run_matrix()
    assert len(rows) >= 12
    failures = [(r.name, r.detail) for r in rows if not r.ok]
    assert not failures, failures


def test_report_states_the_contracts_not_just_a_green_tick():
    text = render(run_matrix())
    assert "PERSISTENCE_FAILED" in text
    assert "hard_pass_rate" in text
    assert "502" in text and "503" in text
    assert "真实" in text and "离线" in text


def test_gate_count_matches_the_makefile():
    import re

    from scripts.check_gate_count import gate_commands

    commands = gate_commands()
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    declared = [int(v) for v in re.findall(r"(\d+)\s*道\s*CI\s*门禁", readme)]
    assert declared and all(v == len(commands) for v in declared), (len(commands), declared)


def test_http_smoke_report_is_deterministic_and_all_green():
    import os
    import tempfile
    from pathlib import Path

    from evals.http_load import server
    from evals.http_smoke import render, walk

    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("SHOTPLAN_")}
    try:
        os.environ["SHOTPLAN_SKIP_DOTENV"] = "1"
        with tempfile.TemporaryDirectory() as raw:
            with server(workers=1, db_path=Path(raw) / "smoke.db", persist=True) as (base, _):
                checks = walk(base)
    finally:
        for key in list(os.environ):
            if key.startswith("SHOTPLAN_"):
                os.environ.pop(key, None)
        os.environ.update(saved)

    assert len(checks) >= 10
    assert all(c.ok for c in checks), [(c.endpoint, c.detail) for c in checks if not c.ok]
    assert "10/10" in render(checks) or "PASS" in render(checks)


def test_readme_numeric_claims_all_match_their_sources():
    from pathlib import Path

    from scripts.check_doc_claims import actuals, claims

    values = actuals()
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    found = claims(readme)
    assert len(found) >= 12, "the gate must actually find the headline numbers"
    mismatches = [(name, declared, raw) for name, declared, raw in found
                  if declared != values[name]]
    assert not mismatches, (values, mismatches)
    # A test count changes every time a test is added: forbid it in prose.
    assert not re.search(r"\d+\s*个单元测试", readme)
