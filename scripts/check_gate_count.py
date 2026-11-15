"""Keep the documented CI gate count equal to the Makefile's actual gates.

README used to claim "16 gates" while the Makefile ran 19. A number nobody
checks is a number that drifts. This script counts real gate commands and
refuses to let a new gate land without updating the documentation.

    python scripts/check_gate_count.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"
README = ROOT / "README.md"


def gate_commands() -> list[str]:
    text = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(r"^gates:\s*\n((?:\t.*\n)+)", text, re.MULTILINE)
    if not match:
        raise SystemExit("cannot find the gates: target in the Makefile")
    commands = [
        line.strip()
        for line in match.group(1).splitlines()
        if line.strip().startswith("$(PYTHON)")
    ]
    return commands


def main() -> int:
    commands = gate_commands()
    count = len(commands)
    readme = README.read_text(encoding="utf-8")
    matches = re.findall(r"(\d+)\s*道\s*CI\s*门禁", readme)
    declared = [int(value) for value in matches]
    print(f"Makefile gates: {count}; README declares: {declared or 'nothing'}")
    if not declared or any(value != count for value in declared):
        print(
            f"gate count drift: every README mention must say '{count} 道 CI 门禁'",
            file=sys.stderr,
        )
        return 1
    print("gate-count gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
