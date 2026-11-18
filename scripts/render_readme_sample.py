"""Keep the README's sample output honest by generating it instead of typing it.

The sample was hand-copied and had drifted: it showed 大雁塔 and 城墙 at 870 yuan
while the planner was producing 青龙寺 and 广仁寺 at 750. Nobody notices that, because
the block looks plausible -- which is exactly why a copied sample is worse than no
sample. It now runs the offline planner and writes what it prints.

Only the block between the markers is touched. Everything else in the README stays
hand-written, because the rest is an argument and this is a transcript.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shotplan import cli  # noqa: E402

README = ROOT / "README.md"
BEGIN = "<!-- sample:begin -->"
END = "<!-- sample:end -->"
KEEP_LINES = 10  # header, rule, five slots, rule, cost, score


def render() -> str:
    """Run the offline planner and capture what it prints."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(["plan", "--mock-mode", "good"])
    if code != 0:
        raise SystemExit(f"planner exited {code}; refusing to write a sample from a failed run")
    lines = [line.rstrip() for line in buffer.getvalue().splitlines() if line.strip()]
    sample = lines[:KEEP_LINES]
    # The plan id is random per run; a sample that changes every run cannot be checked.
    sample[0] = re.sub(r"plan_[0-9a-f]+", "plan_a1b2...", sample[0])
    return "```\n" + "\n".join(sample) + "\n```"


def replace_block(text: str, block: str) -> str:
    pattern = re.compile(
        re.escape(BEGIN) + r"\n.*?\n" + re.escape(END), re.DOTALL
    )
    if not pattern.search(text):
        raise SystemExit(f"markers not found in {README.name}")
    return pattern.sub(f"{BEGIN}\n{block}\n{END}", text, count=1)


def main() -> int:
    body = README.read_text(encoding="utf-8")
    block = render()
    updated = replace_block(body, block)
    if "--write" in sys.argv:
        README.write_text(updated, encoding="utf-8", newline="\n")
        print(f"wrote {README.relative_to(ROOT)} sample")
        return 0
    if "--check" in sys.argv:
        if updated != body:
            print(
                "README 示例与实际输出不一致：运行 scripts/render_readme_sample.py --write",
                file=sys.stderr,
            )
            return 1
        print("README sample matches the planner", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
