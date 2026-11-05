"""Local iteration loop: `uv run python -m app.cli "Compare pgvector vs Qdrant performance"`."""

import argparse
import asyncio
import logging
import time
from pathlib import Path

from app.config import get_settings
from app.runner import run_research
from app.tracing import setup_tracing


async def main(topic: str, out: Path | None) -> None:
    s = get_settings()
    print(f"▶ {topic}\n  provider={s.llm_provider} model={s.model_name}\n")
    t0 = time.time()
    md = None
    async for ev in run_research(topic):
        if ev["kind"] == "result":
            md = ev["data"]["markdown"]
            continue
        mark = {"start": "→", "progress": "·", "done": "✓"}.get(ev["kind"], "!")
        print(f"[{time.time() - t0:6.1f}s] {mark} {ev['node']:<16} {ev['message']}")
    if not md:
        raise SystemExit("no report produced")
    print("\n" + md)
    if out:
        await asyncio.to_thread(out.write_text, md)
        print(f"saved → {out}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    p = argparse.ArgumentParser()
    p.add_argument("topic")
    p.add_argument("-o", "--out", type=Path)
    a = p.parse_args()
    setup_tracing()
    asyncio.run(main(a.topic, a.out))
