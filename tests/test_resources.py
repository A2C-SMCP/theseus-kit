"""Tests for Issue #9 — window:// resource projection.

Validates the three ``window://`` resources via FastMCP's ``list_resources``
and ``read_resource``, backed by :class:`FakeRobotServer` for real TCP
round-trips.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from theseus_kit import set_last_locator
from theseus_kit.config import TheseusSettings
from theseus_kit.server import create_mcp_server

from ._fakeserver import FakeRobotServer, RobotResponse

_WINDOW_NS = "window://com.a2c-smcp.theseus-kit"
_SUMMARY_URI = f"{_WINDOW_NS}/config/summary"
_RECENT_URI = f"{_WINDOW_NS}/config/recent"
_TOPOLOGY_URI = f"{_WINDOW_NS}/config/topology"


# -- Helpers ---------------------------------------------------------------


@pytest.fixture
def fake() -> Iterator[FakeRobotServer]:
    with FakeRobotServer() as server:
        yield server


@pytest.fixture(autouse=True)
def _clear_last_locator() -> Iterator[None]:
    """Ensure module-level _last_locator is reset between tests."""
    set_last_locator(None)
    yield
    set_last_locator(None)


def _settings(fake: FakeRobotServer) -> TheseusSettings:
    """Build minimal settings pointing at *fake*."""
    return TheseusSettings(
        robot={
            "robot_id": "robot-1",
            "namespace": "default",
            "robot_type": "tfrobot",
            "api_base_url": fake.api_base_url,
            "manager_base_url": fake.manager_base_url,
        },
        credential={
            "kind": "user_pat",
            "pat": SecretStr("tfp_test_pat"),
            "robot_public_id": "turingfocus:000042",
        },
    )


def _tfs(data: object) -> bytes:
    """Encode a TFSResponse-style JSON body with *data*."""
    return json.dumps({"code": 200, "message": "Success", "data": data}, ensure_ascii=False).encode()


def _setup_summary_scenes(fake: FakeRobotServer) -> None:
    """Seed fake robot responses needed for a valid config/summary."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _tfs(["brain", "vision"]))
    fake.robot_responses["/v1/factory/online/scenes"] = RobotResponse(200, _tfs(["brain"]))
    fake.robot_responses["/v1/factory/templates/scenes"] = RobotResponse(200, _tfs(["brain"]))
    fake.robot_responses["/v1/factory/templates/query"] = RobotResponse(
        200,
        json.dumps({"code": 200, "message": "Success", "data": {"templates": [], "total": 15}}).encode(),
    )


# -- Resource listing ------------------------------------------------------


async def test_list_resources_includes_all_window_resources(
    fake: FakeRobotServer,
) -> None:
    """resources/list returns the three window:// resources."""
    _setup_summary_scenes(fake)
    mcp = create_mcp_server(_settings(fake))

    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}

    assert _SUMMARY_URI in uris
    assert _RECENT_URI in uris
    assert _TOPOLOGY_URI in uris


async def test_summary_resource_annotations(fake: FakeRobotServer) -> None:
    """config/summary has correct audience and priority annotations."""
    _setup_summary_scenes(fake)
    mcp = create_mcp_server(_settings(fake))

    resources = await mcp.list_resources()
    summary = next(r for r in resources if str(r.uri) == _SUMMARY_URI)

    assert summary.annotations is not None
    assert summary.annotations.audience == ["assistant"]
    assert summary.annotations.priority == 0.9
    assert summary.mimeType == "application/json"


async def test_recent_resource_annotations(fake: FakeRobotServer) -> None:
    """config/recent has correct audience and priority annotations."""
    _setup_summary_scenes(fake)
    mcp = create_mcp_server(_settings(fake))

    resources = await mcp.list_resources()
    recent = next(r for r in resources if str(r.uri) == _RECENT_URI)

    assert recent.annotations is not None
    assert recent.annotations.audience == ["assistant"]
    assert recent.annotations.priority == 0.8
    assert recent.mimeType == "application/json"


# -- Resource content ------------------------------------------------------


async def test_read_summary_success(fake: FakeRobotServer) -> None:
    """resources/read summary returns robot identity + three-state overview."""
    _setup_summary_scenes(fake)
    mcp = create_mcp_server(_settings(fake))

    result = await mcp.read_resource(_SUMMARY_URI)
    content = _read_text(result)
    body: dict[str, Any] = json.loads(content)

    assert body["robot_identity"]["robot_id"] == "robot-1"
    assert "draft" in body["states"]
    assert "online" in body["states"]
    assert "template" in body["states"]
    assert body["states"]["draft"]["present"] is True
    assert body["states"]["template"]["count"] == 15


async def test_read_summary_no_scenes(fake: FakeRobotServer) -> None:
    """All states absent → still returns valid structure (not an error)."""
    # No scenes registered → all /scenes endpoints return empty lists.
    for path in (
        "/v1/factory/drafts/scenes",
        "/v1/factory/online/scenes",
        "/v1/factory/templates/scenes",
    ):
        fake.robot_responses[path] = RobotResponse(200, _tfs([]))

    mcp = create_mcp_server(_settings(fake))

    result = await mcp.read_resource(_SUMMARY_URI)
    content = _read_text(result)
    body: dict[str, Any] = json.loads(content)

    assert body["robot_identity"]["robot_id"] == "robot-1"
    for state in ("draft", "online", "template"):
        assert body["states"][state]["present"] is False


async def test_read_recent_empty_state(fake: FakeRobotServer) -> None:
    """No detail opened yet → recent returns explicit empty-state sentinel."""
    _setup_summary_scenes(fake)
    mcp = create_mcp_server(_settings(fake))

    result = await mcp.read_resource(_RECENT_URI)
    content = _read_text(result)
    body: dict[str, Any] = json.loads(content)

    assert body["available"] is False
    assert "message" in body


async def test_read_recent_after_setting_locator(fake: FakeRobotServer) -> None:
    """After set_last_locator, recent returns the detail for that locator."""
    _setup_summary_scenes(fake)

    # Seed a draft detail response.
    draft_dto = {
        "setting_id": 42,
        "setting_name": "test-draft",
        "scene": "brain",
        "name": "brain",
        "config": {"key": "value"},
        "factory_version": "1.0",
    }
    fake.robot_responses["/v1/factory/drafts/42"] = RobotResponse(200, _tfs(draft_dto))

    set_last_locator("tcfg:draft/brain/brain/42")

    mcp = create_mcp_server(_settings(fake))

    result = await mcp.read_resource(_RECENT_URI)
    content = _read_text(result)
    body: dict[str, Any] = json.loads(content)

    assert body["locator"] == "tcfg:draft/brain/brain/42"
    assert body["state"] == "draft"
    assert body["truncated"] is False


async def test_read_recent_stale_locator_graceful_error(fake: FakeRobotServer) -> None:
    """When the stored locator no longer exists, recent returns a graceful error."""
    _setup_summary_scenes(fake)
    fake.robot_responses["/v1/factory/drafts/99"] = RobotResponse(404, b'{"code": 404, "message": "not found"}')
    set_last_locator("tcfg:draft/brain/brain/99")

    mcp = create_mcp_server(_settings(fake))
    result = await mcp.read_resource(_RECENT_URI)
    content = _read_text(result)
    body: dict[str, Any] = json.loads(content)

    assert body["available"] is False
    assert "Failed to read" in body["message"]


async def test_resource_uri_format() -> None:
    """All window:// URIs follow the no-query conformance rule."""
    from urllib.parse import urlparse

    for uri in (_SUMMARY_URI, _RECENT_URI, _TOPOLOGY_URI):
        parsed = urlparse(uri)
        assert parsed.query == "", f"{uri} must not contain query parameters"
        # window:// is parsed as scheme=window with netloc from //
        assert parsed.scheme == "window", f"{uri} must use window:// scheme"


async def test_last_locator_state_isolation() -> None:
    """set_last_locator correctly updates and clears module-level state."""
    from theseus_kit import resources as res

    assert res._last_locator is None
    set_last_locator("tcfg:draft/brain/brain/1")
    assert res._last_locator == "tcfg:draft/brain/brain/1"
    set_last_locator(None)
    assert res._last_locator is None


# -- Sensitive-field redaction ---------------------------------------------


async def test_summary_does_not_leak_secrets(fake: FakeRobotServer) -> None:
    """The summary resource payload never contains PAT / token / secret values."""
    _setup_summary_scenes(fake)
    mcp = create_mcp_server(_settings(fake))

    result = await mcp.read_resource(_SUMMARY_URI)
    content = _read_text(result)

    # The test credential's secret is "tfp_secret" — must not appear in output.
    assert "tfp_secret" not in content
    assert "tfp_" not in content
    # Generic secret patterns.
    for banned in ("Bearer ", "eyJ"):
        assert banned not in content, f"summary leaked: {banned!r}"


# -- helper ----------------------------------------------------------------


def _read_text(result: Any) -> str:
    """Extract text content from ``read_resource`` result.

    FastMCP returns an ``Iterable[ReadResourceContents]``.  Drain it.
    """
    items = list(result)
    assert len(items) == 1
    content: str | bytes = items[0].content
    if isinstance(content, bytes):
        return content.decode()
    return str(content)
