from importlib.metadata import version

import pytest
from mcp.server.fastmcp import FastMCP

from theseus_kit import __version__
from theseus_kit.server import create_mcp_server, main


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
