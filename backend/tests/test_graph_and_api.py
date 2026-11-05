import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.graph import nodes
from app.runner import run_research
from tests.fakes import FakeLLM, fake_search


@pytest.fixture
def fake(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(nodes, "structured", llm)
    monkeypatch.setattr(nodes, "web_search", fake_search)
    return llm


async def _collect(topic="Compare pgvector vs Qdrant performance"):
    return [ev async for ev in run_research(topic)]


async def test_full_graph_loops_once_then_writes_verified_report(fake):
    events = await _collect()
    result = events[-1]["data"]
    report, md = result["report"], result["markdown"]

    assert fake.calls["Critique"] == 2  # revise -> follow-up research -> sufficient
    assert fake.calls["FindingsExtraction"] == 3  # 2 planned + 1 follow-up researcher
    claims = " ".join(c for ev in events if ev["node"] == "verifier" for c in [ev["message"]])
    assert "rejected" in claims
    assert report.stats["revisions"] == 1
    assert "[99]" not in md  # hallucinated citation removed
    assert {c.url for c in report.citations} == {
        "https://a.example/pgvector",
        "https://b.example/qdrant",
        "https://c.example/cost",
    }
    assert "100x" not in md


async def test_critic_loop_is_bounded(monkeypatch, fake):
    fake.verdicts = ["revise"] * 10
    monkeypatch.setattr(get_settings(), "max_revisions", 2)
    events = await _collect()
    assert fake.calls["Critique"] == 3  # initial + 2 revisions, then forced to verifier
    assert events[-1]["data"]["report"].stats["revisions"] == 2


def test_api_create_stream_and_fetch(fake):
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"] is True
        assert client.post("/api/runs", json={"topic": "nope"}).status_code == 422

        run_id = client.post("/api/runs", json={"topic": "Compare pgvector vs Qdrant performance"}).json()["id"]
        with client.stream("GET", f"/api/runs/{run_id}/stream") as r:
            body = "".join(r.iter_text())
        events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
        assert events[0]["node"] == "planner"
        assert events[-1] == {**events[-1], "node": "system", "kind": "done"}

        detail = client.get(f"/api/runs/{run_id}").json()
        assert detail["status"] == "done" and detail["report"]["title"] == "pgvector vs Qdrant"
        assert len(detail["events"]) == len(events)
        assert client.get(f"/api/runs/{run_id}/report.md").text.startswith("# pgvector vs Qdrant")

        # Reconnecting replays stored events instead of re-running the graph.
        calls_before = dict(fake.calls)
        with client.stream("GET", f"/api/runs/{run_id}/stream") as r:
            replay = "".join(r.iter_text())
        assert replay.count("data:") == len(events) and fake.calls == calls_before
        assert any(row["id"] == run_id for row in client.get("/api/runs").json())


def test_run_survives_client_disconnect(fake):
    import time as _t

    from app.main import app

    fake.delay = 0.1  # ~0.8s total, so the hang-up below lands mid-run
    with TestClient(app) as client:
        run_id = client.post("/api/runs", json={"topic": "Compare pgvector vs Qdrant performance"}).json()["id"]
        with client.stream("GET", f"/api/runs/{run_id}/stream") as r:
            next(r.iter_lines())  # read one event, then hang up mid-run
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "running"
        for _ in range(50):
            detail = client.get(f"/api/runs/{run_id}").json()
            if detail["status"] == "done":
                break
            _t.sleep(0.1)
        assert detail["status"] == "done"
        assert detail["events"][-1]["kind"] == "done"


def test_orphaned_running_run_is_marked_interrupted():
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app import db
    from app.main import app

    with TestClient(app) as client:
        stale = db.Run(id="stale0000001", topic="orphaned run topic", status="running")

        async def seed():
            await db.save_run(stale)
            async with db.session() as s:  # backdate the heartbeat
                row = await s.get(db.Run, stale.id)
                row.updated_at = datetime.now(UTC) - timedelta(minutes=5)
                await s.commit()

        client.portal.call(seed) if hasattr(client, "portal") and client.portal else asyncio.run(seed())
        detail = client.get(f"/api/runs/{stale.id}").json()
        assert detail["status"] == "error"
        assert "interrupted" in detail["error"]
        assert detail["events"][-1] == {**detail["events"][-1], "node": "system", "kind": "error"}
