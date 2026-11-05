"""Fetch a page and extract readable main text (trafilatura), with SSRF, size and time guards.

URLs come from web search results, which outside parties can influence, so every hop is guarded:
- only http/https, only common web ports, no credentials in the URL
- the hostname is resolved ONCE, every address must be publicly routable, and the connection is then
  made to that exact IP (Host header and TLS SNI/certificate name keep the original hostname), so a
  second DNS answer cannot redirect the request to an internal address (DNS rebinding)
- IPv6 forms that embed an IPv4 address (NAT64, IPv4-mapped) are unwrapped and the embedded address
  is checked; tunnel/transition prefixes that cannot be verified (6to4, Teredo, IPv4-compatible) are refused
- redirects are followed manually so each Location goes through the same checks
- bodies are read raw, never auto-decompressed; gzip/deflate are inflated in bounded steps, so neither
  a huge body nor a compression bomb can use more than MAX_BYTES of memory
- the whole fetch has a wall-clock limit, which also stops slow-drip responses
"""

import asyncio
import ipaddress
import logging
import socket
import zlib
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura

log = logging.getLogger(__name__)

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
MAX_IP_ATTEMPTS = 3
TOTAL_TIMEOUT_S = 30
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}
HEADERS = {"User-Agent": "DeepSynthResearchBot/0.1 (+https://github.com/)"}

# IPv6 ranges refused outright: they wrap or tunnel IPv4 in ways we cannot verify, or are never public.
_BLOCKED_V6 = tuple(
    ipaddress.ip_network(n)
    for n in (
        "::/96",  # IPv4-compatible (deprecated) and the unspecified address
        "64:ff9b:1::/48",  # local-use NAT64
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
        "100::/64",  # discard-only
        "fc00::/7",  # unique local
        "fe80::/10",  # link local
        "ff00::/8",  # multicast
    )
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")  # well-known NAT64 prefix: the low 32 bits are an IPv4 address


class BlockedURL(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    """A validated destination: the original hostname plus the only IPs we may connect to."""

    hostname: str
    ips: tuple[str, ...]


def extract_text(html: str, url: str | None = None) -> str:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_recall=True)
    return (text or "").strip()


def _is_public_v4(addr: ipaddress.IPv4Address) -> bool:
    return addr.is_global and not addr.is_multicast


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])  # strip IPv6 zone ids
    if isinstance(addr, ipaddress.IPv4Address):
        return _is_public_v4(addr)
    if addr.ipv4_mapped:
        return _is_public_v4(addr.ipv4_mapped)
    if addr in _NAT64:
        return _is_public_v4(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    if any(addr in net for net in _BLOCKED_V6):
        return False
    return addr.is_global and not addr.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    """Resolve once. Separate function so tests can substitute DNS."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


async def check_url(url: str) -> Target:
    """Return the validated Target, or raise BlockedURL."""
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
        ips = await resolve(parts.hostname, port or (80 if parts.scheme == "http" else 443))
    except socket.gaierror as e:
        raise BlockedURL(f"cannot resolve {parts.hostname}") from e
    if not ips or not all(_is_public(ip) for ip in ips):
        raise BlockedURL(f"{parts.hostname} resolves to a non-public address")
    return Target(parts.hostname, tuple(sorted(ips, key=lambda ip: ":" in ip)))  # IPv4 first (stable)


def _decoder(content_encoding: str):
    enc = content_encoding.strip().lower()
    if enc in ("", "identity"):
        return None, True
    if enc in ("gzip", "x-gzip"):
        return zlib.decompressobj(16 + zlib.MAX_WBITS), True
    if enc == "deflate":
        return zlib.decompressobj(zlib.MAX_WBITS), True
    return None, False  # br, zstd, compress, stacked encodings: refuse rather than inflate unbounded


async def _read_capped(r: httpx.Response) -> bytes | None:
    """Read at most MAX_BYTES of *decoded* body; None if refused (too large, unknown or corrupt encoding)."""
    declared = r.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        return None
    decoder, supported = _decoder(r.headers.get("content-encoding", ""))
    if not supported:
        return None
    out = bytearray()
    raw_total = 0
    try:
        async for chunk in r.aiter_raw():  # raw: httpx must not inflate it for us
            raw_total += len(chunk)
            if decoder is None:
                out += chunk
            else:
                data = chunk
                while data and len(out) < MAX_BYTES:
                    out += decoder.decompress(data, MAX_BYTES - len(out))
                    data = decoder.unconsumed_tail
            if len(out) >= MAX_BYTES or raw_total >= MAX_BYTES:
                break  # leaving the stream context closes the connection
    except zlib.error:
        return None
    return bytes(out[:MAX_BYTES])


async def _get(client: httpx.AsyncClient, url: str, target: Target) -> tuple[str | None, str]:
    """One hop to a validated target. Returns (redirect_location, html); html is "" when unusable."""
    parts = urlsplit(url)
    headers = {
        "Host": parts.netloc,
        "Accept-Encoding": "identity",
        "Connection": "close",  # never reuse a connection that was verified for a different hostname
    }
    extensions = {"sni_hostname": target.hostname} if parts.scheme == "https" else {}
    last_error: Exception | None = None
    for ip in target.ips[:MAX_IP_ATTEMPTS]:
        pinned = httpx.URL(url).copy_with(host=ip)
        try:
            async with client.stream(
                "GET", pinned, headers=headers, extensions=extensions, follow_redirects=False
            ) as r:
                if r.is_redirect:
                    return r.headers.get("location"), ""
                ctype = r.headers.get("content-type", "")
                if r.status_code >= 400 or ("html" not in ctype and "text" not in ctype):
                    return None, ""
                body = await _read_capped(r)
                if body is None:
                    log.info("fetch skipped %s: body too large or unsupported encoding", url)
                    return None, ""
                return None, body.decode(r.encoding or "utf-8", errors="ignore")
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            last_error = e  # try the next validated address
    raise last_error or BlockedURL("no usable address")


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        target = await check_url(current)
        location, html = await _get(client, current, target)
        if location is None:
            return await asyncio.to_thread(extract_text, html, current) if html else ""
        current = urljoin(current, location)
    log.info("fetch gave up on %s: too many redirects", url)
    return ""


async def fetch_text(url: str, client: httpx.AsyncClient | None = None, request_timeout: float = 12.0) -> str:
    """Return extracted page text, or "" on any failure or blocked URL (never raises)."""
    own = client is None
    client = client or httpx.AsyncClient(headers=HEADERS, timeout=request_timeout)
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_S):
            return await _fetch(client, url)
    except BlockedURL as e:
        log.warning("fetch blocked %s: %s", url, e)
        return ""
    except Exception as e:
        log.info("fetch failed %s: %s", url, e)
        return ""
    finally:
        if own:
            await client.aclose()
