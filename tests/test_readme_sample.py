"""The README sample is generated, so its failure mode is a stale block, not a typo."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import render_readme_sample as rrs  # noqa: E402


def test_only_the_marked_block_is_replaced():
    text = "前言\n<!-- sample:begin -->\n旧内容\n<!-- sample:end -->\n后记"
    updated = rrs.replace_block(text, "```\n新内容\n```")
    assert "新内容" in updated
    assert "旧内容" not in updated
    assert updated.startswith("前言")
    assert updated.endswith("后记")


def test_missing_markers_fail_loudly():
    """Silently appending the sample would produce two of them."""
    with pytest.raises(SystemExit):
        rrs.replace_block("没有标记的文档", "```\nnew\n```")


def test_the_sample_keeps_the_plan_id_stable():
    block = rrs.render()
    assert "plan_a1b2..." in block
    assert "provider=mock" in block
    assert "得分" in block
