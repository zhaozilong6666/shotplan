"""Deploy config is only useful if it is checked like code."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_deploy_config as cfg  # noqa: E402


def _load() -> tuple[dict, dict]:
    scrape = yaml.safe_load(cfg.SCRAPE.read_text(encoding="utf-8"))
    rules = yaml.safe_load(cfg.RULES.read_text(encoding="utf-8"))
    return scrape, rules


def test_rules_are_generated_from_the_doc():
    assert cfg.RULES.read_text(encoding="utf-8") == cfg.extract_rules()


def test_config_has_a_scrape_job_and_valid_alerts():
    scrape, rules = _load()
    assert cfg.validate(scrape, rules) == []
    alerts = rules["groups"][0]["rules"]
    assert len(alerts) >= 5
    assert all(alert["alert"].startswith("Shotplan") for alert in alerts)


def test_blackbox_module_is_valid():
    config = yaml.safe_load(cfg.BLACKBOX.read_text(encoding="utf-8"))
    assert cfg.validate_blackbox(config) == []


def test_blackbox_without_a_prober_is_rejected():
    config = yaml.safe_load(cfg.BLACKBOX.read_text(encoding="utf-8"))
    broken = copy.deepcopy(config)
    broken["modules"]["http_2xx"]["prober"] = "tcp"
    assert any("prober" in problem for problem in cfg.validate_blackbox(broken))


def test_an_unknown_metric_is_rejected():
    scrape, rules = _load()
    broken = copy.deepcopy(rules)
    broken["groups"][0]["rules"][0]["expr"] = "shotplan_does_not_exist > 0"
    problems = cfg.validate(scrape, broken)
    assert any("shotplan_does_not_exist" in problem for problem in problems)


def test_a_missing_for_is_rejected():
    scrape, rules = _load()
    broken = copy.deepcopy(rules)
    broken["groups"][0]["rules"][0].pop("for")
    problems = cfg.validate(scrape, broken)
    assert any("没有 for" in problem for problem in problems)
