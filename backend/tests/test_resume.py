"""Failed runs: automatic retry of transient errors, continue from checkpoint, start over."""

import json
import uuid

import httpx
import ollama
import pytest
from fastapi.testclient import TestClient
from langgraph.types import RetryPolicy

from app.graph import build, nodes
from app.graph.build import is_transient
from app.llm import StructuredOutputError
from app.runner import resume_point, run_research
from tests.fakes import FakeLLM, fake_search

TOPIC = "Compare pgvector vs Qdrant performance"


@pytest.fixture
def fake(monkeypatch):
    llm = FakeLLM(critic_verdicts=("sufficient",))  # one research round keeps call counts simple
    monkeypatch.setattr(nodes, "structured", llm)
    monkeypatch.setattr(nodes, "web_search", fake_search)
    # Same retry rules as production, without the 2s/4s waits.
    monkeypatch.setattr(build, "RETRY", RetryPolicy(max_attempts=3, initial_interval=0.01, retry_on=is_transient))
    return llm


async def _run(run_id: str, *, resume: bool = False) -> list[dict]:
    return [ev async for ev in run_research(TOPIC, run_id=run_id, resume=resume)]


# ---------------------------------------------------------------- retry rules


@pytest.mark.parametrize(
    "exc, expected",
    [
        (httpx.ConnectError("refused"), True),
        (httpx.ReadTimeout("slow"), True),
        (ConnectionResetError(), True),
        (InterruptedError(4, "Interrupted system call"), True),  # EINTR seen live on an iCloud-synced folder
        (ollama.ResponseError("model runner has unexpectedly stopped", 500), True),
        (ollama.ResponseError("rate limited", 429), True),
        (ollama.ResponseError("model not found", 404), False),
        (StructuredOutputError("bad json"), False),
        (ValueError("bug"), False),
    ],
)
def test_transient_classification(exc, expected):
    assert is_transient(exc) is expected


async def test_transient_error_is_retried_automatically(fake):
    fake.fail_once("ResearchPlan", httpx.ConnectError("ollama restarting"))
    events = await _run(uuid.uuid4().hex[:12])
    assert events[-1]["data"]["report"] is not None
    assert fake.calls["ResearchPlan"] == 2  # failed once, retried once, no user action needed


async def test_non_transient_error_fails_without_retry(fake):
    fake.fail_once("ResearchPlan", StructuredOutputError("garbage"))
    with pytest.raises(StructuredOutputError):
        await _run(uuid.uuid4().hex[:12])
    assert fake.calls["ResearchPlan"] == 1


# ---------------------------------------------------------------- continue from checkpoint


async def test_continue_reruns_only_the_failed_step(fake):
    run_id = uuid.uuid4().hex[:12]
    fake.fail_once("ReportDraft", StructuredOutputError("writer produced garbage"))
    with pytest.raises(StructuredOutputError):
        await _run(run_id)
    assert await resume_point(run_id) == ["writer"]
    before = dict(fake.calls)

    events = await _run(run_id, resume=True)
    report = events[-1]["data"]["report"]
    assert report is not None and report.title == "pgvector vs Qdrant"
    assert fake.calls["ReportDraft"] == before["ReportDraft"] + 1
    for upstream in ("ResearchPlan", "FindingsExtraction", "Critique"):
        assert fake.calls[upstream] == before[upstream], f"{upstream} should not re-run"
    assert await resume_point(run_id) == []  # finished: nothing left to continue


async def test_continue_reruns_only_the_failed_parallel_lane(fake):
    run_id = uuid.uuid4().hex[:12]
    fake.fail_once("FindingsExtraction", StructuredOutputError("lane 2 garbage"), when="b.example/qdrant")
    with pytest.raises(StructuredOutputError):
        await _run(run_id)
    assert fake.calls["FindingsExtraction"] == 2  # both lanes ran; one failed
    assert await resume_point(run_id) == ["researcher"]

    events = await _run(run_id, resume=True)
    assert fake.calls["FindingsExtraction"] == 3  # only the failed lane ran again
    assert fake.calls["ResearchPlan"] == 1
    cited = {c.url for c in events[-1]["data"]["report"].citations}
    assert cited == {"https://a.example/pgvector", "https://b.example/qdrant"}  # both lanes' findings kept


async def test_new_run_has_nothing_to_continue():
    assert await resume_point("never-ran-0001") == []


# ---------------------------------------------------------------- API


def _stream(client: TestClient, run_id: str) -> list[dict]:
    with client.stream("GET", f"/api/runs/{run_id}/stream") as r:
        body = "".join(r.iter_text())
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


def test_api_continue_after_failure(fake):
    from app.main import app

    fake.fail_once("ReportDraft", StructuredOutputError("writer produced garbage"))
    with TestClient(app) as client:
        run_id = client.post("/api/runs", json={"topic": TOPIC}).json()["id"]
        first = _stream(client, run_id)
        assert first[-1]["node"] == "system" and first[-1]["kind"] == "error"
        assert "wrong format" in first[-1]["message"]

        detail = client.get(f"/api/runs/{run_id}").json()
        assert detail["status"] == "error" and detail["resumable"] is True and detail["resume_from"] == ["writer"]

        before = dict(fake.calls)
        r = client.post(f"/api/runs/{run_id}/continue")
        assert r.status_code == 200 and r.json() == {"id": run_id, "status": "pending"}

        second = _stream(client, run_id)
        assert second[-1]["node"] == "system" and second[-1]["kind"] == "done"
        seqs = [e["seq"] for e in second]
        assert seqs == list(range(len(seqs)))  # one continuous, gap-free history

        # The old failure is kept as a 'resume' marker; it is no longer a terminal event.
        marker = second[len(first) - 1]
        assert marker["kind"] == "resume" and "wrong format" in marker["data"]["error"]
        assert marker["data"]["resume_from"] == ["writer"]
        assert [e for e in second if e["kind"] == "error"] == []

        assert fake.calls["FindingsExtraction"] == before["FindingsExtraction"]
        final = client.get(f"/api/runs/{run_id}").json()
        assert final["status"] == "done" and final["resumable"] is False and final["markdown"]


def test_api_continue_rejects_runs_that_cannot_continue(fake):
    from app import db
    from app.main import app

    with TestClient(app) as client:
        run_id = client.post("/api/runs", json={"topic": TOPIC}).json()["id"]
        _stream(client, run_id)
        r = client.post(f"/api/runs/{run_id}/continue")
        assert r.status_code == 409 and "failed" in r.json()["detail"]  # finished runs can't continue

        # Failed before anything was saved (e.g. checkpoint store was down): only start over works.
        orphan = db.Run(id="orphan000001", topic=TOPIC, status="error", error="boom")
        client.portal.call(db.save_run, orphan)
        assert client.get(f"/api/runs/{orphan.id}").json()["resumable"] is False
        r = client.post(f"/api/runs/{orphan.id}/continue")
        assert r.status_code == 409 and "Start over" in r.json()["detail"]
        assert client.post("/api/runs/doesnotexist/continue").status_code == 404


def test_api_restart_creates_a_fresh_run(fake):
    from app import db
    from app.main import app

    with TestClient(app) as client:
        failed = db.Run(id="failed000001", topic=TOPIC, status="error", error="boom")
        client.portal.call(db.save_run, failed)

        r = client.post(f"/api/runs/{failed.id}/restart")
        assert r.status_code == 201
        new_id = r.json()["id"]
        assert new_id != failed.id
        fresh = client.get(f"/api/runs/{new_id}").json()
        assert fresh["topic"] == TOPIC and fresh["status"] == "pending" and fresh["events"] == []
        assert client.get(f"/api/runs/{failed.id}").json()["status"] == "error"  # original kept

        busy = db.Run(id="running00001", topic=TOPIC, status="running")
        client.portal.call(db.save_run, busy)
        assert client.post(f"/api/runs/{busy.id}/restart").status_code == 409


# ---------------------------------------------------------------- timing across attempts


async def test_resume_after_long_pause_reports_active_time_and_gets_fresh_budget(fake, monkeypatch):
    import time as time_mod

    run_id = uuid.uuid4().hex[:12]
    fake.fail_once("ReportDraft", StructuredOutputError("writer produced garbage"))
    with pytest.raises(StructuredOutputError):
        await _run(run_id)

    real = time_mod.time
    monkeypatch.setattr(time_mod, "time", lambda: real() + 10 * 3600)  # continue ten hours later
    events = [ev async for ev in run_research(TOPIC, run_id=run_id, resume=True, active_before_s=12.5)]
    seconds = events[-1]["data"]["report"].stats["seconds"]
    assert 12.5 <= seconds < 60, seconds  # earlier attempt + this one, not the 10h gap


def test_critic_budget_uses_the_attempt_start():
    import time as time_mod

    from app.graph.nodes import should_revise
    from app.schemas import Critique, SubQuestion

    crit = Critique(verdict="revise", coverage_score=3, follow_up=[SubQuestion(question="q", queries=["x"])])
    old_run = {"started_at": time_mod.time() - 10 * 3600}
    assert not should_revise(old_run, crit, 1)  # measured from the original start: out of time
    assert should_revise(old_run, crit, 1, started_at=time_mod.time())  # fresh attempt: has budget


def test_active_seconds_skips_pauses_between_attempts():
    from datetime import UTC, datetime, timedelta

    from app.main import active_seconds
    from app.schemas import RunEvent

    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    def ev(seq, minutes, kind="start"):
        return RunEvent(seq=seq, node="planner", kind=kind, message="", ts=t0 + timedelta(minutes=minutes))

    events = [ev(0, 0), ev(1, 2), ev(2, 3, "error")]  # attempt 1: 3 minutes
    events[2] = ev(2, 600, "resume")  # failure converted to a resume marker, 10 hours later
    events += [ev(3, 601), ev(4, 605)]  # attempt 2: 4 minutes
    assert active_seconds(events) == (2 + 4) * 60  # attempt 1 spans to its last real event at minute 2
