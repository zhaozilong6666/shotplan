"""Relative links in committed docs must resolve.

This repo leans on cross-referenced reports and ADRs. Moving a file without
updating every link is invisible in CI and only found by a reader, so it gets
the same treatment as any other drift.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\(([^)]+)\)")
SKIP_PREFIX = ("http://", "https://", "mailto:", "#")


def _markdown_files() -> list[Path]:
    return [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]


def test_relative_doc_links_resolve():
    broken: list[str] = []
    for path in _markdown_files():
        in_code = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            for raw in LINK.findall(line):
                target = raw.split()[0].strip("<>")
                if target.startswith(SKIP_PREFIX):
                    continue
                target = target.split("#", 1)[0]
                if target and not (path.parent / target).resolve().exists():
                    broken.append(f"{path.relative_to(ROOT)}:{lineno} -> {raw}")
    assert not broken, "broken relative links:\n" + "\n".join(broken)
