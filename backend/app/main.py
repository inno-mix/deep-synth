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

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from app import db, runner
from app.checkpoints import setup_checkpointer
from app.config import get_settings
from app.llm import StructuredOutputError
from app.schemas import ResearchRequest, RunCreated, RunDetail, RunEvent, RunSummary
from app.tracing import setup_tracing

log = logging.getLogger("deep_synth")
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_tracing()
    await db.init_db()
    try:
        await setup_checkpointer()
    except Exception:  # runs still work; they just can't be continued after a failure
        log.exception("checkpoint store unavailable; failed runs will not be resumable")
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
_MAX_TRACKED_IPS = 10_000
WINDOW_S = 3600


def client_ip(request: Request) -> str:
    """Client IP for rate limiting.

    Forwarding headers are client-controlled unless a trusted proxy overwrites them, so they are
    read only when TRUST_PROXY_HEADERS is on (default on Vercel, which sets x-real-ip and
    x-forwarded-for to the verified client address and discards client-supplied values).
    """
    if get_settings().trust_proxy_headers:
        real = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
        if real.strip():
            return real.strip()
    return request.client.host if request.client else "unknown"


def _evict(now: float) -> None:
    for key in [k for k, q in _hits.items() if not q or now - q[-1] > WINDOW_S]:
        del _hits[key]
    while len(_hits) >= _MAX_TRACKED_IPS:  # leave room for the key about to be added
        del _hits[next(iter(_hits))]


def _rate_limit(request: Request) -> None:
    limit = get_settings().rate_limit_per_hour
    now = time.time()
    if len(_hits) > _MAX_TRACKED_IPS // 2:
        _evict(now)
    q = _hits[client_ip(request)]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Rate limit exceeded, try again later")
    q.append(now)


# ------------------------------------------------------------- helpers


def _summary(r: db.Run) -> RunSummary:
    return RunSummary(id=r.id, topic=r.topic, status=r.status, created_at=r.created_at, model=r.model)


def _detail(r: db.Run, resume_from: list[str] | None = None) -> RunDetail:
    return RunDetail(
        **_summary(r).model_dump(),
        report=r.report,
        markdown=r.markdown,
        events=[RunEvent.model_validate(e) for e in r.events],
        error=r.error,
        resumable=bool(resume_from),
        resume_from=resume_from or [],
    )


def _is_terminal(e: RunEvent | dict) -> bool:
    node, kind = (e.node, e.kind) if isinstance(e, RunEvent) else (e["node"], e["kind"])
    return node == "system" and kind in ("done", "error")


async def _resume_point(run_id: str) -> list[str]:
    try:
        return await runner.resume_point(run_id)
    except Exception:
        log.exception("could not read checkpoint for run %s", run_id)
        return []


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
    run = await _reap_if_stale(run)
    return _detail(run, await _resume_point(run.id) if run.status == "error" else None)


@app.post("/api/runs/{run_id}/continue", response_model=RunCreated)
async def continue_run(run_id: str, request: Request) -> RunCreated:
    """Resume a failed run from its last completed step. Finished steps are kept, not re-run."""
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    run = await _reap_if_stale(run)
    if run.status != "error":
        raise HTTPException(409, "Only a failed run can be continued.")
    steps = await _resume_point(run.id)
    if not steps:
        raise HTTPException(409, "There's no saved progress to continue from. Start over instead.")
    _rate_limit(request)

    # Keep the invariant "a terminal system event is always the last event": the old failure
    # becomes a 'resume' marker that still carries the error text for the timeline.
    events = list(run.events)
    if events and _is_terminal(events[-1]) and events[-1]["kind"] == "error":
        last = events.pop()
        events.append(
            {
                **last,
                "kind": "resume",
                "message": "Continued after a failure",
                "data": {"error": last["message"], "resume_from": steps},
                "ts": datetime.now(UTC).isoformat(),
            }
        )
    run.events, run.status, run.error = events, "pending", None
    _live.pop(run.id, None)  # drop any in-memory copy of the failed attempt
    await db.save_run(run)
    return RunCreated(id=run.id, status="pending")


@app.post("/api/runs/{run_id}/restart", response_model=RunCreated, status_code=201)
async def restart_run(run_id: str, request: Request) -> RunCreated:
    """Start a fresh run with the same question. The original run stays in history."""
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status in ("pending", "running"):
        raise HTTPException(409, "This run is still going.")
    return await create_run(ResearchRequest(topic=run.topic), request)


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
    # A continued run keeps its earlier events; new ones are appended after them.
    events = [RunEvent.model_validate(e) for e in run.events]
    _live[run.id] = events
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
        resume = bool(await runner.resume_point(run.id))
        active_before = active_seconds(events) if resume else 0.0
        async with asyncio.timeout(get_settings().run_deadline_s + 25):
            async for raw in runner.run_research(
                run.topic, run_id=run.id, resume=resume, active_before_s=active_before
            ):
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
        run.status, run.error = "error", "Run was cancelled because the server shut down."
        _event(events, "system", "error", run.error)
        raise
    except Exception as e:
        # Full details go to server logs only; users get a fixed message (no hosts, URLs or stack text).
        log.exception("run %s failed", run.id)
        run.status, run.error = "error", public_error(e, run.id)
        _event(events, "system", "error", run.error)
    finally:
        hb.cancel()
        await asyncio.shield(flush())
        _tasks.pop(run.id, None)
        asyncio.get_running_loop().call_later(120, _live.pop, run.id, None)


def active_seconds(events: list[RunEvent]) -> float:
    """Running time of earlier attempts: each attempt spans from its first event to its last.

    Attempts are separated by 'resume' markers, so the time a failed run sat waiting is not counted.
    """
    total, start, last = 0.0, None, None
    for ev in events:
        if ev.kind == "resume":
            if start and last:
                total += (last - start).total_seconds()
            start = last = None
            continue
        start = start or ev.ts
        last = ev.ts
    if start and last:
        total += (last - start).total_seconds()
    return total


def public_error(e: BaseException, run_id: str) -> str:
    """Map an internal exception to a fixed, user-safe message."""
    if isinstance(e, TimeoutError):
        return "Ran out of time before the brief was written. Try a narrower question."
    if isinstance(e, StructuredOutputError):
        return (
            "The model kept returning output in the wrong format. Continue to retry that step, or use a larger model."
        )
    if isinstance(e, httpx.ConnectError | ConnectionError):
        return "Couldn't reach the language model. Check that the model server is running."
    return f"The run failed unexpectedly. Server logs have the details (run {run_id})."


def _age_s(ts: datetime) -> float:
    if ts.tzinfo is None:  # SQLite returns naive datetimes
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds()


async def _reap_if_stale(run: db.Run) -> db.Run:
    """Mark runs whose executor died (server restart, frozen instance) as failed instead of hanging."""
    if run.status == "running" and run.id not in _tasks and _age_s(run.updated_at) > STALE_AFTER_S:
        run.status = "error"
        run.error = "Run was interrupted before finishing (the server stopped)."
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
            if (task is None or task.done()) and events and _is_terminal(events[-1]):
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
            if not (run.events and _is_terminal(run.events[-1])):
                kind = "done" if run.status == "done" else "error"
                msg = run.error or "Report ready"
                yield _sse(RunEvent(seq=sent, node="system", kind=kind, message=msg, ts=datetime.now(UTC)))
            return
        await asyncio.sleep(1.5)
