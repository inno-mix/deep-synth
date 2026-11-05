"""Fetch a page and extract readable main text (trafilatura), with size/time caps."""

import asyncio
import logging

import httpx
import trafilatura

log = logging.getLogger(__name__)

MAX_BYTES = 2_000_000
HEADERS = {"User-Agent": "DeepSynthResearchBot/0.1 (+https://github.com/)"}


def extract_text(html: str, url: str | None = None) -> str:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_recall=True)
    return (text or "").strip()


async def fetch_text(url: str, client: httpx.AsyncClient | None = None, request_timeout: float = 12.0) -> str:
    """Return extracted page text, or "" on any failure (never raises)."""
    own = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, headers=HEADERS, timeout=request_timeout)
    try:
        r = await client.get(url)
        ctype = r.headers.get("content-type", "")
        if r.status_code >= 400 or ("html" not in ctype and "text" not in ctype):
            return ""
        html = r.content[:MAX_BYTES].decode(r.encoding or "utf-8", errors="ignore")
        return await asyncio.to_thread(extract_text, html, url)
    except Exception as e:
        log.info("fetch failed %s: %s", url, e)
        return ""
    finally:
        if own:
            await client.aclose()
