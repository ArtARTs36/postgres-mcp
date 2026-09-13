import sys
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolate_transport_security(monkeypatch):
    """Keep env/global FastMCP transport settings from leaking between tests."""
    from postgres_mcp.server import MCP_ALLOWED_HOSTS_ENV
    from postgres_mcp.server import mcp

    monkeypatch.delenv(MCP_ALLOWED_HOSTS_ENV, raising=False)
    original_transport_security = mcp.settings.transport_security
    yield
    mcp.settings.transport_security = original_transport_security


def test_allowed_host_argument_parsing_single():
    """Test that one --allowed-host flag is parsed as a list."""
    from postgres_mcp.server import build_arg_parser
    from postgres_mcp.server import resolve_allowed_hosts

    args = build_arg_parser().parse_args(
        [
            "postgresql://user:password@localhost/db",
            "--allowed-host",
            "mcp.example.com",
        ]
    )

    assert resolve_allowed_hosts(args.allowed_host, {}) == ["mcp.example.com"]


def test_allowed_host_argument_parsing_multiple():
    """Test that repeated --allowed-host flags preserve exact patterns."""
    from postgres_mcp.server import build_arg_parser
    from postgres_mcp.server import resolve_allowed_hosts

    args = build_arg_parser().parse_args(
        [
            "postgresql://user:password@localhost/db",
            "--allowed-host",
            "mcp.example.com",
            "--allowed-host",
            "mcp.example.com:*",
        ]
    )

    assert resolve_allowed_hosts(args.allowed_host, {}) == ["mcp.example.com", "mcp.example.com:*"]


def test_mcp_allowed_hosts_env_comma_separated():
    """Test comma-separated MCP_ALLOWED_HOSTS parsing."""
    from postgres_mcp.server import MCP_ALLOWED_HOSTS_ENV
    from postgres_mcp.server import resolve_allowed_hosts

    environ = {MCP_ALLOWED_HOSTS_ENV: "mcp.example.com,mcp.example.com:*,postgres-mcp"}

    assert resolve_allowed_hosts(None, environ) == ["mcp.example.com", "mcp.example.com:*", "postgres-mcp"]


def test_mcp_allowed_hosts_env_trims_whitespace_and_ignores_empty_entries():
    """Test whitespace trimming and empty entry filtering for MCP_ALLOWED_HOSTS."""
    from postgres_mcp.server import MCP_ALLOWED_HOSTS_ENV
    from postgres_mcp.server import resolve_allowed_hosts

    environ = {MCP_ALLOWED_HOSTS_ENV: " mcp.example.com , , mcp.example.com:* ,, postgres-mcp "}

    assert resolve_allowed_hosts(None, environ) == ["mcp.example.com", "mcp.example.com:*", "postgres-mcp"]


def test_mcp_allowed_hosts_env_overrides_cli():
    """Test MCP_ALLOWED_HOSTS takes precedence over CLI configuration."""
    from postgres_mcp.server import MCP_ALLOWED_HOSTS_ENV
    from postgres_mcp.server import resolve_allowed_hosts

    environ = {MCP_ALLOWED_HOSTS_ENV: "env.example.com,env.example.com:*"}

    assert resolve_allowed_hosts(["cli.example.com"], environ) == ["env.example.com", "env.example.com:*"]


def test_no_allowed_hosts_preserves_fastmcp_defaults():
    """Test no custom config leaves the existing FastMCP transport security object alone."""
    from mcp.server.fastmcp import FastMCP

    from postgres_mcp.server import apply_transport_security_settings

    server = FastMCP("test")
    original_transport_security = server.settings.transport_security

    apply_transport_security_settings(server, "streamable-http", [])

    assert server.settings.transport_security is original_transport_security


def test_stdio_does_not_modify_transport_security():
    """Test stdio transport does not receive HTTP transport security settings."""
    from mcp.server.fastmcp import FastMCP

    from postgres_mcp.server import apply_transport_security_settings

    server = FastMCP("test")
    original_transport_security = server.settings.transport_security

    apply_transport_security_settings(server, "stdio", ["mcp.example.com"])

    assert server.settings.transport_security is original_transport_security


def test_streamable_http_asgi_validates_allowed_hosts():
    """Test real Streamable HTTP ASGI app Host validation behavior."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient

    server = FastMCP(
        "test",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["mcp.example.com", "mcp.example.com:*"],
        ),
    )
    app = server.streamable_http_app()

    with TestClient(app) as client:
        bare_host_response = client.request("PUT", "/mcp", headers={"host": "mcp.example.com"})
        port_host_response = client.request("PUT", "/mcp", headers={"host": "mcp.example.com:8443"})
        invalid_host_response = client.request("PUT", "/mcp", headers={"host": "evil.example.com"})

    assert bare_host_response.status_code != 421
    assert port_host_response.status_code != 421
    assert invalid_host_response.status_code == 421


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["stdio", "sse", "streamable-http"])
async def test_transport_argument_parsing(transport):
    """Test that all transport options are parsed correctly."""
    from postgres_mcp.server import main

    original_argv = sys.argv
    try:
        sys.argv = [
            "postgres_mcp",
            "postgresql://user:password@localhost/db",
            f"--transport={transport}",
        ]

        with (
            patch("postgres_mcp.server.db_connection.pool_connect", AsyncMock()),
            patch("postgres_mcp.server.mcp.run_stdio_async", AsyncMock()) as mock_stdio,
            patch("postgres_mcp.server.mcp.run_sse_async", AsyncMock()) as mock_sse,
            patch("postgres_mcp.server.mcp.run_streamable_http_async", AsyncMock()) as mock_http,
        ):
            await main()

            # Verify the correct transport method was called
            if transport == "stdio":
                mock_stdio.assert_called_once()
                mock_sse.assert_not_called()
                mock_http.assert_not_called()
            elif transport == "sse":
                mock_stdio.assert_not_called()
                mock_sse.assert_called_once()
                mock_http.assert_not_called()
            elif transport == "streamable-http":
                mock_stdio.assert_not_called()
                mock_sse.assert_not_called()
                mock_http.assert_called_once()
    finally:
        sys.argv = original_argv


@pytest.mark.asyncio
async def test_streamable_http_host_port_arguments():
    """Test that streamable-http host and port arguments are applied correctly."""
    from postgres_mcp.server import main
    from postgres_mcp.server import mcp

    original_argv = sys.argv
    try:
        sys.argv = [
            "postgres_mcp",
            "postgresql://user:password@localhost/db",
            "--transport=streamable-http",
            "--streamable-http-host=0.0.0.0",
            "--streamable-http-port=9000",
        ]

        with (
            patch("postgres_mcp.server.db_connection.pool_connect", AsyncMock()),
            patch("postgres_mcp.server.mcp.run_streamable_http_async", AsyncMock()),
        ):
            await main()

            # Verify the host and port were set correctly
            assert mcp.settings.host == "0.0.0.0"
            assert mcp.settings.port == 9000
    finally:
        sys.argv = original_argv


@pytest.mark.asyncio
async def test_sse_host_port_arguments():
    """Test that SSE host and port arguments are applied correctly."""
    from postgres_mcp.server import main
    from postgres_mcp.server import mcp

    original_argv = sys.argv
    try:
        sys.argv = [
            "postgres_mcp",
            "postgresql://user:password@localhost/db",
            "--transport=sse",
            "--sse-host=0.0.0.0",
            "--sse-port=8080",
        ]

        with (
            patch("postgres_mcp.server.db_connection.pool_connect", AsyncMock()),
            patch("postgres_mcp.server.mcp.run_sse_async", AsyncMock()),
        ):
            await main()

            # Verify the host and port were set correctly
            assert mcp.settings.host == "0.0.0.0"
            assert mcp.settings.port == 8080
    finally:
        sys.argv = original_argv


@pytest.mark.asyncio
async def test_default_transport_is_stdio():
    """Test that the default transport is stdio when not specified."""
    from postgres_mcp.server import main

    original_argv = sys.argv
    try:
        sys.argv = [
            "postgres_mcp",
            "postgresql://user:password@localhost/db",
        ]

        with (
            patch("postgres_mcp.server.db_connection.pool_connect", AsyncMock()),
            patch("postgres_mcp.server.mcp.run_stdio_async", AsyncMock()) as mock_stdio,
            patch("postgres_mcp.server.mcp.run_sse_async", AsyncMock()) as mock_sse,
            patch("postgres_mcp.server.mcp.run_streamable_http_async", AsyncMock()) as mock_http,
        ):
            await main()

            mock_stdio.assert_called_once()
            mock_sse.assert_not_called()
            mock_http.assert_not_called()
    finally:
        sys.argv = original_argv
