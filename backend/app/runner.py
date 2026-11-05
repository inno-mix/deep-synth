"""Run the research graph and yield UI events. Shared by the API and the CLI."""

import time
from collections.abc import AsyncIterator
from typing import Any

from app.config import get_settings
from app.graph.build import build_graph


async def run_research(topic: str, run_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
    """Yield custom events from nodes; the final event has kind='result' with report + markdown."""
    s = get_settings()
    graph = build_graph()
    config = {
        "max_concurrency": s.max_parallel_researchers,
        "run_name": "deep-synth-research",
        "metadata": {"run_id": run_id, "provider": s.llm_provider, "model": s.model_name},
        "tags": [s.llm_provider],
        "recursion_limit": 40,
    }
    final: dict[str, Any] = {}
    async for mode, chunk in graph.astream(
        {"topic": topic, "started_at": time.time()}, config=config, stream_mode=["custom", "values"]
    ):
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
