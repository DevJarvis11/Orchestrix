"""mcp-web: search, fetch and the SSRF guard (SPEC 12.3, 15.2)."""

from pathlib import Path

import httpx
import pytest
import respx
from pydantic import SecretStr
from starlette.testclient import TestClient

from orchestrix_mcp_web.server import WebSettings, build_server
from orchestrix_mcp_web.ssrf import BlockedURL, check_ip, check_url
from orchestrix_tools.conventions import build_app
from tests.unit.mcp.conftest import call, error_of

PAGE = (
    "<html><head><title>Vector DBs</title></head><body><article><p>"
    + "Qdrant is Apache-2.0 licensed. " * 20
    + "</p></article></body></html>"
)
REPO_FIXTURES = Path(__file__).parents[2] / "fixtures"


def settings(**kw: object) -> WebSettings:
    return WebSettings(mcp_service_token=SecretStr("t" * 32), **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "::ffff:10.0.0.1",
        "224.0.0.1",
    ],
)
def test_ssrf_blocks_non_public_addresses(ip: str) -> None:
    with pytest.raises(BlockedURL):
        check_ip(ip)


def test_ssrf_allows_public_addresses() -> None:
    check_ip("93.184.216.34")
    check_ip("2606:4700:4700::1111")


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://x.org/a", "http://user:pw@example.com/", "http:///nohost"]
)
async def test_ssrf_rejects_bad_urls(url: str, fake_dns: dict[str, str]) -> None:
    with pytest.raises(BlockedURL):
        await check_url(url)


async def test_ssrf_resolves_dns_before_deciding(fake_dns: dict[str, str]) -> None:
    fake_dns["looks-public.example"] = "10.0.0.7"  # DNS points inside the network
    with pytest.raises(BlockedURL, match="not public"):
        await check_url("https://looks-public.example/")


@respx.mock
async def test_fetch_extracts_main_text(fake_dns: dict[str, str]) -> None:
    fake_dns["docs.example"] = "93.184.216.34"
    route = respx.get("https://93.184.216.34/vdb").respond(200, html=PAGE)
    result = await call(
        build_server(settings()), "fetch", {"url": "https://docs.example/vdb", "max_chars": 200}
    )
    assert not result.isError
    data = result.structuredContent or {}
    assert data["title"] == "Vector DBs"
    assert "Apache-2.0" in data["text"]
    assert len(data["text"]) <= 200
    sent = route.calls.last.request
    assert sent.headers["host"] == "docs.example"  # pinned IP, real host name
    assert sent.extensions["sni_hostname"] == "docs.example"  # TLS verifies the real host


@respx.mock
async def test_fetch_rechecks_every_redirect_hop(fake_dns: dict[str, str]) -> None:
    fake_dns["public.example"] = "93.184.216.34"
    fake_dns["internal.example"] = "10.0.0.5"
    respx.get("https://93.184.216.34/go").respond(
        302, headers={"Location": "http://internal.example/admin"}
    )
    result = await call(build_server(settings()), "fetch", {"url": "https://public.example/go"})
    err = error_of(result)
    assert err["error_code"] == "blocked_url"
    assert err["retryable"] is False


@respx.mock
async def test_fetch_follows_at_most_three_redirects(fake_dns: dict[str, str]) -> None:
    fake_dns["loop.example"] = "93.184.216.34"
    for i in range(5):
        respx.get(f"https://93.184.216.34/{i}").respond(302, headers={"Location": f"/{i + 1}"})
    err = error_of(await call(build_server(settings()), "fetch", {"url": "https://loop.example/0"}))
    assert err["error_code"] == "too_many_redirects"


@respx.mock
async def test_search_uses_searxng_and_caps_results() -> None:
    results = [
        {"title": f"t{i}", "url": f"https://r{i}.example", "content": "snip"} for i in range(10)
    ]
    respx.get("http://searxng:8080/search").respond(200, json={"results": results})
    result = await call(
        build_server(settings()), "search", {"query": "vector databases", "max_results": 3}
    )
    assert [r["url"] for r in (result.structuredContent or {})["results"]] == [
        "https://r0.example",
        "https://r1.example",
        "https://r2.example",
    ]


@respx.mock
async def test_search_backend_failure_is_retryable() -> None:
    respx.get("http://searxng:8080/search").mock(side_effect=httpx.ConnectError("down"))
    err = error_of(await call(build_server(settings()), "search", {"query": "x"}))
    assert (err["error_code"], err["retryable"]) == ("search_unavailable", True)


@respx.mock
async def test_search_records_then_replays_fixtures(tmp_path) -> None:
    route = respx.get("http://searxng:8080/search").respond(
        200, json={"results": [{"title": "a", "url": "https://a.example", "content": "s"}]}
    )
    await call(
        build_server(settings(web_fixtures_dir=tmp_path, web_record_fixtures=True)),
        "search",
        {"query": "Q"},
    )
    replay = await call(
        build_server(settings(web_fixtures_dir=tmp_path)), "search", {"query": "q "}
    )
    assert route.call_count == 1  # the replay came from the fixture
    assert (replay.structuredContent or {})["results"][0]["url"] == "https://a.example"


async def test_search_replays_a_recorded_repository_fixture() -> None:
    result = await call(
        build_server(settings(web_fixtures_dir=REPO_FIXTURES / "web")),
        "search",
        {"query": "fixture vector databases", "max_results": 3},
    )
    assert (result.structuredContent or {})["results"] == [
        {
            "title": "Vector database fixture source",
            "url": "https://example.test/vector-database",
            "snippet": "A deterministic recorded fixture for MCP web-search tests.",
        }
    ]


def test_service_token_required_except_healthz() -> None:
    app = build_app(build_server(settings()), "t" * 32)
    with TestClient(app) as client:  # type: ignore[arg-type]
        assert client.get("/healthz").status_code == 200
        assert client.post("/mcp", json={}).status_code == 401
        assert (
            client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )


async def test_validation_errors_are_structured() -> None:
    result = await call(build_server(settings()), "search", {"query": "x", "max_results": 9})
    error = error_of(result)
    assert error["error_code"] == "invalid_input"
    assert error["retryable"] is False
    assert "validation error" in error["message"]


@respx.mock
async def test_fetch_connects_to_the_checked_ip_so_dns_rebinding_cannot_redirect_it(
    fake_dns: dict[str, str],
) -> None:
    """The address that passed the check is the one connected to; a second, rebinding DNS
    answer is never consulted (only the pinned IP has a route)."""
    fake_dns["rebind.example"] = "93.184.216.34"
    respx.get("https://93.184.216.34/").respond(200, html=PAGE)
    result = await call(build_server(settings()), "fetch", {"url": "https://rebind.example/"})
    assert not result.isError


@respx.mock
async def test_fetch_reads_gzip_encoded_pages(fake_dns: dict[str, str]) -> None:
    """Most sites send gzip: the body must be decoded exactly once."""
    import gzip

    fake_dns["gz.example"] = "93.184.216.34"
    respx.get("https://93.184.216.34/").respond(
        200,
        content=gzip.compress(PAGE.encode()),
        headers={"Content-Encoding": "gzip", "Content-Type": "text/html; charset=utf-8"},
    )
    result = await call(build_server(settings()), "fetch", {"url": "https://gz.example/"})
    assert not result.isError, result.content
    assert "Apache-2.0" in (result.structuredContent or {})["text"]
