"""Tests for the production entry point (``python -m theseus_kit`` / ``theseus-kit``).

Issue #30: the console entry built ``create_mcp_server()`` WITHOUT settings,
serving zero tools and zero resources, while the settings-driven composition
root served the full surface.  These tests drive the REAL entry over a REAL
stdio subprocess — the same shape a PyPI-installed client exercises — and
assert parity with the composition root plus fail-fast behaviour on missing
configuration.

Subprocesses run with ``cwd`` pinned to a temp dir so the repo's real ``.env``
(staging config) can never leak into the entry's settings resolution.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import PaginatedRequestParams
from pydantic import SecretStr

from theseus_kit.config import TheseusSettings
from theseus_kit.server import create_mcp_server

_REPO_ROOT = Path(__file__).resolve().parents[1]

_KIT_ENV = {
    "THESEUS_ROBOT__ROBOT_ID": "entry-robot",
    "THESEUS_ROBOT__NAMESPACE": "entry-ns",
    "THESEUS_ROBOT__ROBOT_TYPE": "tfrobot",
    "THESEUS_ROBOT__API_BASE_URL": "https://localhost:1",
    "THESEUS_ROBOT__MANAGER_BASE_URL": "https://localhost:1",
    "THESEUS_CREDENTIAL__KIND": "user_pat",
    "THESEUS_CREDENTIAL__PAT": "tfp_entry_test_pat",
    "THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID": "entryorg:10001",
}


def _subprocess_env(**overrides: str) -> dict[str, str]:
    """Parent env minus any THESEUS_* (isolate from ambient config), plus overrides.

    Case-insensitive: pydantic-settings matches env names case-insensitively,
    so a lowercase ``theseus_*`` export in the parent shell must not leak in.
    """
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("THESEUS_")}
    env.update(overrides)
    return env


def _ci() -> bool:
    """GitHub Actions sets CI=true; treat "", "0", "false" as not-CI."""
    return os.environ.get("CI", "").lower() not in ("", "0", "false")


def _checked(cmd: list[str], *, cwd: Path) -> None:
    """Run a setup command; on failure surface its captured output in CI logs."""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"command failed ({cmd}):\n{proc.stdout}\n{proc.stderr}")


async def _composition_root_surface() -> tuple[set[str], set[str]]:
    """Tools + resource URIs served by the settings-driven composition root."""
    settings = TheseusSettings(
        robot={
            "robot_id": "entry-robot",
            "namespace": "entry-ns",
            "robot_type": "tfrobot",
            "api_base_url": "https://localhost:1",
            "manager_base_url": "https://localhost:1",
        },
        credential={
            "kind": "user_pat",
            "pat": SecretStr("tfp_entry_test_pat"),
            "robot_public_id": "entryorg:10001",
        },
    )
    mcp = create_mcp_server(settings)
    tools = {t.name for t in await mcp.list_tools()}
    uris = {str(r.uri) for r in await mcp.list_resources()}
    return tools, uris


async def _assert_served_surface(
    params: StdioServerParameters, expected_tools: set[str], expected_uris: set[str]
) -> None:
    """Drive *params* over real stdio and assert exact surface parity."""
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_ret = await session.list_tools()
            tool_names = {t.name for t in tools_ret.tools}

            uris: set[str] = set()
            cursor: str | None = None
            for _ in range(10):  # bounded walk guard (mirrors the e2e suite)
                page = await session.list_resources(params=PaginatedRequestParams(cursor=cursor))
                uris.update(str(r.uri) for r in page.resources)
                cursor = page.nextCursor
                if not cursor:
                    break

    # Non-empty guard first: exact-parity alone would pass vacuously when BOTH
    # sides degenerate to zero — the exact failure shape of Issue #30.
    assert expected_tools and tool_names, f"tools must not degenerate to empty; got {sorted(tool_names)}"
    assert tool_names == expected_tools, "entry tools must match the composition root surface"
    assert uris == expected_uris, "entry resources must match the composition root surface"


async def test_console_entry_serves_composition_root_surface(tmp_path: Path) -> None:
    """The console entry serves the same tools/resources as the composition root."""
    expected_tools, expected_uris = await _composition_root_surface()

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "theseus_kit"],
        env=_subprocess_env(**_KIT_ENV),
        cwd=str(tmp_path),
    )
    await _assert_served_surface(params, expected_tools, expected_uris)


def test_console_entry_without_config_fails_fast(tmp_path: Path) -> None:
    """Missing configuration aborts with an actionable error — never an empty server."""
    env = _subprocess_env()
    proc = subprocess.run(
        [sys.executable, "-m", "theseus_kit"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        input="",
        timeout=15,
    )
    assert proc.returncode != 0, f"entry must fail without config; stdout={proc.stdout!r}"
    assert "THESEUS_ROBOT__ROBOT_ID" in proc.stderr, f"stderr must name the missing env var: {proc.stderr!r}"
    # A missing discriminated union points at its discriminator — THESEUS_CREDENTIAL
    # alone is not a settable variable.
    assert "THESEUS_CREDENTIAL__KIND" in proc.stderr, f"stderr must hint the credential kind: {proc.stderr!r}"


def test_console_entry_config_error_redacts_secrets(tmp_path: Path) -> None:
    """Config errors that would echo a secret never leak it to the console.

    The URL validators embed the offending value in their message
    (``got {value!r}``) — a PAT-shaped ``api_base_url`` makes pydantic
    actually echo the secret, so this asserts the scrubber truly scrubs.
    """
    kit_env = dict(_KIT_ENV)
    kit_env["THESEUS_ROBOT__API_BASE_URL"] = "tfp_redact_me_entry"
    env = _subprocess_env(**kit_env)
    proc = subprocess.run(
        [sys.executable, "-m", "theseus_kit"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        input="",
        timeout=15,
    )
    assert proc.returncode != 0, "entry must fail on an invalid api_base_url"
    assert "tfp_redact_me_entry" not in proc.stderr + proc.stdout, "secret must never reach the console"
    assert "THESEUS_ROBOT__API_BASE_URL" in proc.stderr, f"error must stay actionable: {proc.stderr!r}"


def test_console_entry_config_error_keeps_long_env_hints(tmp_path: Path) -> None:
    """Env hints survive secret scrubbing even at 40+ chars of pure [A-Za-z0-9_-].

    Regression: ``THESEUS_CREDENTIAL__AUTHORIZATION_SERVER`` is exactly 40
    characters of the opaque-token pattern (redaction._OPAQUE_RE) — a
    whole-message scrub would replace the actionable hint with
    ``<<redacted>>``.
    """
    kit_env = dict(_KIT_ENV)
    kit_env["THESEUS_CREDENTIAL__KIND"] = "oauth"
    kit_env.pop("THESEUS_CREDENTIAL__PAT")
    kit_env.pop("THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID")
    env = _subprocess_env(**kit_env)
    proc = subprocess.run(
        [sys.executable, "-m", "theseus_kit"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        input="",
        timeout=15,
    )
    assert proc.returncode != 0, "entry must fail when authorization_server is missing"
    assert "THESEUS_CREDENTIAL__AUTHORIZATION_SERVER" in proc.stderr, (
        f"40-char env hint must survive scrubbing: {proc.stderr!r}"
    )


@pytest.mark.skipif(not _ci(), reason="wheel smoke runs in CI (CI=1 to force locally)")
async def test_wheel_install_console_script_serves_full_surface(tmp_path: Path) -> None:
    """The installed WHEEL's console script serves the full surface (PyPI path).

    Builds the wheel, installs it into a throwaway venv, and launches the
    real ``theseus-kit`` console script over stdio.  Only a wheel install
    exercises the delivery-path differences from a source checkout: pip's
    byte-compilation of ``.py`` files inside skill packages (the
    ``__pycache__`` walk collision) and the console-script entry wiring.
    ``--system-site-packages`` skips dependency downloads — the host env
    already carries them, so pip installs only the wheel itself.
    """
    build_dir = tmp_path / "dist"
    _checked(["uv", "build", "--out-dir", str(build_dir)], cwd=_REPO_ROOT)
    wheels = list(build_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"

    # Dot-prefixed on purpose: uv's default layout (`.venv`) puts a hidden
    # segment in the skill package's ancestors — a delivery-path shape the
    # content walk must not trip over.
    venv_dir = tmp_path / ".venv"
    _checked([sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)], cwd=tmp_path)
    _checked([str(venv_dir / "bin" / "pip"), "install", "--quiet", str(wheels[0])], cwd=tmp_path)
    console_script = venv_dir / "bin" / "theseus-kit"
    assert console_script.is_file(), "wheel must ship the theseus-kit console script"

    expected_tools, expected_uris = await _composition_root_surface()

    params = StdioServerParameters(
        command=str(console_script),
        args=[],
        env=_subprocess_env(**_KIT_ENV),
        cwd=str(tmp_path),
    )
    await _assert_served_surface(params, expected_tools, expected_uris)
