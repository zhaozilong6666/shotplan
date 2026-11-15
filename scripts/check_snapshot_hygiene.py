"""Prevent endpoint URLs and credentials from re-entering committed snapshots.

Provider error messages used to embed the full request URL and upstream body.
They land in `error_messages`, in `ValidationIssue.message`, in `runs.payload`
and finally in generated reports. P2 redaction fixed new runs; this gate makes
sure the fix stays fixed.

Two historical snapshots are explicit legacy exceptions. Rewriting them would
falsify the raw evidence they were collected to preserve (and drift their
committed reports). They are listed here rather than silently ignored.

    python scripts/check_snapshot_hygiene.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "evals"

# Raw evidence from before redaction shipped. Do not add new entries here.
LEGACY = {
    "real-model-repeat3.json",
    "real-model-replication.json",
}

FULL_URL = re.compile(r"https?://[^\s\"'\\]*/[^\s\"'\\]*")
SECRET = re.compile(r"sk-[A-Za-z0-9]{12,}|Bearer\s+[A-Za-z0-9._-]{12,}", re.IGNORECASE)


def _messages(data: dict) -> list[str]:
    found = [str(key) for key in (data.get("error_messages") or {})]
    for case in data.get("per_case", []) or []:
        if case.get("error"):
            found.append(str(case["error"]))
    for outcome in data.get("outcomes", []) or []:
        if isinstance(outcome, dict) and outcome.get("error"):
            found.append(str(outcome["error"]))
    return found


def audit() -> tuple[list[str], int, int]:
    problems: list[str] = []
    scanned = 0
    legacy_hits = 0
    for path in sorted(SNAPSHOTS.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "hard_pass_rate" not in data:
            continue
        scanned += 1
        for message in _messages(data):
            url = FULL_URL.search(message)
            secret = SECRET.search(message)
            if not (url or secret):
                continue
            if path.name in LEGACY:
                legacy_hits += 1
                continue
            kind = "credential" if secret else "full endpoint URL"
            problems.append(f"{path.name}: {kind} in error message -> {message[:100]!r}")
    return problems, scanned, legacy_hits


def main() -> int:
    problems, scanned, legacy_hits = audit()
    print(f"snapshot hygiene: {scanned} snapshots, {legacy_hits} legacy exception(s), "
          f"{len(problems)} new violation(s)")
    for name in sorted(LEGACY):
        print(f"  legacy (documented, not rewritten): {name}")
    for problem in problems:
        print(f"  FAIL {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
