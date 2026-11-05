from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.db import normalize_db_url
from app.graph.nodes import _clean_citations, _match_url, verify_findings
from app.report import render_markdown
from app.schemas import Citation, ComparisonRow, Finding, Report, ReportSection, ResearchRequest, Source

SRC = Source(url="https://x.dev/a", title="A", text="Qdrant uses HNSW graphs with   payload-aware filtering in Rust.")


def test_request_validation():
    assert ResearchRequest(topic="  pgvector   vs qdrant  ").topic == "pgvector vs qdrant"
    with pytest.raises(ValidationError):
        ResearchRequest(topic="short")


def test_verifier_accepts_real_quotes_rejects_fabricated_and_dupes():
    findings = [
        Finding(
            claim="Qdrant filters during HNSW",
            evidence_quote="HNSW graphs with payload-aware filtering",
            source_url=SRC.url,
        ),
        Finding(
            claim="Qdrant filters during HNSW",
            evidence_quote="HNSW graphs with payload-aware filtering",
            source_url=SRC.url,
        ),
        Finding(
            claim="Qdrant is written in Go",
            evidence_quote="Qdrant is implemented in the Go language",
            source_url=SRC.url,
        ),
        Finding(claim="unknown source", evidence_quote="HNSW graphs with payload", source_url="https://nope"),
    ]
    kept, cites, dropped = verify_findings(findings, {SRC.url: SRC}, threshold=80)
    assert [k.claim for k in kept] == ["Qdrant filters during HNSW"]
    assert kept[0].confidence == "high" and kept[0].citation == 1
    assert cites == [Citation(n=1, url=SRC.url, title="A")]
    assert dropped == 3


def test_url_repair():
    known = {"https://x.dev/a": SRC}
    assert _match_url("https://x.dev/a/", known) == "https://x.dev/a"
    assert _match_url("https://other.dev", known) is None


def test_clean_citations_strips_unknown_numbers():
    assert _clean_citations("fast [1][7] ok [2]", {1, 2}) == "fast [1] ok [2]"


def test_render_markdown_has_table_links_and_sources():
    r = Report(
        title="T",
        tldr="Summary [1].",
        sections=[ReportSection(heading="Perf", body="Fast [1].")],
        comparison_options=["pgvector", "Qdrant"],
        comparison=[ComparisonRow(dimension="Lang | core", values=["C [1]"])],
        caveats=["few sources"],
        topic="topic",
        citations=[Citation(n=1, url="https://x.dev/a", title="A")],
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        model="ollama:qwen2.5:7b",
        stats={"verified": 1},
    )
    md = render_markdown(r)
    assert md.startswith("# T\n")
    assert "| Dimension | pgvector | Qdrant |" in md
    assert "| Lang \\| core | C [[1]](#ref-1) |  |" in md  # pipes escaped, missing cell padded
    assert '1. <a id="ref-1"></a>[A](https://x.dev/a)' in md


def test_normalize_neon_url():
    url, args = normalize_db_url("postgres://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require")
    assert url == "postgresql+asyncpg://u:p@ep-x.neon.tech/db"
    assert args == {"ssl": "require"}
    assert normalize_db_url("sqlite+aiosqlite:///x.db") == ("sqlite+aiosqlite:///x.db", {})


def test_ad_redirects_are_filtered():
    from app.tools.search import is_ad_or_redirect

    assert is_ad_or_redirect("https://www.bing.com/aclick?ld=abc&u=xyz")
    assert is_ad_or_redirect("https://site.com/x?utm_source=bingads&utm_medium=cpc")
    assert not is_ad_or_redirect("https://www.redpanda.com/blog/redpanda-vs-kafka-performance-benchmark")


def test_low_coverage_forces_revision_even_if_verdict_says_sufficient():
    import time

    from app.graph.nodes import should_revise
    from app.schemas import Critique, SubQuestion

    follow = [SubQuestion(question="q", queries=["x"])]
    state = {"started_at": time.time()}
    assert should_revise(state, Critique(verdict="sufficient", coverage_score=3, follow_up=follow), 1)
    assert not should_revise(state, Critique(verdict="sufficient", coverage_score=8, follow_up=follow), 1)
    assert not should_revise(state, Critique(verdict="sufficient", coverage_score=3, follow_up=[]), 1)
    assert not should_revise(
        {"started_at": time.time() - 10_000}, Critique(verdict="revise", coverage_score=2, follow_up=follow), 1
    )
