"""Make citations honest before the validator sees them.

A real-model run failed one case with `UNKNOWN_SOURCE_REF`: the model invented a
citation. Fabrication is worse than silence, so the validator is right to call it
hard -- but failing the whole plan over one bad pointer is the wrong product
answer. Code can do better than a prompt here: drop what does not resolve, so
fabrication degrades into absence (which is a soft `NO_SOURCE_REF`), and keep
every citation that is real.

This never invents a citation and never touches the plan's content.
"""

from __future__ import annotations

from dataclasses import replace

from .agents.validator import source_ref_exists
from .domain.models import Plan
from .knowledge.tools import KnowledgeTools


def sanitize_source_refs(plan: Plan, kb: KnowledgeTools) -> tuple[Plan, int]:
    """Return (plan, dropped_count). Uses the validator's own resolver."""
    kept = [ref for ref in plan.source_refs if source_ref_exists(str(ref), plan, kb)]
    dropped = len(plan.source_refs) - len(kept)

    slots = []
    for slot in plan.slots:
        valid = [ref for ref in slot.source_refs if source_ref_exists(str(ref), plan, kb)]
        if len(valid) != len(slot.source_refs):
            dropped += len(slot.source_refs) - len(valid)
            slots.append(replace(slot, source_refs=valid))
        else:
            slots.append(slot)

    if dropped == 0:
        return plan, 0
    return replace(plan, source_refs=kept, slots=slots), dropped
