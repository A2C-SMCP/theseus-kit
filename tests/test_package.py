from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

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
    """main() runs with transport='stdio' (settings from THESEUS_* env).

    main() loads TheseusSettings() first (Issue #30) — env vars are set
    explicitly so the test never depends on a repo-local ``.env`` (absent in
    CI, present on developer machines).
    """
    transports: list[str] = []

    def fake_run(self: FastMCP, *, transport: str) -> None:
        transports.append(transport)

    monkeypatch.setattr(FastMCP, "run", fake_run)
    for name, value in {
        "THESEUS_ROBOT__ROBOT_ID": "test-robot",
        "THESEUS_ROBOT__NAMESPACE": "test-ns",
        "THESEUS_ROBOT__ROBOT_TYPE": "tfrobot",
        "THESEUS_ROBOT__API_BASE_URL": "https://localhost:1",
        "THESEUS_ROBOT__MANAGER_BASE_URL": "https://localhost:1",
        "THESEUS_CREDENTIAL__KIND": "user_pat",
        "THESEUS_CREDENTIAL__PAT": "tfp_test_pat",
        "THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID": "testorg:10001",
    }.items():
        monkeypatch.setenv(name, value)
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


def test_write_tfonto_skill_package_shape() -> None:
    """The write-tfonto skill is a real package folder (SKILL.md + references/ + scripts/)."""
    root = files("theseus_kit.skills").joinpath("write-tfonto")
    assert root.joinpath("SKILL.md").is_file()
    assert root.joinpath("references/teacher-math.tfo").is_file()
    assert root.joinpath("scripts/validate_tfonto.py").is_file()


def test_wheel_ships_write_tfonto_package(tmp_path: Path) -> None:
    """The built wheel contains every write-tfonto package file.

    Guards the packaging contract behind the folder-based skill: hatchling
    must keep shipping the data files (a silent drop would break the skill
    for installed deployments).
    """
    import subprocess
    import zipfile

    out = tmp_path / "dist"
    proc = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"uv build failed:\n{proc.stdout}\n{proc.stderr}"

    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())

    prefix = "theseus_kit/skills/write-tfonto/"
    expected = {
        f"{prefix}SKILL.md",
        f"{prefix}references/tfonto-format.md",
        f"{prefix}references/capability-layer.md",
        f"{prefix}references/teacher-math-example.md",
        f"{prefix}references/teacher-math.tfo",
        f"{prefix}scripts/validate_tfonto.py",
    }
    assert expected <= names, f"missing from wheel: {expected - names}"
