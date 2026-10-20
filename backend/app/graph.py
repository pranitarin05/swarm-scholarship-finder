"""LangGraph StateGraph: supervisor -> analyst -> enricher -> (later phases).

Later phases add scout, verifier fan-out, ranker, writer, critic with
conditional edges for retry loops.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .agents import analyst, enricher, supervisor
from .state import RunState


def build_graph():
    """Compile the agent graph. Extended phase by phase."""
    graph: StateGraph = StateGraph(RunState)
    graph.add_node("supervisor", supervisor.plan)
    graph.add_node("analyst", analyst.analyse)
    graph.add_node("enricher", enricher.enrich)
    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", "analyst")
    graph.add_edge("analyst", "enricher")
    graph.add_edge("enricher", END)
    return graph.compile()


async def run_pipeline(state: RunState) -> RunState:
    """Execute the graph for one run and return the final state."""
    app = build_graph()
    final: dict = {}
    async for update in app.astream(
        state, config={"configurable": {"thread_id": state.run_id}}
    ):
        final.update(update)
    merged = state.model_copy(deep=True)
    for node_output in final.values():
        if isinstance(node_output, dict):
            for key, value in node_output.items():
                setattr(merged, key, value)
    return merged
