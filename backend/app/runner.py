"""Run the research graph and yield UI events. Shared by the API and the CLI."""

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.checkpoints import open_checkpointer
from app.config import get_settings
from app.graph.build import build_graph, build_graph_uncheckpointed


def _config(run_id: str, durable: bool, active_before_s: float) -> dict[str, Any]:
    s = get_settings()
    cfg: dict[str, Any] = {
        "max_concurrency": s.max_parallel_researchers,
        "run_name": "deep-synth-research",
        "metadata": {"run_id": run_id, "provider": s.llm_provider, "model": s.model_name},
        "tags": [s.llm_provider],
        "recursion_limit": 40,
    }
    # Per-attempt timing: the time budget restarts on continue; reported duration excludes pauses.
    cfg["configurable"] = {"attempt_started_at": time.time(), "active_before_s": active_before_s}
    if durable:
        cfg["configurable"]["thread_id"] = run_id  # one checkpoint thread per run
    return cfg


async def run_research(
    topic: str,
    run_id: str | None = None,
    *,
    resume: bool = False,
    durable: bool = True,
    active_before_s: float = 0.0,
) -> AsyncIterator[dict[str, Any]]:
    """Yield custom events from nodes; the final event has kind='result' with report + markdown.

    durable: save a checkpoint after every step so a failed run can continue later.
    resume:  continue this run's thread from its last checkpoint instead of starting over.
    active_before_s: time earlier attempts spent running, so the reported duration excludes pauses.
    """
    run_id = run_id or uuid.uuid4().hex[:12]
    if resume and not durable:
        raise ValueError("resume requires durable=True")
    config = _config(run_id, durable, active_before_s)
    graph_input = None if resume else {"topic": topic, "started_at": time.time()}

    final: dict[str, Any] = {}
    if durable:
        async with open_checkpointer() as saver:
            graph = build_graph(saver)
            async for mode, chunk in graph.astream(graph_input, config=config, stream_mode=["custom", "values"]):
                if mode == "custom":
                    yield chunk
                else:
                    final = chunk
    else:
        graph = build_graph_uncheckpointed()
        async for mode, chunk in graph.astream(graph_input, config=config, stream_mode=["custom", "values"]):
            if mode == "custom":
                yield chunk
            else:
                final = chunk

    yield {
        "node": "system",
        "kind": "result",
        "message": "complete",
        "data": {"report": final.get("report"), "markdown": final.get("markdown")},
    }


async def resume_point(run_id: str) -> list[str]:
    """Names of the steps a continued run would start with; empty if there is nothing to continue."""
    async with open_checkpointer() as saver:
        snap = await build_graph(saver).aget_state({"configurable": {"thread_id": run_id}})
        return list(dict.fromkeys(snap.next))
