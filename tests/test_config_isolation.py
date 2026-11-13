"""The test suite must not depend on the machine it runs on.

These cover the `SHOTPLAN_SKIP_DOTENV` escape hatch directly, because a
hermeticity bug is invisible until someone fills in a real `.env` — which is
exactly when it hurts most.
"""

from __future__ import annotations

import os

from shotplan.config import get_settings, load_dotenv


def test_local_env_is_skipped_when_the_flag_is_set(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SHOTPLAN_LLM_MODEL=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("SHOTPLAN_SKIP_DOTENV", "1")

    load_dotenv(env_file)

    assert "from-dotenv" not in os.environ.get("SHOTPLAN_LLM_MODEL", "")


def test_local_env_is_read_without_the_flag(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SHOTPLAN_LLM_MODEL=from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("SHOTPLAN_SKIP_DOTENV", raising=False)
    monkeypatch.delenv("SHOTPLAN_LLM_MODEL", raising=False)

    try:
        load_dotenv(env_file)
        assert os.environ["SHOTPLAN_LLM_MODEL"] == "from-dotenv"
    finally:
        # load_dotenv writes straight to os.environ, which monkeypatch does not
        # own; leaving it behind would silently configure every later test.
        os.environ.pop("SHOTPLAN_LLM_MODEL", None)


def test_the_suite_itself_sees_no_ambient_key():
    """A real key in the developer's environment must not reach a test."""
    assert get_settings().llm_api_key == ""
