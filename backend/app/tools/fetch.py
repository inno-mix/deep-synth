"""Fetch a page and extract readable main text (trafilatura), with SSRF, size and time guards.

URLs come from web search results, which outside parties can influence, so every request
(including each redirect hop) is checked before it is sent:
- only http/https, only default-ish ports
- the hostname must resolve exclusively to publicly routable addresses
  (no loopback, private, link-local/metadata, CGNAT, multicast or reserved ranges)
- bodies are streamed and reading stops at MAX_BYTES
"""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura

log = logging.getLogger(__name__)

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}
HEADERS = {"User-Agent": "DeepSynthResearchBot/0.1 (+https://github.com/)"}


class BlockedURL(ValueError):
    pass


def extract_text(html: str, url: str | None = None) -> str:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_recall=True)
    return (text or "").strip()


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])  # strip IPv6 zone ids
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


async def check_url(url: str) -> None:
    """Raise BlockedURL unless `url` is http(s) on an allowed port and resolves only to public IPs."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedURL(f"scheme {parts.scheme!r} not allowed")
    if not parts.hostname:
        raise BlockedURL("missing host")
    if parts.username or parts.password:
        raise BlockedURL("credentials in URL not allowed")
    try:
        port = parts.port
    except ValueError as e:
        raise BlockedURL("invalid port") from e
    if port not in ALLOWED_PORTS:
        raise BlockedURL(f"port {port} not allowed")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise BlockedURL(f"cannot resolve {parts.hostname}") from e
    ips = {info[4][0] for info in infos}
    if not ips or not all(_is_public(ip) for ip in ips):
        raise BlockedURL(f"{parts.hostname} resolves to a non-public address")


async def _read_capped(r: httpx.Response) -> bytes | None:
    """Read at most MAX_BYTES; return None if the server declares a larger body."""
    declared = r.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        return None
    buf = bytearray()
    async for chunk in r.aiter_bytes():
        buf += chunk
        if len(buf) >= MAX_BYTES:
            break  # leaving the stream context closes the connection
    return bytes(buf[:MAX_BYTES])


async def fetch_text(url: str, client: httpx.AsyncClient | None = None, request_timeout: float = 12.0) -> str:
    """Return extracted page text, or "" on any failure or blocked URL (never raises)."""
    own = client is None
    client = client or httpx.AsyncClient(headers=HEADERS, timeout=request_timeout)
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(current)
            async with client.stream("GET", current, follow_redirects=False) as r:
                if r.is_redirect:
                    location = r.headers.get("location")
                    if not location:
                        return ""
                    current = urljoin(current, location)
                    continue
                ctype = r.headers.get("content-type", "")
                if r.status_code >= 400 or ("html" not in ctype and "text" not in ctype):
                    return ""
                body = await _read_capped(r)
                if body is None:
                    log.info("fetch skipped %s: body larger than %d bytes", url, MAX_BYTES)
                    return ""
                html = body.decode(r.encoding or "utf-8", errors="ignore")
            return await asyncio.to_thread(extract_text, html, current)
        log.info("fetch gave up on %s: too many redirects", url)
        return ""
    except BlockedURL as e:
        log.warning("fetch blocked %s: %s", url, e)
        return ""
    except Exception as e:
        log.info("fetch failed %s: %s", url, e)
        return ""
    finally:
        if own:
            await client.aclose()
