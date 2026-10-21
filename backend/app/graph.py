"""LangGraph StateGraph: the full agent pipeline with retry loops.

Flow: supervisor -> analyst -> enricher -> scout -> (supervisor loop check)
-> ranker (phase 3+) -> writer -> critic (phase 4).
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .agents import analyst, critic, enricher, ranker, scout, supervisor, verifier, writer
from .config import settings
from .events import emit_event
from .state import RunState


async def check_candidates(state: RunState) -> dict:
    """Supervisor node: loop back to scout if too few usable candidates."""
    run_id = state.run_id
    usable = [
        c for c in state.candidates
        if c.url and (c.official_url or c.url.startswith("http"))
    ]
    enough = len(usable) >= settings.min_candidates
    within_loops = state.scout_loops <= settings.max_scout_loops
    if enough or not within_loops:
        reason = (
            f"{len(usable)} usable candidates (need {settings.min_candidates})"
            if enough
            else f"max scout loops ({settings.max_scout_loops}) reached; "
                 f"continuing with {len(usable)}"
        )
        emit_event(run_id, "supervisor", "result", f"Candidate check: {reason}")
        emit_event(run_id, "supervisor", "handoff", "Routing to Eligibility Verifier")
        return {}
    emit_event(
        run_id, "supervisor", "retry",
        f"Only {len(usable)} usable candidates (< {settings.min_candidates}); "
        f"bouncing back to Scout (loop {state.scout_loops}/{settings.max_scout_loops})",
    )
    return {"route": "rediscover"}


def _route_after_check(state: RunState) -> str:
    if getattr(state, "route", "") == "rediscover":
        return "rediscover"
    return "verify"


def _route_after_critic(state: RunState) -> str:
    if state.audit and state.audit.rejected and state.critic_rounds < settings.max_critic_rounds:
        return "revise"
    return "done"


async def rediscover_node(state: RunState) -> dict:
    return await scout.rediscover(
        state, note=f"fewer than {settings.min_candidates} usable candidates"
    )


def build_graph():
    """Compile the agent graph. Extended in later phases."""
    graph: StateGraph = StateGraph(RunState)
    graph.add_node("supervisor", supervisor.plan)
    graph.add_node("analyst", analyst.analyse)
    graph.add_node("enricher", enricher.enrich)
    graph.add_node("scout", scout.discover)
    graph.add_node("check_candidates", check_candidates)
    graph.add_node("rediscover", rediscover_node)
    graph.add_node("verifier", verifier.verify_all)
    graph.add_node("ranker", ranker.rank)
    graph.add_node("writer", writer.write_sop)
    graph.add_node("critic", critic.audit)
    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", "analyst")
    graph.add_edge("analyst", "enricher")
    graph.add_edge("enricher", "scout")
    graph.add_edge("scout", "check_candidates")
    graph.add_conditional_edges(
        "check_candidates",
        _route_after_check,
        {"rediscover": "rediscover", "verify": "verifier"},
    )
    graph.add_edge("rediscover", "check_candidates")
    graph.add_edge("verifier", "ranker")
    graph.add_edge("ranker", "writer")
    graph.add_edge("writer", "critic")
    graph.add_conditional_edges(
        "critic",
        _route_after_critic,
        {"revise": "verifier", "done": END},
    )
    return graph.compile()


async def run_pipeline(state: RunState, inject_fail: str = "") -> RunState:
    """Execute the graph for one run and return the final state."""
    app = build_graph()
    final: dict = {}
    async for update in app.astream(
        state,
        config={
            "configurable": {"thread_id": state.run_id},
            "recursion_limit": 50,
        },
    ):
        final.update(update)
    merged = state.model_copy(deep=True)
    for node_output in final.values():
        if isinstance(node_output, dict):
            for key, value in node_output.items():
                setattr(merged, key, value)
    return merged
