"""Web search: Tavily (free tier, returns page content) with a keyless DuckDuckGo fallback."""

import asyncio
import logging

from app.config import get_settings
from app.schemas import SearchQuery, SearchResult

log = logging.getLogger(__name__)
logging.getLogger("primp").setLevel(logging.ERROR)  # ddgs' HTTP client warns about macOS trust stores


async def _tavily(q: SearchQuery, api_key: str) -> list[SearchResult]:
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=api_key)
    resp = await client.search(
        q.query,
        max_results=q.max_results,
        search_depth="basic",
        include_raw_content="text",
        timeout=20,
    )
    return [
        SearchResult(
            title=r.get("title") or r["url"],
            url=r["url"],
            snippet=r.get("content") or "",
            content=r.get("raw_content") or None,
            provider="tavily",
        )
        for r in resp.get("results", [])
        if r.get("url")
    ]


async def _duckduckgo(q: SearchQuery) -> list[SearchResult]:
    from ddgs import DDGS

    def run() -> list[dict]:
        return DDGS().text(q.query, max_results=q.max_results)

    rows = await asyncio.to_thread(run)
    return [
        SearchResult(
            title=r.get("title") or r["href"],
            url=r["href"],
            snippet=r.get("body") or "",
            provider="duckduckgo",
        )
        for r in rows
        if r.get("href")
    ]


async def web_search(q: SearchQuery) -> list[SearchResult]:
    s = get_settings()
    if s.tavily_api_key:
        try:
            results = await _tavily(q, s.tavily_api_key)
            if results:
                return results
        except Exception as e:  # quota, network, etc. -> fall back
            log.warning("tavily failed for %r: %s; falling back to DuckDuckGo", q.query, e)
    try:
        return [r for r in await _duckduckgo(q) if not is_ad_or_redirect(r.url)]
    except Exception as e:
        log.warning("duckduckgo failed for %r: %s", q.query, e)
        return []


_AD_HOSTS = ("bing.com/aclick", "duckduckgo.com/y.js", "googleadservices.com", "doubleclick.net")


def is_ad_or_redirect(url: str) -> bool:
    """DuckDuckGo occasionally returns sponsored redirect links; they are never research sources."""
    return any(h in url for h in _AD_HOSTS) or "utm_medium=cpc" in url
