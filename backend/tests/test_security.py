"""Regression tests for the security review findings: SSRF, body size cap, rate-limit IP trust, error leakage."""

import httpx
import pytest
import respx
from starlette.requests import Request

from app.config import get_settings
from app.tools import fetch
from app.tools.fetch import BlockedURL, check_url, fetch_text

PUBLIC = "http://93.184.215.14"  # literal public IP: no DNS needed in tests


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
