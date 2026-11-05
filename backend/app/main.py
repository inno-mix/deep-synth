"""FastAPI app: create research runs and stream agent events over Server-Sent Events."""

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from app import db, runner
from app.config import get_settings
from app.schemas import ResearchRequest, RunCreated, RunDetail, RunEvent, RunSummary
from app.tracing import setup_tracing

log = logging.getLogger("deep_synth")
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_tracing()
    await db.init_db()
    yield


app = FastAPI(title="Deep Synth API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ------------------------------------------------------------- rate limit (per instance)
_hits: dict[str, deque[float]] = defaultdict(deque)


def _rate_limit(request: Request) -> None:
    limit = get_settings().rate_limit_per_hour
    fwd = request.headers.get("x-forwarded-for")
    ip = (fwd or (request.client.host if request.client else "?")).split(",")[0].strip()
    q, now = _hits[ip], time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Rate limit exceeded, try again later")
    q.append(now)


# ------------------------------------------------------------- helpers


def _summary(r: db.Run) -> RunSummary:
    return RunSummary(id=r.id, topic=r.topic, status=r.status, created_at=r.created_at, model=r.model)


def _detail(r: db.Run) -> RunDetail:
    return RunDetail(
        **_summary(r).model_dump(),
        report=r.report,
        markdown=r.markdown,
        events=[RunEvent.model_validate(e) for e in r.events],
        error=r.error,
    )


def _sse(ev: RunEvent) -> dict[str, str]:
    return {"event": "agent", "id": str(ev.seq), "data": ev.model_dump_json()}


# ------------------------------------------------------------- routes


@app.get("/api/health")
async def health() -> dict[str, Any]:
    s = get_settings()
    return {
        "ok": True,
        "provider": s.llm_provider,
        "model": s.model_name,
        "search": "tavily" if s.tavily_api_key else "duckduckgo",
    }


@app.post("/api/runs", response_model=RunCreated, status_code=201)
async def create_run(body: ResearchRequest, request: Request) -> RunCreated:
    _rate_limit(request)
    s = get_settings()
    run = db.Run(id=uuid.uuid4().hex[:12], topic=body.topic, model=f"{s.llm_provider}:{s.model_name}")
    await db.save_run(run)
    return RunCreated(id=run.id, status="pending")


@app.get("/api/runs", response_model=list[RunSummary])
async def list_runs() -> list[RunSummary]:
    return [_summary(r) for r in await db.list_runs()]


@app.get("/api/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: str) -> RunDetail:
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return _detail(await _reap_if_stale(run))


@app.get("/api/runs/{run_id}/report.md", response_class=PlainTextResponse)
async def get_markdown(run_id: str) -> str:
    run = await db.get_run(run_id)
    if not run or not run.markdown:
        raise HTTPException(404, "report not ready")
    return run.markdown


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str) -> EventSourceResponse:
    """Start the run on first connect, then follow it.

    The graph runs as a background task, not inside this request, so closing the tab does not
    cancel it. Every connection (first, refresh, second tab) follows the same way: from memory
    when the task lives on this instance, otherwise by polling the database.
    """
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    run = await _reap_if_stale(run)
    if run.status == "pending" and run_id not in _tasks:
        _tasks[run_id] = asyncio.create_task(_execute(run))
    return EventSourceResponse(_follow(run_id), ping=10)


# ------------------------------------------------------------- execution

HEARTBEAT_S = 15
STALE_AFTER_S = 90  # a running run with no heartbeat for this long was interrupted
_tasks: dict[str, asyncio.Task] = {}
_live: dict[str, list[RunEvent]] = {}


def _event(events: list[RunEvent], node: str, kind: str, message: str, data: dict | None = None) -> RunEvent:
    ev = RunEvent(seq=len(events), node=node, kind=kind, message=message, data=data or {}, ts=datetime.now(UTC))
    events.append(ev)
    return ev


async def _execute(run: db.Run) -> None:
    events = _live.setdefault(run.id, [])
    lock = asyncio.Lock()

    async def flush() -> None:
        async with lock:
            run.events = [e.model_dump(mode="json") for e in events]
            await db.save_run(run)  # also bumps updated_at, which is the heartbeat

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await flush()

    run.status = "running"
    await flush()
    hb = asyncio.create_task(heartbeat())
    try:
        async with asyncio.timeout(get_settings().run_deadline_s + 25):
            async for raw in runner.run_research(run.topic, run_id=run.id):
                if raw["kind"] == "result":
                    report = raw["data"]["report"]
                    run.report = report.model_dump(mode="json") if report else None
                    run.markdown = raw["data"]["markdown"]
                else:
                    _event(events, raw["node"], raw["kind"], raw["message"], raw.get("data"))
        if not run.markdown:
            raise RuntimeError("graph finished without a report")
        run.status = "done"
        _event(events, "system", "done", "Report ready")
    except asyncio.CancelledError:
        run.status, run.error = "error", "Run was cancelled because the server shut down. Start it again."
        _event(events, "system", "error", run.error)
        raise
    except Exception as e:
        log.exception("run %s failed", run.id)
        msg = "Ran out of time before the brief was written." if isinstance(e, TimeoutError) else str(e)
        run.status, run.error = "error", f"{type(e).__name__}: {msg}"[:2000]
        _event(events, "system", "error", run.error)
    finally:
        hb.cancel()
        await asyncio.shield(flush())
        _tasks.pop(run.id, None)
        asyncio.get_running_loop().call_later(120, _live.pop, run.id, None)


def _age_s(ts: datetime) -> float:
    if ts.tzinfo is None:  # SQLite returns naive datetimes
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds()


async def _reap_if_stale(run: db.Run) -> db.Run:
    """Mark runs whose executor died (server restart, frozen instance) as failed instead of hanging."""
    if run.status == "running" and run.id not in _tasks and _age_s(run.updated_at) > STALE_AFTER_S:
        run.status = "error"
        run.error = "Run was interrupted before finishing (the server stopped). Start it again."
        run.events = [
            *run.events,
            _event([RunEvent.model_validate(e) for e in run.events], "system", "error", run.error).model_dump(
                mode="json"
            ),
        ]
        await db.save_run(run)
    return run


async def _follow(run_id: str) -> AsyncIterator[dict[str, str]]:
    sent = 0
    while True:
        task = _tasks.get(run_id)
        if run_id in _live:  # executing (or just finished) on this instance
            events = _live[run_id]
            for ev in events[sent:]:
                yield _sse(ev)
            sent = len(events)
            if (task is None or task.done()) and any(e.node == "system" for e in events):
                return
            await asyncio.sleep(0.3)
            continue

        run = await db.get_run(run_id)
        if run is None:
            return
        run = await _reap_if_stale(run)
        for e in run.events[sent:]:
            yield _sse(RunEvent.model_validate(e))
        sent = len(run.events)
        if run.status in ("done", "error"):
            if not any(e["node"] == "system" for e in run.events):
                kind = "done" if run.status == "done" else "error"
                msg = run.error or "Report ready"
                yield _sse(RunEvent(seq=sent, node="system", kind=kind, message=msg, ts=datetime.now(UTC)))
            return
        await asyncio.sleep(1.5)
