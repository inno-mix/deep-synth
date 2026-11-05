"""Regression tests for the security review findings: SSRF, body size cap, rate-limit IP trust, error leakage."""

import httpx
import pytest
import respx
from starlette.requests import Request

from app.config import get_settings
from app.tools import fetch
from app.tools.fetch import BlockedURL, check_url, fetch_text

PUBLIC = "http://93.184.215.14"  # literal public IP: no DNS needed in tests


def _streamed(body: bytes, **headers: str) -> httpx.Response:
    """A response whose body is still an unread stream, like one coming off the network.

    (httpx.Response(content=...) eagerly reads and decodes the body, which would hide what we test.)
    """
    return httpx.Response(200, headers=headers, stream=httpx.ByteStream(body))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:11434/api/tags",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",  # CGNAT
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
        "http://0.0.0.0/",
        "file:///etc/passwd",
        "ftp://93.184.215.14/",
        "http://93.184.215.14:22/",
        "http://user:pw@93.184.215.14/",
    ],
)
async def test_check_url_blocks_internal_and_odd_targets(url):
    with pytest.raises(BlockedURL):
        await check_url(url)


async def test_check_url_allows_public_http():
    await check_url(f"{PUBLIC}/page")
    await check_url("https://93.184.215.14:8443/page")


@respx.mock
async def test_redirect_into_private_network_is_not_followed():
    respx.get(f"{PUBLIC}/a").mock(return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/secret"}))
    secret = respx.get("http://127.0.0.1/secret").mock(
        return_value=httpx.Response(200, text="<p>secret</p>", headers={"content-type": "text/html"})
    )
    assert await fetch_text(f"{PUBLIC}/a") == ""
    assert not secret.called


@respx.mock
async def test_public_redirect_is_followed():
    body = "<html><body><article>" + "<p>Kafka uses a log-structured design for durability.</p>" * 20
    respx.get(f"{PUBLIC}/a").mock(return_value=httpx.Response(301, headers={"location": "/b"}))
    respx.get(f"{PUBLIC}/b").mock(return_value=httpx.Response(200, text=body, headers={"content-type": "text/html"}))
    assert "log-structured" in await fetch_text(f"{PUBLIC}/a")


@respx.mock
async def test_declared_oversized_body_is_skipped_without_reading(monkeypatch):
    monkeypatch.setattr(fetch, "MAX_BYTES", 100)
    respx.get(f"{PUBLIC}/big").mock(
        return_value=httpx.Response(200, content=b"x" * 1000, headers={"content-type": "text/html"})
    )
    assert await fetch_text(f"{PUBLIC}/big") == ""


async def test_streamed_body_stops_at_cap(monkeypatch):
    monkeypatch.setattr(fetch, "MAX_BYTES", 100)
    pulled = 0

    async def endless():
        nonlocal pulled
        for _ in range(10_000):
            pulled += 1
            yield b"y" * 64

    r = httpx.Response(200, content=endless())  # chunked: no content-length to check
    body = await fetch._read_capped(r)
    assert body is not None and len(body) == 100
    assert pulled <= 3  # stopped early instead of draining 640 KB


def _req(headers: dict[str, str], host: str = "203.0.113.9") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (host, 1234),
        }
    )


def test_forwarded_headers_ignored_unless_trusted(monkeypatch):
    from app.main import client_ip

    spoof = {"x-forwarded-for": "1.2.3.4", "x-real-ip": "5.6.7.8"}
    monkeypatch.setattr(get_settings(), "trust_proxy_headers", False)
    assert client_ip(_req(spoof)) == "203.0.113.9"
    monkeypatch.setattr(get_settings(), "trust_proxy_headers", True)
    assert client_ip(_req(spoof)) == "5.6.7.8"
    assert client_ip(_req({"x-forwarded-for": "1.2.3.4, 10.0.0.1"})) == "1.2.3.4"


def test_rate_limit_tracking_is_bounded(monkeypatch):
    import app.main as m

    monkeypatch.setattr(m, "_MAX_TRACKED_IPS", 50)
    monkeypatch.setattr(get_settings(), "trust_proxy_headers", False)
    m._hits.clear()
    for i in range(500):
        m._rate_limit(_req({}, host=f"198.51.100.{i % 250}-{i}"))
    assert len(m._hits) <= 50
    m._hits.clear()


def test_public_error_never_leaks_internal_details():
    from app.llm import StructuredOutputError
    from app.main import public_error

    secret = "postgresql://admin:hunter2@10.0.0.5/db unreachable"
    for exc in (RuntimeError(secret), ValueError(secret), StructuredOutputError(secret)):
        msg = public_error(exc, "abc123")
        assert "hunter2" not in msg and "10.0.0.5" not in msg and "postgresql" not in msg
    assert "run abc123" in public_error(RuntimeError(secret), "abc123")
    assert "time" in public_error(TimeoutError(), "x")
    assert "model server" in public_error(httpx.ConnectError("refused 127.0.0.1:11434"), "x")


# ---------------------------------------------------------------- DNS rebinding: connect to the validated IP

PAGE_HTML = "<html><body><article>" + "<p>Redpanda stores data in a thread-per-core log.</p>" * 20


def _scripted_dns(monkeypatch, answers: list[list[str]]):
    """First lookup returns answers[0], later lookups answers[1], ... (the last repeats)."""
    calls: list[str] = []

    async def fake_resolve(host: str, port: int) -> list[str]:
        calls.append(host)
        return answers[min(len(calls) - 1, len(answers) - 1)]

    monkeypatch.setattr(fetch, "resolve", fake_resolve)
    return calls


@respx.mock
async def test_rebinding_second_dns_answer_is_never_used(monkeypatch):
    calls = _scripted_dns(monkeypatch, [["93.184.215.14"], ["127.0.0.1"]])
    route = respx.get("http://93.184.215.14/page").mock(
        return_value=httpx.Response(200, text=PAGE_HTML, headers={"content-type": "text/html"})
    )
    internal = respx.get("http://127.0.0.1/page").mock(return_value=httpx.Response(200, text="secret"))
    assert "thread-per-core" in await fetch_text("http://rebind.example/page")
    assert calls == ["rebind.example"]  # resolved exactly once
    assert route.called and not internal.called
    req = route.calls.last.request
    assert req.url.host == "93.184.215.14"
    assert req.headers["host"] == "rebind.example"  # virtual hosting still works


@respx.mock
async def test_https_keeps_original_hostname_for_sni_and_cert_check(monkeypatch):
    _scripted_dns(monkeypatch, [["93.184.215.14"]])
    route = respx.get("https://93.184.215.14/page").mock(
        return_value=httpx.Response(200, text=PAGE_HTML, headers={"content-type": "text/html"})
    )
    assert await fetch_text("https://secure.example/page")
    assert route.calls.last.request.extensions["sni_hostname"] == "secure.example"


@respx.mock
async def test_dns_answer_mixing_public_and_private_is_refused(monkeypatch):
    _scripted_dns(monkeypatch, [["93.184.215.14", "10.0.0.7"]])
    route = respx.get(host="93.184.215.14").mock(return_value=httpx.Response(200, text="x"))
    assert await fetch_text("http://mixed.example/") == ""
    assert not route.called


@respx.mock
async def test_falls_back_to_next_validated_ip_when_connect_fails(monkeypatch):
    _scripted_dns(monkeypatch, [["93.184.215.14", "93.184.216.34"]])
    respx.get("http://93.184.215.14/page").mock(side_effect=httpx.ConnectError("unreachable"))
    ok = respx.get("http://93.184.216.34/page").mock(
        return_value=httpx.Response(200, text=PAGE_HTML, headers={"content-type": "text/html"})
    )
    assert "thread-per-core" in await fetch_text("http://flaky.example/page")
    assert ok.called


@respx.mock
async def test_each_redirect_hop_is_resolved_and_pinned_separately(monkeypatch):
    calls = _scripted_dns(monkeypatch, [["93.184.215.14"], ["127.0.0.1"]])  # hop 2 hostname resolves internal
    respx.get("http://93.184.215.14/a").mock(
        return_value=httpx.Response(302, headers={"location": "http://evil.example/b"})
    )
    internal = respx.get("http://127.0.0.1/b").mock(return_value=httpx.Response(200, text="secret"))
    assert await fetch_text("http://start.example/a") == ""
    assert calls == ["start.example", "evil.example"] and not internal.called


# ---------------------------------------------------------------- compression amplification


@respx.mock
async def test_requests_ask_for_identity_encoding_and_close_connection():
    route = respx.get(f"{PUBLIC}/p").mock(
        return_value=httpx.Response(200, text=PAGE_HTML, headers={"content-type": "text/html"})
    )
    await fetch_text(f"{PUBLIC}/p")
    sent = route.calls.last.request.headers
    assert sent["accept-encoding"] == "identity" and sent["connection"] == "close"


async def test_gzip_bomb_is_cut_at_the_cap(monkeypatch):
    import gzip
    import time

    monkeypatch.setattr(fetch, "MAX_BYTES", 10_000)
    bomb = gzip.compress(b"A" * 200_000_000, compresslevel=9)  # ~200 KB on the wire, 200 MB inflated
    r = _streamed(bomb, **{"content-encoding": "gzip", "content-type": "text/html"})
    t0 = time.perf_counter()
    body = await fetch._read_capped(r)
    assert body is not None and len(body) == 10_000
    assert time.perf_counter() - t0 < 1.0  # never inflated the full 200 MB


async def test_deflate_is_bounded_too(monkeypatch):
    import zlib

    monkeypatch.setattr(fetch, "MAX_BYTES", 5_000)
    r = _streamed(zlib.compress(b"B" * 50_000_000), **{"content-encoding": "deflate"})
    body = await fetch._read_capped(r)
    assert body is not None and len(body) == 5_000


@pytest.mark.parametrize("encoding", ["br", "zstd", "compress", "gzip, br"])
async def test_unsupported_encodings_are_refused(encoding):
    r = _streamed(b"whatever", **{"content-encoding": encoding})
    assert await fetch._read_capped(r) is None


async def test_corrupt_gzip_is_refused_not_raised():
    r = _streamed(b"definitely not gzip", **{"content-encoding": "gzip"})
    assert await fetch._read_capped(r) is None


async def test_legit_gzip_page_is_decoded(monkeypatch):
    import gzip

    r = _streamed(gzip.compress(PAGE_HTML.encode()), **{"content-encoding": "gzip"})
    assert (await fetch._read_capped(r)) == PAGE_HTML.encode()


# ---------------------------------------------------------------- IP classification differentials


@pytest.mark.parametrize(
    "ip",
    [
        "64:ff9b::7f00:1",  # NAT64 wrapping 127.0.0.1
        "64:ff9b::a00:1",  # NAT64 wrapping 10.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 wrapping 169.254.169.254 (metadata)
        "64:ff9b:1::1",  # local-use NAT64
        "2002:7f00:1::",  # 6to4 wrapping 127.0.0.1
        "2002:a9fe:a9fe::1",  # 6to4 wrapping metadata
        "2001:0:4136:e378:8000:63bf:80ff:fffe",  # Teredo
        "::7f00:1",  # IPv4-compatible 127.0.0.1
        "::a00:1",  # IPv4-compatible 10.0.0.1
        "::ffff:10.0.0.1",  # IPv4-mapped
        "::ffff:169.254.169.254",
        "::",
        "fc00::1",
        "fe80::1%eth0",
        "ff02::1",
        "100.64.0.1",
        "0.0.0.0",
    ],
)
def test_non_public_forms_are_rejected(ip):
    assert not fetch._is_public(ip)


@pytest.mark.parametrize(
    "ip",
    [
        "93.184.215.14",
        "2606:2800:220:1:248:1893:25c8:1946",
        "64:ff9b::5db8:d70e",  # NAT64 wrapping a public IPv4: legitimate on IPv6-only networks
        "::ffff:93.184.215.14",
    ],
)
def test_public_addresses_are_allowed(ip):
    assert fetch._is_public(ip)


@pytest.mark.parametrize("url", ["http://2130706433/", "http://0x7f.0.0.1/", "http://017700000001/", "http://127.1/"])
async def test_numeric_host_encodings_of_loopback_are_blocked(url):
    with pytest.raises(BlockedURL):
        await check_url(url)
