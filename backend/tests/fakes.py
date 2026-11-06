"""Scripted fakes for the LLM and web search so the whole graph runs offline and deterministically."""

import asyncio

from app.schemas import (
    Critique,
    Finding,
    FindingsExtraction,
    ReportDraft,
    ReportSection,
    ResearchPlan,
    SearchResult,
    SubQuestion,
)

PAGE = {
    "https://a.example/pgvector": (
        "pgvector adds vector similarity search to Postgres. HNSW indexes in pgvector deliver "
        "high recall with query latency under 10 milliseconds on one million vectors. " * 5
    ),
    "https://b.example/qdrant": (
        "Qdrant is a vector database written in Rust. Qdrant supports payload filtering during "
        "HNSW search and scalar quantization to reduce memory usage. " * 5
    ),
    "https://c.example/cost": (
        "Running pgvector on a managed Postgres instance costs roughly the same as the database itself. " * 5
    ),
}


async def fake_search(q):
    if "cost" in q.query:
        url = "https://c.example/cost"
    elif "pgvector" in q.query:
        url = "https://a.example/pgvector"
    else:
        url = "https://b.example/qdrant"
    return [SearchResult(title=url.rsplit("/", 1)[-1], url=url, snippet="", content=PAGE[url], provider="tavily")]


class FakeLLM:
    """Replaces app.llm.structured; returns canned objects per schema and counts calls."""

    def __init__(self, critic_verdicts=("revise", "sufficient")):
        self.calls: dict[str, int] = {}
        self.verdicts = list(critic_verdicts)
        self.failures: list[tuple[str, str, BaseException]] = []

    delay = 0.0

    def fail_once(self, schema_name: str, exc: BaseException, when: str = "") -> None:
        """Make the next call for `schema_name` (whose prompt contains `when`) raise `exc`, once."""
        self.failures.append((schema_name, when, exc))

    async def __call__(self, schema, system, user, **_):
        if self.delay:
            await asyncio.sleep(self.delay)
        name = schema.__name__
        self.calls[name] = self.calls.get(name, 0) + 1
        for i, (fail_name, when, exc) in enumerate(self.failures):
            if fail_name == name and when in user:
                del self.failures[i]
                raise exc
        if schema is ResearchPlan:
            return ResearchPlan(
                objective="Compare them",
                sub_questions=[
                    SubQuestion(question="pgvector perf?", queries=["pgvector hnsw latency"]),
                    SubQuestion(question="qdrant perf?", queries=["qdrant hnsw"]),
                ],
            )
        if schema is FindingsExtraction:
            if "c.example/cost" in user:
                return FindingsExtraction(
                    findings=[
                        Finding(
                            claim="pgvector costs about the same as the Postgres instance",
                            evidence_quote="costs roughly the same as the database itself",
                            source_url="https://c.example/cost",
                        )
                    ]
                )
            if "a.example/pgvector" in user:
                return FindingsExtraction(
                    findings=[
                        Finding(
                            claim="pgvector HNSW achieves sub-10ms latency at 1M vectors",
                            evidence_quote="query latency under 10 milliseconds on one million vectors",
                            source_url="https://a.example/pgvector/",  # trailing slash: tests URL repair
                        ),
                        Finding(
                            claim="pgvector is 100x faster than everything",  # fabricated quote
                            evidence_quote="pgvector is one hundred times faster than every database",
                            source_url="https://a.example/pgvector",
                        ),
                    ]
                )
            return FindingsExtraction(
                findings=[
                    Finding(
                        claim="Qdrant supports filtered HNSW and scalar quantization",
                        evidence_quote="supports payload filtering during HNSW search and scalar quantization",
                        source_url="https://b.example/qdrant",
                    )
                ]
            )
        if schema is Critique:
            verdict = self.verdicts.pop(0) if self.verdicts else "sufficient"
            return Critique(
                verdict=verdict,
                coverage_score=6 if verdict == "revise" else 8,
                gaps=["no cost data"] if verdict == "revise" else [],
                follow_up=[SubQuestion(question="pgvector cost?", queries=["pgvector cost"])]
                if verdict == "revise"
                else [],
            )
        if schema is ReportDraft:
            return ReportDraft(
                title="pgvector vs Qdrant",
                tldr="Both are fast [1][2]; bogus cite [99].",
                sections=[ReportSection(heading="Performance", body="pgvector is quick [1]. Qdrant filters [2].")],
                comparison_options=["pgvector", "Qdrant"],
                comparison=[],
                caveats=["Synthetic test data"],
            )
        raise AssertionError(f"unexpected schema {name}")
