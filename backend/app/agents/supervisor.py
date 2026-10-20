"""Supervisor: plans the run and routes the graph."""
from __future__ import annotations

from ..config import settings
from ..events import emit_event
from ..state import RunPlan, RunState


async def plan(state: RunState) -> dict:
    """Node: emit a run plan and validate inputs before the analyst runs."""
    run_id = state.run_id
    emit_event(run_id, "supervisor", "start", "Supervisor planning the run")
    steps = [
        "1. Profile Analyst: parse resume + marksheet",
        "2. Profile Enricher: GitHub + links evidence",
        "3. Opportunity Scout: live search -> 12-20 candidates",
        "4. Eligibility Verifier: parallel official-page checks",
        "5. Matcher/Ranker: deterministic rubric -> top 5",
        "6. Application Writer: SOP draft from real evidence",
        "7. Fact-Checker: audit or bounce back (max "
        f"{settings.max_critic_rounds} rounds)",
    ]
    notes = (
        f"targets={', '.join(state.preferences.countries) or 'unset'} | "
        f"level={state.preferences.level} | "
        f"budget={state.preferences.budget or 'unset'} | "
        f"min candidates={settings.min_candidates} | "
        f"scout loops allowed={settings.max_scout_loops}"
    )
    if not state.resume_path:
        emit_event(run_id, "supervisor", "error", "No resume uploaded")
        return {"status": "failed", "errors": state.errors + ["resume missing"]}
    emit_event(run_id, "supervisor", "result", f"Plan: {notes}", {"steps": steps})
    emit_event(run_id, "supervisor", "handoff", "Routing to Profile Analyst")
    return {"plan": RunPlan(steps=steps, notes=notes), "status": "running"}
