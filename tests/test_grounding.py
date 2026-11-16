"""A fabricated citation must degrade into silence, not fail the plan."""

from __future__ import annotations

from shotplan.domain.models import Plan, TimeSlot
from shotplan.grounding import sanitize_source_refs
from shotplan.knowledge.tools import get_tools

KB = get_tools()


def _plan(refs: list[str], slot_refs: list[str]) -> Plan:
    return Plan(
        city="xian", date="2026-10-04", total_cost_cny=600,
        source_refs=refs,
        slots=[TimeSlot("08:00", "09:30", "makeup", "indoor_studio", "妆造", 400,
                        source_refs=slot_refs)],
    )


def test_invented_citations_are_dropped():
    plan = _plan(["locations.json#city_wall", "locations.json#atlantis"],
                 ["styles.json#tang", "locations.json#nowhere"])
    cleaned, dropped = sanitize_source_refs(plan, KB)
    assert dropped == 2
    assert cleaned.source_refs == ["locations.json#city_wall"]
    assert cleaned.slots[0].source_refs == ["styles.json#tang"]
    # the original is untouched
    assert len(plan.source_refs) == 2


def test_a_cross_city_citation_is_not_valid():
    """Pointing at another city's scene is still a fabricated citation."""
    plan = _plan(["locations.json#suzhou_museum"], [])
    cleaned, dropped = sanitize_source_refs(plan, KB)
    assert dropped == 1
    assert cleaned.source_refs == []


def test_clean_plans_are_returned_untouched():
    plan = _plan(["locations.json#city_wall"], ["styles.json#tang"])
    cleaned, dropped = sanitize_source_refs(plan, KB)
    assert dropped == 0
    assert cleaned is plan
