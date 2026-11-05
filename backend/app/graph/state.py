"""Shared LangGraph state. Parallel researchers merge into `findings`/`sources` via reducers."""

import operator
from typing import Annotated, Any, TypedDict

from langgraph.config import get_stream_writer

from app.schemas import (
    Critique,
    Finding,
    Report,
    ResearchPlan,
    Source,
    SubQuestion,
    VerifiedFinding,
)


def merge_dicts(a: dict, b: dict) -> dict:
    return {**a, **b}


class ResearchState(TypedDict, total=False):
    topic: str
    started_at: float
    plan: ResearchPlan
    findings: Annotated[list[Finding], operator.add]
    sources: Annotated[dict[str, Source], merge_dicts]
    searched_queries: Annotated[list[str], operator.add]
    critiques: Annotated[list[Critique], operator.add]
    iteration: int
    revise: bool
    verified: list[VerifiedFinding]
    report: Report
    markdown: str


class ResearchTask(TypedDict):
    """Payload sent to each parallel researcher via the Send API."""

    topic: str
    sub_question: SubQuestion
    seen_urls: list[str]
    lane: int
    iteration: int


def emit(node: str, kind: str, message: str, **data: Any) -> None:
    """Push a UI/CLI event on LangGraph's custom stream (no-op outside a graph run)."""
    try:
        writer = get_stream_writer()
    except Exception:
        return
    writer({"node": node, "kind": kind, "message": message, "data": data})
