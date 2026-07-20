from importlib.metadata import version

from theseus_kit import __version__
from theseus_kit.server import main, mcp


def test_package_version_matches_distribution_metadata() -> None:
    assert __version__ == version("theseus-kit")


def test_mcp_server_is_constructed() -> None:
    assert mcp.name == "theseus-kit"


def test_main_uses_portable_stdio_transport(monkeypatch) -> None:
    transports: list[str] = []

    def fake_run(*, transport: str) -> None:
        transports.append(transport)

    monkeypatch.setattr(mcp, "run", fake_run)
    main()
    assert transports == ["stdio"]
