"""The test suite must not depend on the machine it runs on.

These cover two promises: a developer's `.env` must not change what a unit test
observes, and loading `.env` must not export its values to child processes.
"""

from __future__ import annotations

import os

import shotplan.config as config


def test_local_env_is_skipped_when_the_flag_is_set(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SHOTPLAN_LLM_MODEL=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("SHOTPLAN_SKIP_DOTENV", "1")
    monkeypatch.setattr(config, "_DOTENV", {})

    config.load_dotenv(env_file)

    assert "from-dotenv" not in os.environ.get("SHOTPLAN_LLM_MODEL", "")
    assert "SHOTPLAN_LLM_MODEL" not in config._DOTENV


def test_local_env_is_read_without_being_exported(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SHOTPLAN_LLM_MODEL=from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("SHOTPLAN_SKIP_DOTENV", raising=False)
    monkeypatch.delenv("SHOTPLAN_LLM_MODEL", raising=False)
    monkeypatch.setattr(config, "_DOTENV", {})

    config.load_dotenv(env_file)

    assert "SHOTPLAN_LLM_MODEL" not in os.environ
    assert config._DOTENV["SHOTPLAN_LLM_MODEL"] == "from-dotenv"
    assert config.get_settings().llm_model == "from-dotenv"


def test_the_suite_itself_sees_no_ambient_key():
    """A real key in the developer's environment must not reach a test."""
    assert config.get_settings().llm_api_key == ""
