"""Wire the agents into a cyclic LangGraph state machine.

planner ─┬─> researcher x N (parallel, Send API) ─> critic
         │                                           │
         │        ┌── revise & budget left ──────────┤
         │        v                                  │
         └── researcher x M (follow-ups) ──> critic  └── sufficient ─> verifier ─> writer ─> END
"""

from functools import cache

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.graph.nodes import critic, planner, researcher, verifier, writer
from app.graph.state import ResearchState
from app.schemas import SubQuestion


def _sends(state: ResearchState, questions: list[SubQuestion], iteration: int) -> list[Send]:
    seen = list(state.get("sources", {}).keys())
    return [
        Send(
            "researcher",
            {"topic": state["topic"], "sub_question": q, "seen_urls": seen, "lane": i, "iteration": iteration},
        )
        for i, q in enumerate(questions)
    ]


def fan_out_plan(state: ResearchState) -> list[Send]:
    return _sends(state, state["plan"].sub_questions, 0)


def route_after_critic(state: ResearchState) -> list[Send] | str:
    if state.get("revise"):
        return _sends(state, state["critiques"][-1].follow_up, state["iteration"])
    return "verifier"


@cache
def build_graph():
    g = StateGraph(ResearchState)
    g.add_node("planner", planner)
    g.add_node("researcher", researcher)
    g.add_node("critic", critic)
    g.add_node("verifier", verifier)
    g.add_node("writer", writer)

    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", fan_out_plan, ["researcher"])
    g.add_edge("researcher", "critic")
    g.add_conditional_edges("critic", route_after_critic, ["researcher", "verifier"])
    g.add_edge("verifier", "writer")
    g.add_edge("writer", END)
    return g.compile()
