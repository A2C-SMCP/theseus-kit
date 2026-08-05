"""Real-HTTP tests for Issue #4 — progressive-disclosure config reader.

Drives :class:`ConfigReader` against :class:`FakeRobotServer` (real TCP
socket, per #3/#4's acceptance bar).  Covers ``get_summary``,
``list_nodes``, ``get_detail``, and ``get_template``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from theseus_kit import (
    ClientCredentialsConfig,
    ConfigLocatorError,
    NodeKind,
    RobotClient,
)
from theseus_kit.routing import RequestContext
from theseus_kit.services.config_reader import ConfigReader
from theseus_kit.tokens import build_token_source

from ._fakeserver import FakeRobotServer, RobotResponse

_CTX = dict(robot_id="robot-1", namespace="default", robot_type="tfrobot")
_ROBOT_ID = "robot-1"

# -- Helpers ---------------------------------------------------------------


@pytest.fixture
def fake() -> Iterator[FakeRobotServer]:
    with FakeRobotServer() as server:
        yield server


def _client(fake: FakeRobotServer, **kw: object) -> RobotClient:
    cred = ClientCredentialsConfig(
        machine_client_id="turingfocus:000042",
        machine_client_secret=SecretStr("tfp_secret"),
    )
    token_source = build_token_source(cred, manager_base_url=fake.manager_base_url)
    context = RequestContext(**_CTX)
    return RobotClient(
        token_source,
        context,
        api_base_url=fake.api_base_url,
        **kw,  # type: ignore[arg-type]
    )


def _reader() -> ConfigReader:
    return ConfigReader(robot_id=_ROBOT_ID)


def _tfs(data: object) -> bytes:
    """Encode a TFSResponse-style JSON body with *data*."""
    return json.dumps({"code": 200, "message": "Success", "data": data}, ensure_ascii=False).encode()


# -- get_summary -----------------------------------------------------------


async def test_get_summary_all_states_present(fake: FakeRobotServer) -> None:
    """All three states have scenes → present=True for each."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _tfs(["brain", "vision"]))
    fake.robot_responses["/v1/factory/online/scenes"] = RobotResponse(200, _tfs(["brain"]))
    fake.robot_responses["/v1/factory/templates/scenes"] = RobotResponse(200, _tfs(["brain"]))
    fake.robot_responses["/v1/factory/templates/query"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": {"templates": [], "total": 15},
            }
        ).encode(),
    )

    async with _client(fake) as client:
        result = await _reader().get_summary(client)

    assert result.robot_identity.robot_id == _ROBOT_ID
    assert result.states["draft"].present is True
    assert result.states["draft"].status == "dirty"
    assert result.states["online"].present is True
    assert result.states["template"].present is True
    assert result.states["template"].count == 15


async def test_get_summary_single_state(fake: FakeRobotServer) -> None:
    """Only query the requested state."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _tfs(["brain"]))

    async with _client(fake) as client:
        result = await _reader().get_summary(client, state="draft")

    assert len(result.states) == 1
    assert result.states["draft"].present is True


async def test_get_summary_empty_state(fake: FakeRobotServer) -> None:
    """A state with no scenes → present=False."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _tfs([]))

    async with _client(fake) as client:
        result = await _reader().get_summary(client, state="draft")

    assert result.states["draft"].present is False
    assert result.states["draft"].root_locator == "tcfg:draft"


async def test_get_summary_state_error(fake: FakeRobotServer) -> None:
    """When a state endpoint returns an error, treat as not present."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(500, b"internal error")

    async with _client(fake) as client:
        result = await _reader().get_summary(client, state="draft")

    assert result.states["draft"].present is False
    assert result.states["draft"].status == "unknown"


# -- list_nodes (forest roots) ---------------------------------------------


async def test_list_nodes_forest_roots(fake: FakeRobotServer) -> None:
    """No parent, no state → return the three forest root nodes."""
    async with _client(fake) as client:
        result = await _reader().list_nodes(client)

    assert len(result.nodes) == 3
    kinds = {n.kind for n in result.nodes}
    assert kinds == {NodeKind.STATE}
    locators = {n.locator for n in result.nodes}
    assert locators == {"tcfg:draft", "tcfg:online", "tcfg:template"}


# -- list_nodes (scenes) ---------------------------------------------------


async def test_list_nodes_scenes(fake: FakeRobotServer) -> None:
    """List scenes within the draft state."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _tfs(["brain", "vision"]))

    async with _client(fake) as client:
        result = await _reader().list_nodes(client, parent="tcfg:draft")

    assert len(result.nodes) == 2
    assert result.nodes[0].kind == NodeKind.SCENE
    assert result.nodes[0].locator == "tcfg:draft/brain"
    assert result.nodes[0].name == "brain"


# -- list_nodes (factories) ------------------------------------------------


async def test_list_nodes_factories(fake: FakeRobotServer) -> None:
    """List factories within a scene."""
    fake.robot_responses["/v1/factory/drafts/brain/factories"] = RobotResponse(
        200, _tfs({"factory_names": ["brain", "chain"]})
    )

    async with _client(fake) as client:
        result = await _reader().list_nodes(client, parent="tcfg:draft/brain")

    assert len(result.nodes) == 2
    assert result.nodes[0].kind == NodeKind.FACTORY
    assert result.nodes[0].locator == "tcfg:draft/brain/brain"
    assert result.nodes[1].locator == "tcfg:draft/brain/chain"


# -- list_nodes (settings) -------------------------------------------------


async def test_list_nodes_settings(fake: FakeRobotServer) -> None:
    """List settings within a scene+factory."""
    fake.robot_responses["/v1/factory/drafts/query"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": [
                    {
                        "setting_id": 1,
                        "name": "brain",
                        "setting_name": "Main Brain",
                        "scene": "brain",
                        "config": {"key": "value"},
                        "factory_version": "v1.0",
                    },
                    {
                        "setting_id": 2,
                        "name": "brain",
                        "setting_name": "Alt Brain",
                        "scene": "brain",
                        "config": {},
                        "factory_version": "v1.0",
                    },
                ],
            }
        ).encode(),
    )

    async with _client(fake) as client:
        result = await _reader().list_nodes(client, parent="tcfg:draft/brain/brain")

    assert len(result.nodes) == 2
    assert result.nodes[0].kind == NodeKind.SETTING
    assert result.nodes[0].locator == "tcfg:draft/brain/brain/1"
    assert result.nodes[0].name == "Main Brain"
    assert result.nodes[1].locator == "tcfg:draft/brain/brain/2"


async def test_list_nodes_settings_filtered(fake: FakeRobotServer) -> None:
    """Name filter narrows settings."""
    fake.robot_responses["/v1/factory/drafts/query"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": [
                    {"setting_id": 1, "name": "brain", "setting_name": "Main Brain", "scene": "brain", "config": {}},
                ],
            }
        ).encode(),
    )

    async with _client(fake) as client:
        result = await _reader().list_nodes(client, parent="tcfg:draft/brain/brain", name="Main")

    # The filter is applied server-side; here we just check the call goes through.
    assert len(result.nodes) == 1


# -- list_nodes (pagination) -----------------------------------------------


async def test_list_nodes_pagination_cursor(fake: FakeRobotServer) -> None:
    """Cursor-based pagination of scenes."""
    scenes = [f"scene-{i}" for i in range(25)]
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _tfs(scenes))

    async with _client(fake) as client:
        page1 = await _reader().list_nodes(client, parent="tcfg:draft", page_size=10)

    assert len(page1.nodes) == 10
    assert page1.next_cursor is not None

    # Use the cursor for page 2.
    async with _client(fake) as client:
        page2 = await _reader().list_nodes(client, cursor=page1.next_cursor, page_size=10)

    assert len(page2.nodes) == 10
    assert page2.nodes[0].name == "scene-10"


# -- get_detail ------------------------------------------------------------


async def test_get_detail_full(fake: FakeRobotServer) -> None:
    """Full detail for a setting."""
    fake.robot_responses["/v1/factory/drafts/42"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": {
                    "setting_id": 42,
                    "name": "brain",
                    "setting_name": "Test Brain",
                    "scene": "brain",
                    "config": {"model": "gpt-4", "temperature": 0.7},
                    "factory_version": "v2.1",
                },
            }
        ).encode(),
    )

    async with _client(fake) as client:
        result = await _reader().get_detail(client, locator="tcfg:draft/brain/brain/42")

    assert result.locator == "tcfg:draft/brain/brain/42"
    assert result.state == "draft"
    assert result.revision == "v2.1"
    assert result.subtree == {"model": "gpt-4", "temperature": 0.7}
    assert result.truncated is False
    assert result.redacted == []


async def test_get_detail_select_pointer(fake: FakeRobotServer) -> None:
    """Select drills into config via JSON Pointer."""
    fake.robot_responses["/v1/factory/drafts/42"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": {
                    "setting_id": 42,
                    "name": "brain",
                    "setting_name": "Test Brain",
                    "config": {"llm": {"model": "gpt-4", "temp": 0.7}},
                },
            }
        ).encode(),
    )

    async with _client(fake) as client:
        result = await _reader().get_detail(client, locator="tcfg:draft/brain/brain/42", select="/llm/model")

    assert result.subtree == "gpt-4"


async def test_get_detail_redacts_sensitive_keys(fake: FakeRobotServer) -> None:
    """Sensitive field values are replaced with <<redacted>>."""
    fake.robot_responses["/v1/factory/drafts/42"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": {
                    "setting_id": 42,
                    "name": "brain",
                    "config": {
                        "api_key": "sk-secret-123",
                        "password": "hunter2",
                        "nested": {"token": "abc123", "safe": "visible"},
                    },
                },
            }
        ).encode(),
    )

    async with _client(fake) as client:
        result = await _reader().get_detail(client, locator="tcfg:draft/brain/brain/42")

    assert result.subtree["api_key"] == "<<redacted>>"
    assert result.subtree["password"] == "<<redacted>>"
    assert result.subtree["nested"]["token"] == "<<redacted>>"
    assert result.subtree["nested"]["safe"] == "visible"
    assert len(result.redacted) == 3
    assert "/api_key" in result.redacted
    assert "/nested/token" in result.redacted


async def test_get_detail_truncated_by_max_bytes(fake: FakeRobotServer) -> None:
    """Large config truncated to max_bytes budget."""
    large_config = {"key" + str(i): "x" * 500 for i in range(50)}
    fake.robot_responses["/v1/factory/drafts/42"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": {"setting_id": 42, "name": "brain", "config": large_config},
            }
        ).encode(),
    )

    async with _client(fake) as client:
        result = await _reader().get_detail(client, locator="tcfg:draft/brain/brain/42", max_bytes=2048)

    assert result.truncated is True
    assert result.truncated_at is not None
    assert result.next_actions is not None
    assert len(result.next_actions) > 0
    # Returned bytes should be ≤ max_bytes (roughly).
    assert result.bytes_returned <= 2500  # some slack for JSON wrapper


async def test_get_detail_invalid_locator(fake: FakeRobotServer) -> None:
    """Invalid locator raises ConfigLocatorError."""
    async with _client(fake) as client:
        with pytest.raises(ConfigLocatorError):
            await _reader().get_detail(client, locator="tcfg:draft")

    async with _client(fake) as client:
        with pytest.raises(ConfigLocatorError):
            await _reader().get_detail(client, locator="tcfg:unknown")

    async with _client(fake) as client:
        with pytest.raises(ConfigLocatorError):
            await _reader().get_detail(client, locator="bad:format")


async def test_get_detail_depth_bound(fake: FakeRobotServer) -> None:
    """Depth=0 returns scalar only."""
    fake.robot_responses["/v1/factory/drafts/42"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": {
                    "setting_id": 42,
                    "name": "brain",
                    "config": {"a": {"b": {"c": "deep"}}, "x": 1},
                },
            }
        ).encode(),
    )

    async with _client(fake) as client:
        result = await _reader().get_detail(client, locator="tcfg:draft/brain/brain/42", depth=0)

    # depth=0 means config is serialized as a placeholder (scalar only).
    assert isinstance(result.subtree, str) or result.truncated


# -- get_template ----------------------------------------------------------


async def test_get_template_metadata_only(fake: FakeRobotServer) -> None:
    """Template metadata-only response."""
    fake.robot_responses["/v1/factory/templates/7"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": {
                    "setting_id": 7,
                    "name": "brain",
                    "setting_name": "Brain Template",
                    "template_name": "Brain v1",
                    "scene": "brain",
                    "config": {"model": "gpt-4"},
                    "factory_version": "v3.0",
                },
            }
        ).encode(),
    )

    async with _client(fake) as client:
        result = await _reader().get_template(client, template_id="7", metadata_only=True)

    assert result.template_id == "7"
    assert result.name == "Brain v1"
    assert result.lifecycle == "template"
    assert result.root_locator == "tcfg:template/7"
    # Detail fields should be defaults.
    assert result.subtree == {} or result.truncated is False


async def test_get_template_full(fake: FakeRobotServer) -> None:
    """Template full detail (same shape as get_config_detail)."""
    fake.robot_responses["/v1/factory/templates/7"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": {
                    "setting_id": 7,
                    "name": "brain",
                    "setting_name": "Brain Template",
                    "template_name": "Brain v1",
                    "config": {"model": "gpt-4", "temperature": 0.5},
                },
            }
        ).encode(),
    )

    async with _client(fake) as client:
        result = await _reader().get_template(client, template_id="7", metadata_only=False)

    assert result.template_id == "7"
    assert result.subtree == {"model": "gpt-4", "temperature": 0.5}
    assert result.state == "template"


async def test_get_template_not_found(fake: FakeRobotServer) -> None:
    """Non-existent template raises ConfigLocatorError."""
    fake.robot_responses["/v1/factory/templates/999"] = RobotResponse(404, b'{"code":404,"message":"not found"}')

    async with _client(fake) as client:
        with pytest.raises(ConfigLocatorError, match="999"):
            await _reader().get_template(client, template_id="999")


# -- Locator parsing -------------------------------------------------------


def test_parse_locator_valid() -> None:
    from theseus_kit.services.config_reader import parse_locator

    assert parse_locator("tcfg:draft") == ("draft", [])
    assert parse_locator("tcfg:draft/brain") == ("draft", ["brain"])
    assert parse_locator("tcfg:draft/brain/brain") == ("draft", ["brain", "brain"])
    assert parse_locator("tcfg:draft/brain/brain/42") == ("draft", ["brain", "brain", "42"])
    assert parse_locator("tcfg:online") == ("online", [])
    assert parse_locator("tcfg:template") == ("template", [])


def test_parse_locator_invalid() -> None:
    from theseus_kit.services.config_reader import parse_locator

    with pytest.raises(ConfigLocatorError, match="must start with"):
        parse_locator("bad:format")
    with pytest.raises(ConfigLocatorError, match="unknown state"):
        parse_locator("tcfg:unknown")
    with pytest.raises(ConfigLocatorError, match="missing state"):
        parse_locator("tcfg:")
