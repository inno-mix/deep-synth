"""Agent nodes: planner, researcher (parallel), critic, verifier, writer."""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from rapidfuzz import fuzz

from app.config import get_settings
from app.graph.state import ResearchState, ResearchTask, emit
from app.llm import structured
from app.prompts import prompt
from app.report import render_markdown
from app.schemas import (
    Citation,
    Critique,
    Finding,
    FindingsExtraction,
    Report,
    ReportDraft,
    ResearchPlan,
    SearchQuery,
    SearchResult,
    Source,
    VerifiedFinding,
)
from app.tools.fetch import HEADERS, fetch_text
from app.tools.search import web_search

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ planner


async def planner(state: ResearchState) -> dict:
    s = get_settings()
    emit("planner", "start", "Breaking the topic into sub-questions")
    plan = await structured(
        ResearchPlan,
        prompt("planner", max_sub_questions=s.max_sub_questions, today=datetime.now(UTC).date()),
        f"Topic: {state['topic']}",
        run_name="planner",
    )
    plan.sub_questions = plan.sub_questions[: s.max_sub_questions]
    emit(
        "planner",
        "done",
        f"Planned {len(plan.sub_questions)} sub-questions",
        objective=plan.objective,
        sub_questions=[q.model_dump() for q in plan.sub_questions],
    )
    return {"plan": plan, "iteration": 0, "started_at": state.get("started_at") or time.time()}


# ------------------------------------------------------------------ researcher


def _domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def _match_url(url: str, known: dict[str, Source]) -> str | None:
    """Small models sometimes mangle URLs slightly; map back to a known source."""
    if url in known:
        return url
    stripped = url.rstrip("/")
    for k in known:
        if k.rstrip("/") == stripped or (stripped and (k.startswith(stripped) or stripped.startswith(k))):
            return k
    return None


async def researcher(task: ResearchTask) -> dict:
    s = get_settings()
    sq, lane = task["sub_question"], task["lane"]
    node = f"researcher:{task['iteration']}:{lane}"
    emit(node, "start", sq.question, queries=sq.queries)

    batches = await asyncio.gather(
        *(web_search(SearchQuery(query=q, max_results=s.results_per_query)) for q in sq.queries)
    )
    seen = set(task["seen_urls"])
    picked: list[SearchResult] = []
    for r in (r for batch in batches for r in batch):
        if r.url not in seen and len(picked) < s.sources_per_question:
            seen.add(r.url)
            picked.append(r)
    emit(node, "progress", f"Reading {len(picked)} sources", urls=[r.url for r in picked])

    async with httpx.AsyncClient(follow_redirects=False, headers=HEADERS, timeout=12) as client:
        texts = await asyncio.gather(
            *(asyncio.sleep(0, r.content) if r.content else fetch_text(r.url, client) for r in picked)
        )
    sources: dict[str, Source] = {}
    for r, text in zip(picked, texts, strict=True):
        text = (text or r.snippet).strip()
        if len(text) >= 200:
            sources[r.url] = Source(url=r.url, title=r.title, text=text[: s.max_source_chars])

    findings: list[Finding] = []
    if sources:
        corpus = "\n\n".join(
            f"### SOURCE {i + 1}\nURL: {src.url}\nTITLE: {src.title}\n---\n{src.text[: s.llm_source_chars]}"
            for i, src in enumerate(sources.values())
        )
        extraction = await structured(
            FindingsExtraction,
            prompt("researcher"),
            f"Overall topic: {task['topic']}\nSub-question: {sq.question}\n\nSOURCES:\n{corpus}",
            run_name="researcher",
        )
        for f in extraction.findings:
            url = _match_url(f.source_url, sources)
            if url:
                findings.append(f.model_copy(update={"source_url": url, "sub_question": sq.question}))
            else:
                log.warning("dropping finding citing unknown URL %r (lane %s)", f.source_url, node)
        if not extraction.findings:
            log.info("%s: model found nothing relevant in %d sources", node, len(sources))

    emit(
        node,
        "done",
        f"{len(findings)} findings from {len(sources)} sources",
        question=sq.question,
        sources=[{"url": u, "title": src.title, "domain": _domain(u)} for u, src in sources.items()],
        findings=[f.claim for f in findings],
    )
    return {"findings": findings, "sources": sources, "searched_queries": list(sq.queries)}


# ------------------------------------------------------------------ critic


def _findings_digest(findings: list[Finding]) -> str:
    by_q: dict[str, list[Finding]] = {}
    for f in findings:
        by_q.setdefault(f.sub_question or "General", []).append(f)
    lines = []
    for q, fs in by_q.items():
        lines.append(f"## {q}")
        lines += [f"- {f.claim} ({_domain(f.source_url)})" for f in fs]
    return "\n".join(lines) or "(no findings yet)"


async def critic(state: ResearchState) -> dict:
    s = get_settings()
    iteration = state.get("iteration", 0) + 1
    emit("critic", "start", f"Reviewing {len(state.get('findings', []))} findings (round {iteration})")
    crit = await structured(
        Critique,
        prompt("critic", max_follow_up=min(3, s.max_parallel_researchers)),
        f"Topic: {state['topic']}\n\nAlready searched: {state.get('searched_queries', [])}\n\n"
        f"FINDINGS:\n{_findings_digest(state.get('findings', []))}",
        run_name="critic",
    )
    will_revise = should_revise(state, crit, iteration)
    emit(
        "critic",
        "done",
        f"Coverage {crit.coverage_score}/10 → " + ("requesting follow-up research" if will_revise else "sufficient"),
        verdict=crit.verdict,
        coverage=crit.coverage_score,
        gaps=crit.gaps,
        contradictions=crit.contradictions,
        follow_up=[q.question for q in crit.follow_up] if will_revise else [],
    )
    return {"critiques": [crit], "iteration": iteration, "revise": will_revise}


def should_revise(state: ResearchState, crit: Critique, iteration: int) -> bool:
    s = get_settings()
    elapsed = time.time() - state.get("started_at", time.time())
    # Leave roughly half the budget for verification + writing.
    time_ok = elapsed < s.run_deadline_s * 0.55
    # Small models sometimes say "sufficient" while scoring coverage 3/10; trust the score too.
    wants_more = crit.verdict == "revise" or crit.coverage_score <= LOW_COVERAGE
    return wants_more and bool(crit.follow_up) and iteration <= s.max_revisions and time_ok


LOW_COVERAGE = 5


# ------------------------------------------------------------------ verifier

_WS = re.compile(r"\s+")


def _norm(t: str) -> str:
    return _WS.sub(" ", t.replace("“", '"').replace("”", '"').replace("’", "'")).strip().lower()


def verify_findings(
    findings: list[Finding], sources: dict[str, Source], threshold: int
) -> tuple[list[VerifiedFinding], list[Citation], int]:
    """Deterministically check each evidence quote really appears in its source text."""
    kept: list[VerifiedFinding] = []
    citations: dict[str, Citation] = {}
    dropped = 0
    for f in findings:
        src = sources.get(f.source_url)
        quote = _norm(f.evidence_quote)
        score = int(fuzz.partial_ratio(quote, _norm(src.text))) if src and len(quote) >= 12 else 0
        duplicate = any(fuzz.token_set_ratio(f.claim, k.claim) > 92 for k in kept)
        if score < 60 or duplicate:
            dropped += 1
            continue
        if f.source_url not in citations:
            citations[f.source_url] = Citation(n=len(citations) + 1, url=f.source_url, title=src.title)
        kept.append(
            VerifiedFinding(
                **f.model_dump(),
                id=len(kept) + 1,
                match_score=score,
                confidence="high" if score >= 95 else "medium" if score >= threshold else "low",
                citation=citations[f.source_url].n,
            )
        )
    return kept, list(citations.values()), dropped


async def verifier(state: ResearchState) -> dict:
    s = get_settings()
    findings = state.get("findings", [])
    emit("verifier", "start", f"Checking {len(findings)} quotes against source text")
    kept, _, dropped = verify_findings(findings, state.get("sources", {}), s.verify_threshold)
    emit(
        "verifier",
        "done",
        f"Verified {len(kept)} findings, rejected {dropped}",
        kept=len(kept),
        dropped=dropped,
        confidence={c: sum(1 for k in kept if k.confidence == c) for c in ("high", "medium", "low")},
    )
    return {"verified": kept}


# ------------------------------------------------------------------ writer

_CITE = re.compile(r"\[(\d+)\]")


def _clean_citations(text: str, valid: set[int]) -> str:
    return _CITE.sub(lambda m: m.group(0) if int(m.group(1)) in valid else "", text)


async def writer(state: ResearchState) -> dict:
    s = get_settings()
    verified = state.get("verified", [])
    emit("writer", "start", f"Drafting brief from {len(verified)} verified findings")
    citations = _citations_from(verified, state.get("sources", {}))
    valid = {c.n for c in citations}
    evidence = "\n".join(f"[{v.citation}] ({v.confidence}) {v.claim}  — re: {v.sub_question}" for v in verified)
    last = state.get("critiques", [])[-1:] if state.get("critiques") else []
    notes = ""
    if last:
        notes = f"\n\nCRITIC NOTES:\nGaps: {last[0].gaps}\nContradictions: {last[0].contradictions}"
    draft = await structured(
        ReportDraft,
        prompt("writer"),
        f"Topic: {state['topic']}\n\nVERIFIED FINDINGS (cite by number):\n{evidence or '(none)'}{notes}",
        run_name="writer",
    )
    draft.tldr = _clean_citations(draft.tldr, valid)
    for sec in draft.sections:
        sec.body = _clean_citations(sec.body, valid)
    for row in draft.comparison:
        row.values = [_clean_citations(v, valid) for v in row.values]

    report = Report(
        **draft.model_dump(),
        topic=state["topic"],
        citations=citations,
        generated_at=datetime.now(UTC),
        model=f"{s.llm_provider}:{s.model_name}",
        stats={
            "sources_read": len(state.get("sources", {})),
            "findings": len(state.get("findings", [])),
            "verified": len(verified),
            "revisions": max(0, state.get("iteration", 1) - 1),
            "seconds": round(time.time() - state.get("started_at", time.time()), 1),
        },
    )
    md = render_markdown(report)
    emit("writer", "done", "Brief ready", title=report.title)
    return {"report": report, "markdown": md}


def _citations_from(verified, sources) -> list[Citation]:
    out: dict[int, Citation] = {}
    for v in verified:
        if v.citation not in out:
            out[v.citation] = Citation(n=v.citation, url=v.source_url, title=sources[v.source_url].title)
    return [out[k] for k in sorted(out)]
