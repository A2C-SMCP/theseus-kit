"""Tests for Issue #37 — A2C-SMCP Desktop Resource protocol alignment.

Covers the ``config/topology`` window resource, the ``resources.subscribe``
capability declaration, and end-to-end ``notifications/resources/updated``
delivery over real MCP sessions (in-memory transport) backed by
:class:`FakeRobotServer` for real TCP robot round-trips.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any, cast

import pytest
from mcp import types
from mcp.client.session import MessageHandlerFnT
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.shared.session import RequestResponder
from mcp.types import ResourceUpdatedNotification, ServerNotification
from pydantic import AnyUrl, SecretStr

from theseus_kit import set_last_locator
from theseus_kit.config import TheseusSettings
from theseus_kit.server import create_mcp_server

from ._fakeserver import FakeRobotServer, RobotResponse

_WINDOW_NS = "window://com.a2c-smcp.theseus-kit"
_SUMMARY_URI = f"{_WINDOW_NS}/config/summary"
_RECENT_URI = f"{_WINDOW_NS}/config/recent"
_TOPOLOGY_URI = f"{_WINDOW_NS}/config/topology"

_LOCATOR = "tcfg:draft/brain/brain/42"


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


def _seed_draft_detail(fake: FakeRobotServer) -> None:
    """Seed the draft detail response that ``get_config_detail`` fetches."""
    fake.robot_responses["/v1/factory/drafts/42"] = RobotResponse(
        200,
        _tfs(
            {
                "setting_id": 42,
                "setting_name": "test-draft",
                "scene": "brain",
                "name": "brain",
                "config": {"key": "value"},
                "factory_version": "1.0",
            }
        ),
    )


async def _drain_notifications(updated: asyncio.Event) -> None:
    """Wait for at least one captured notification (bounded)."""
    await asyncio.wait_for(updated.wait(), timeout=2)


def _make_message_handler(captured: list[str], updated: asyncio.Event) -> MessageHandlerFnT:
    """Capture ``notifications/resources/updated`` URIs from a client session."""

    async def on_message(
        message: RequestResponder[types.ServerRequest, types.ClientResult] | ServerNotification | Exception,
    ) -> None:
        if isinstance(message, ServerNotification) and isinstance(message.root, ResourceUpdatedNotification):
            captured.append(str(message.root.params.uri))
            updated.set()

    return on_message


def _read_text(result: Any) -> str:
    """Extract text content from ``read_resource`` result."""
    items = list(result)
    assert len(items) == 1
    content: str | bytes = items[0].content
    if isinstance(content, bytes):
        return content.decode()
    return str(content)


# -- Topology window resource ----------------------------------------------


async def test_topology_resource_annotations(fake: FakeRobotServer) -> None:
    """config/topology has correct audience, priority, and mime annotations."""
    mcp = create_mcp_server(_settings(fake))

    resources = await mcp.list_resources()
    topology = next(r for r in resources if str(r.uri) == _TOPOLOGY_URI)

    assert topology.annotations is not None
    assert topology.annotations.audience == ["assistant"]
    assert topology.annotations.priority == 0.7
    assert topology.mimeType == "application/json"


async def test_read_topology_success(fake: FakeRobotServer) -> None:
    """resources/read topology returns roots/orphans/nodes JSON."""
    fake.robot_responses["/v1/factory/drafts/topology"] = RobotResponse(
        200,
        _tfs(
            {
                "roots": [1],
                "orphans": [1],
                "nodes": {"1": {"s": "brain", "f": "EmployeeDraftSetting", "n": "main", "c": []}},
            }
        ),
    )
    mcp = create_mcp_server(_settings(fake))

    result = await mcp.read_resource(_TOPOLOGY_URI)
    body: dict[str, Any] = json.loads(_read_text(result))

    assert body["roots"] == [1]
    assert body["orphans"] == [1]
    assert body["nodes"]["1"]["n"] == "main"


async def test_read_topology_failure_sentinel(fake: FakeRobotServer) -> None:
    """Topology endpoint failure → available:false sentinel, never an error."""
    fake.robot_responses["/v1/factory/drafts/topology"] = RobotResponse(500, b'{"code": 500, "message": "boom"}')
    mcp = create_mcp_server(_settings(fake))

    result = await mcp.read_resource(_TOPOLOGY_URI)
    body: dict[str, Any] = json.loads(_read_text(result))

    assert body["available"] is False
    assert "message" in body


# -- resources.subscribe capability -----------------------------------------


async def test_initialization_options_declare_subscribe(fake: FakeRobotServer) -> None:
    """Initialization options advertise resources.subscribe=True, listChanged=False."""
    mcp = create_mcp_server(_settings(fake))

    opts = mcp._mcp_server.create_initialization_options()

    assert opts.capabilities.resources is not None
    assert opts.capabilities.resources.subscribe is True
    assert opts.capabilities.resources.listChanged is False


async def test_initialize_handshake_reports_subscribe(fake: FakeRobotServer) -> None:
    """A real client handshake sees resources.subscribe=True."""
    _seed_draft_detail(fake)
    mcp = create_mcp_server(_settings(fake))

    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.initialize()
        assert result.capabilities.resources is not None
        assert result.capabilities.resources.subscribe is True


# -- notifications/resources/updated delivery --------------------------------


async def test_subscriber_receives_notification_on_tool_change(fake: FakeRobotServer) -> None:
    """A subscribed session receives resources/updated when another actor
    opens a config detail (subscriber != tool caller)."""
    _seed_draft_detail(fake)
    mcp = create_mcp_server(_settings(fake))

    captured: list[str] = []
    updated = asyncio.Event()

    async with create_connected_server_and_client_session(
        mcp,
        message_handler=_make_message_handler(captured, updated),
    ) as subscriber:
        await subscriber.subscribe_resource(AnyUrl(_RECENT_URI))

        # A different actor (session-less direct tool call) changes _last_locator.
        await mcp.call_tool("get_config_detail", {"locator": _LOCATOR})

        await _drain_notifications(updated)

    assert captured == [_RECENT_URI]


async def test_calling_session_receives_notification_without_subscribe(
    fake: FakeRobotServer,
) -> None:
    """The tool-calling session is notified even when it never subscribed."""
    _seed_draft_detail(fake)
    mcp = create_mcp_server(_settings(fake))

    captured: list[str] = []
    updated = asyncio.Event()

    async with create_connected_server_and_client_session(
        mcp,
        message_handler=_make_message_handler(captured, updated),
    ) as client:
        await client.call_tool("get_config_detail", {"locator": _LOCATOR})

        await _drain_notifications(updated)

    assert captured == [_RECENT_URI]


async def test_subscribing_caller_gets_exactly_one_notification(fake: FakeRobotServer) -> None:
    """Subscriber == caller → deduped to a single notification."""
    _seed_draft_detail(fake)
    mcp = create_mcp_server(_settings(fake))

    captured: list[str] = []
    updated = asyncio.Event()

    async with create_connected_server_and_client_session(
        mcp,
        message_handler=_make_message_handler(captured, updated),
    ) as client:
        await client.subscribe_resource(AnyUrl(_RECENT_URI))
        await client.call_tool("get_config_detail", {"locator": _LOCATOR})

        await _drain_notifications(updated)

    assert captured == [_RECENT_URI]


async def test_create_draft_notifies_summary_and_topology(fake: FakeRobotServer) -> None:
    """A mutation tool fires the mapped notifications: create_draft →
    summary + topology."""
    fake.robot_responses[("POST", "/v1/factory/drafts")] = RobotResponse(
        200,
        _tfs(
            {
                "settingId": 42,
                "settingName": "my-llm",
                "scene": "LLM",
                "factoryName": "GLM草稿",
                "config": {"model": "glm-4"},
                "factoryVersion": "1.0",
            }
        ),
    )
    mcp = create_mcp_server(_settings(fake))

    captured: list[str] = []
    updated = asyncio.Event()

    async with create_connected_server_and_client_session(
        mcp,
        message_handler=_make_message_handler(captured, updated),
    ) as client:
        await client.subscribe_resource(AnyUrl(_SUMMARY_URI))
        await client.subscribe_resource(AnyUrl(_TOPOLOGY_URI))

        await client.call_tool(
            "create_draft",
            {"scene": "LLM", "factory_name": "GLM草稿", "setting_name": "my-llm"},
        )

        # Both notifications are written before call_tool resolves; the
        # client-side pump delivers them asynchronously — poll with a budget.
        for _ in range(200):
            if len(captured) >= 2:
                break
            await asyncio.sleep(0.01)

    assert captured == [_SUMMARY_URI, _TOPOLOGY_URI]


async def test_delete_draft_notifies_summary_topology_recent(fake: FakeRobotServer) -> None:
    """A mutation tool fires the mapped notifications: delete_draft →
    summary + topology + recent."""
    fake.robot_responses[("DELETE", "/v1/factory/drafts/42")] = RobotResponse(200, _tfs({}))
    mcp = create_mcp_server(_settings(fake))

    captured: list[str] = []
    updated = asyncio.Event()

    async with create_connected_server_and_client_session(
        mcp,
        message_handler=_make_message_handler(captured, updated),
    ) as client:
        await client.subscribe_resource(AnyUrl(_SUMMARY_URI))
        await client.subscribe_resource(AnyUrl(_TOPOLOGY_URI))
        await client.subscribe_resource(AnyUrl(_RECENT_URI))

        await client.call_tool("delete_draft", {"setting_id": 42})

        # All notifications are written before call_tool resolves; the
        # client-side pump delivers them asynchronously — poll with a budget.
        for _ in range(200):
            if len(captured) >= 3:
                break
            await asyncio.sleep(0.01)

    assert captured == [_SUMMARY_URI, _TOPOLOGY_URI, _RECENT_URI]


async def test_unsubscribe_stops_delivery(fake: FakeRobotServer) -> None:
    """After unsubscribe, a session-less tool call notifies nobody."""
    _seed_draft_detail(fake)
    mcp = create_mcp_server(_settings(fake))

    captured: list[str] = []
    updated = asyncio.Event()

    async with create_connected_server_and_client_session(
        mcp,
        message_handler=_make_message_handler(captured, updated),
    ) as client:
        await client.subscribe_resource(AnyUrl(_RECENT_URI))
        await client.unsubscribe_resource(AnyUrl(_RECENT_URI))

        await mcp.call_tool("get_config_detail", {"locator": _LOCATOR})

        # Drain grace: everything the tool call wrote is already in the
        # client stream when call_tool resolves; this only lets the pump run.
        await asyncio.sleep(0.1)

    assert captured == []
    assert mcp.subscription_registry.sessions_for(_RECENT_URI) == []


async def test_registry_roundtrip_via_wire(fake: FakeRobotServer) -> None:
    """Subscribe/unsubscribe over the wire updates the server-side registry."""
    _seed_draft_detail(fake)
    mcp = create_mcp_server(_settings(fake))

    async with create_connected_server_and_client_session(mcp) as client:
        await client.subscribe_resource(AnyUrl(_SUMMARY_URI))
        assert len(mcp.subscription_registry.sessions_for(_SUMMARY_URI)) == 1

        await client.unsubscribe_resource(AnyUrl(_SUMMARY_URI))
        assert mcp.subscription_registry.sessions_for(_SUMMARY_URI) == []


# -- Session-less direct call -----------------------------------------------


async def test_direct_call_without_session_does_not_crash(fake: FakeRobotServer) -> None:
    """Direct FastMCP tool call (no MCP session) still works after _notify."""
    _seed_draft_detail(fake)
    mcp = create_mcp_server(_settings(fake))

    result = await mcp.call_tool("get_config_detail", {"locator": _LOCATOR})

    # FastMCP's direct call_tool returns (content blocks, converted output).
    _, output = cast(tuple[Any, dict[str, Any]], result)
    assert output["locator"] == _LOCATOR
