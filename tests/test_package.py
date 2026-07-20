from theseus_kit import __version__
from theseus_kit.server import mcp


def test_package_version_matches_initial_milestone() -> None:
    assert __version__ == "0.1.0.dev0"


def test_mcp_server_is_constructed() -> None:
    assert mcp.name == "theseus-kit"
