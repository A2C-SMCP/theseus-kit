from importlib.metadata import version

import pytest
from mcp.server.fastmcp import FastMCP

from theseus_kit import __version__
from theseus_kit.config import OAuthConfig, RobotTarget, TheseusSettings
from theseus_kit.server import _LazyOAuthTokenVerifier, create_mcp_server, main


def test_package_version_matches_distribution_metadata() -> None:
    assert __version__ == version("theseus-kit")


def test_create_mcp_server_default() -> None:
    """create_mcp_server() without settings returns a plain FastMCP instance."""
    mcp = create_mcp_server()
    assert mcp.name == "theseus-kit"


def test_main_uses_portable_stdio_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() runs with transport='stdio'."""
    transports: list[str] = []

    def fake_run(self: FastMCP, *, transport: str) -> None:
        transports.append(transport)

    monkeypatch.setattr(FastMCP, "run", fake_run)
    main()
    assert transports == ["stdio"]


def test_create_mcp_server_with_oauth() -> None:
    """create_mcp_server(settings) wires OAuth auth + token_verifier."""
    settings = TheseusSettings(
        robot=RobotTarget(
            robot_id="test-robot",
            namespace="test-ns",
            robot_type="tfrobot",
            api_base_url="https://api.example.com",
            manager_base_url="https://manager.example.com",
        ),
        credential=OAuthConfig(
            authorization_server="https://auth.example.com",
            scopes="config:read config:write",
            resource_server_url="https://theseus.example.com",
        ),
    )

    mcp = create_mcp_server(settings)

    assert mcp.name == "theseus-kit"
    assert mcp.settings.auth is not None
    assert str(mcp.settings.auth.issuer_url) == "https://auth.example.com/"
    assert mcp.settings.auth.required_scopes == ["config:read", "config:write"]
    # token_verifier is stored as a private attr on FastMCP (not on .settings).
    assert isinstance(mcp._token_verifier, _LazyOAuthTokenVerifier)
