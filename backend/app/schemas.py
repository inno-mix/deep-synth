"""Pydantic contracts for every agent input/output, tool payload and API response."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------- API inputs


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=8, max_length=300, description="Technical question to research")

    @field_validator("topic")
    @classmethod
    def strip(cls, v: str) -> str:
        return " ".join(v.split())


# ---------------------------------------------------------------- planner


class SubQuestion(BaseModel):
    question: str = Field(description="A focused sub-question that helps answer the topic")
    queries: list[str] = Field(
        min_length=1, max_length=3, description="1-3 concise web search queries for this sub-question"
    )


class ResearchPlan(BaseModel):
    objective: str = Field(description="One sentence restating what the final brief must answer")
    sub_questions: list[SubQuestion] = Field(min_length=1, max_length=6)


# ---------------------------------------------------------------- tools


class SearchQuery(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    max_results: int = Field(default=4, ge=1, le=10)


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""
    content: str | None = None  # full page text if the provider returned it
    provider: Literal["tavily", "duckduckgo"]


class Source(BaseModel):
    url: str
    title: str
    text: str


# ---------------------------------------------------------------- researcher


class Finding(BaseModel):
    claim: str = Field(description="A specific, factual claim relevant to the sub-question")
    evidence_quote: str = Field(
        description="A short verbatim quote (copied exactly) from the source that supports the claim"
    )
    source_url: str = Field(description="URL of the source the quote was copied from")
    sub_question: str = ""


class FindingsExtraction(BaseModel):
    """LLM output schema for the researcher."""

    findings: list[Finding] = Field(default_factory=list, max_length=8)


# ---------------------------------------------------------------- critic


class Critique(BaseModel):
    verdict: Literal["sufficient", "revise"] = Field(
        description="'revise' only if important gaps or contradictions remain"
    )
    coverage_score: int = Field(ge=1, le=10, description="How completely the findings answer the topic")
    gaps: list[str] = Field(default_factory=list, max_length=5, description="What is missing or weak")
    contradictions: list[str] = Field(default_factory=list, max_length=5)
    follow_up: list[SubQuestion] = Field(
        default_factory=list, max_length=3, description="Targeted sub-questions to close the gaps"
    )


# ---------------------------------------------------------------- verifier


class VerifiedFinding(Finding):
    id: int
    match_score: int = Field(ge=0, le=100)
    confidence: Literal["high", "medium", "low"]
    citation: int  # index into Report.citations


# ---------------------------------------------------------------- writer


class ComparisonRow(BaseModel):
    dimension: str
    values: list[str] = Field(description="One cell per compared option, same order as options")


class ReportSection(BaseModel):
    heading: str
    body: str = Field(description="Markdown paragraphs. Cite with [n] using finding citation numbers.")


class ReportDraft(BaseModel):
    """LLM output schema for the writer (citations are attached deterministically)."""

    title: str
    tldr: str = Field(description="2-3 sentence executive summary with citations [n]")
    sections: list[ReportSection] = Field(min_length=1, max_length=8)
    comparison_options: list[str] = Field(
        default_factory=list, max_length=4, description="Options being compared, empty if not a comparison"
    )
    comparison: list[ComparisonRow] = Field(default_factory=list, max_length=10)
    caveats: list[str] = Field(default_factory=list, max_length=6)


class Citation(BaseModel):
    n: int
    url: str
    title: str


class Report(ReportDraft):
    topic: str
    citations: list[Citation]
    generated_at: datetime
    model: str
    stats: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- streaming / API


RunStatus = Literal["pending", "running", "done", "error"]


class RunEvent(BaseModel):
    seq: int
    node: str
    kind: Literal["start", "progress", "update", "done", "error", "resume"]
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    ts: datetime


class RunCreated(BaseModel):
    id: str
    status: RunStatus


class RunSummary(BaseModel):
    id: str
    topic: str
    status: RunStatus
    created_at: datetime
    model: str


class RunDetail(RunSummary):
    report: Report | None = None
    markdown: str | None = None
    events: list[RunEvent] = Field(default_factory=list)
    error: str | None = None
    resumable: bool = False  # a failed run with saved progress
    resume_from: list[str] = Field(default_factory=list)  # steps a continue would start with
