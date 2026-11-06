"""Wire the agents into a cyclic LangGraph state machine.

planner ─┬─> researcher x N (parallel, Send API) ─> critic
         │                                           │
         │        ┌── revise & budget left ──────────┤
         │        v                                  │
         └── researcher x M (follow-ups) ──> critic  └── sufficient ─> verifier ─> writer ─> END
"""

from functools import cache

import httpx
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send

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


def is_transient(exc: Exception) -> bool:
    """Errors worth an automatic retry: dropped connections, timeouts, rate limits, 5xx from the model server,
    and interrupted system calls (EINTR, e.g. a file read interrupted while a synced folder fetches it).

    Bad model output (StructuredOutputError) and bugs are not retried here; the user can choose to continue.
    """
    if isinstance(exc, httpx.TransportError | ConnectionError | InterruptedError):
        return True
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


# Two retries with 2s/4s backoff: enough for an Ollama model reload or a brief network drop.
RETRY = RetryPolicy(max_attempts=3, initial_interval=2.0, backoff_factor=2.0, retry_on=is_transient)


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(ResearchState)
    for name, fn in (
        ("planner", planner),
        ("researcher", researcher),
        ("critic", critic),
        ("verifier", verifier),
        ("writer", writer),
    ):
        g.add_node(name, fn, retry_policy=RETRY)

    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", fan_out_plan, ["researcher"])
    g.add_edge("researcher", "critic")
    g.add_conditional_edges("critic", route_after_critic, ["researcher", "verifier"])
    g.add_edge("verifier", "writer")
    g.add_edge("writer", END)
    return g.compile(checkpointer=checkpointer)


@cache
def build_graph_uncheckpointed():
    """For drawing the graph and one-off CLI runs."""
    return build_graph(None)
