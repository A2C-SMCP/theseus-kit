"""Real-HTTP tests for Issue #5 — update_draft with conflict protection.

Drives :class:`DraftEditor` against :class:`FakeRobotServer` (real TCP
socket, per project acceptance bar).  Covers successful update,
optimistic-concurrency hash checks, error paths, and hash determinism.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from theseus_kit import (
    AuthRejectedError,
    DraftConflictError,
    DraftNotFoundError,
    RobotClient,
    RobotValidationError,
    UserPatConfig,
    compute_config_hash,
)
from theseus_kit.routing import RequestContext
from theseus_kit.services.draft_editor import DraftEditor
from theseus_kit.tokens import build_token_source

from ._fakeserver import FakeRobotServer, RobotResponse

_CTX = dict(robot_id="robot-1", namespace="default", robot_type="tfrobot")
_ROBOT_ID = "robot-1"


# -- Helpers ---------------------------------------------------------------


@pytest.fixture
def fake() -> Iterator[FakeRobotServer]:
    with FakeRobotServer() as server:
        yield server


def _client(fake: FakeRobotServer) -> RobotClient:
    cred = UserPatConfig(
        pat=SecretStr("tfp_test_pat"),
        robot_public_id="turingfocus:000042",
    )
    token_source = build_token_source(cred, manager_base_url=fake.manager_base_url)
    context = RequestContext(**_CTX)
    return RobotClient(
        token_source,
        context,
        api_base_url=fake.api_base_url,
    )


def _editor() -> DraftEditor:
    return DraftEditor(robot_id=_ROBOT_ID)


def _tfs(data: object) -> bytes:
    """Encode a TFSResponse-style JSON body with *data*."""
    return json.dumps({"code": 200, "message": "Success", "data": data}, ensure_ascii=False).encode()


def _draft_dto(**overrides: object) -> dict[str, Any]:
    """Build a minimal DraftFactorySettingDto dict for FakeRobotServer responses."""
    d: dict[str, Any] = {
        "setting_id": 1,
        "setting_name": "test-draft",
        "scene": "brain",
        "name": "brain",
        "config": {"key": "original"},
        "factory_version": "1.0",
        "compatible_versions": [],
        "config_schema": {},
        "tfs_actions": {},
    }
    d.update(overrides)
    return d


# -- Success paths ---------------------------------------------------------


async def test_update_draft_success(fake: FakeRobotServer) -> None:
    """PUT succeeds, response carries correct locator, hash, and revision."""
    draft = _draft_dto()
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))
    # The PUT response returns the updated DTO with the new setting_name.
    updated = _draft_dto(setting_name="renamed", config={"key": "updated"}, factory_version="2.0")
    fake.robot_responses[("PUT", "/v1/factory/drafts/1")] = RobotResponse(200, _tfs(updated))

    async with _client(fake) as client:
        result = await _editor().update_draft(
            client,
            setting_id=1,
            setting_name="renamed",
            config={"key": "updated"},
        )

    assert result.setting_id == 1
    assert result.setting_name == "renamed"
    assert result.scene == "brain"
    assert result.locator == "tcfg:draft/brain/brain/1"
    assert result.config == {"key": "updated"}
    assert result.revision == "2.0"
    assert result.content_hash == compute_config_hash({"key": "updated"})
    assert result.meta.fetched_at
    assert "T" in result.meta.fetched_at

    # Verify PUT request body matches TFRobotServer contract.
    put_requests = [r for r in fake.requests if r.method == "PUT"]
    assert len(put_requests) == 1
    put_body = json.loads(put_requests[0].body)
    assert "draftInfo" in put_body
    assert put_body["draftInfo"]["setting_name"] == "renamed"
    assert put_body["draftInfo"]["config"] == {"key": "updated"}


async def test_update_draft_hash_match(fake: FakeRobotServer) -> None:
    """expected_hash matches current config → succeeds."""
    config = {"key": "original"}
    draft = _draft_dto(config=config)
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))

    updated = _draft_dto(config={"key": "updated"})
    fake.robot_responses[("PUT", "/v1/factory/drafts/1")] = RobotResponse(200, _tfs(updated))

    expected = compute_config_hash(config)

    async with _client(fake) as client:
        result = await _editor().update_draft(
            client,
            setting_id=1,
            setting_name="renamed",
            config={"key": "updated"},
            expected_hash=expected,
        )

    assert result.config == {"key": "updated"}


async def test_update_draft_no_expected_hash(fake: FakeRobotServer) -> None:
    """No expected_hash → skips conflict check, succeeds."""
    draft = _draft_dto()
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))

    updated = _draft_dto(config={"key": "updated"})
    fake.robot_responses[("PUT", "/v1/factory/drafts/1")] = RobotResponse(200, _tfs(updated))

    async with _client(fake) as client:
        result = await _editor().update_draft(
            client,
            setting_id=1,
            setting_name="renamed",
            config={"key": "updated"},
        )

    assert result.config == {"key": "updated"}


async def test_update_draft_empty_config(fake: FakeRobotServer) -> None:
    """Empty config {} is accepted and hash is computed correctly."""
    draft = _draft_dto(config={})
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))

    updated = _draft_dto(config={})
    fake.robot_responses[("PUT", "/v1/factory/drafts/1")] = RobotResponse(200, _tfs(updated))

    async with _client(fake) as client:
        result = await _editor().update_draft(
            client,
            setting_id=1,
            setting_name="empty-config",
            config={},
        )

    assert result.config == {}
    assert result.content_hash == compute_config_hash({})


# -- Conflict --------------------------------------------------------------


async def test_update_draft_conflict(fake: FakeRobotServer) -> None:
    """expected_hash mismatch → DraftConflictError with current_hash."""
    config = {"key": "original"}
    draft = _draft_dto(config=config)
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))

    wrong_hash = compute_config_hash({"key": "someone-else-changed-this"})

    async with _client(fake) as client:
        with pytest.raises(DraftConflictError) as exc_info:
            await _editor().update_draft(
                client,
                setting_id=1,
                setting_name="renamed",
                config={"key": "updated"},
                expected_hash=wrong_hash,
            )

    assert exc_info.value.current_hash == compute_config_hash(config)


# -- Error paths -----------------------------------------------------------


async def test_update_draft_not_found_on_read(fake: FakeRobotServer) -> None:
    """GET returns 404 when expected_hash is provided → DraftNotFoundError."""
    fake.robot_responses["/v1/factory/drafts/999"] = RobotResponse(
        404,
        json.dumps({"code": 404, "message": "Not Found", "data": {}}).encode(),
    )

    async with _client(fake) as client:
        with pytest.raises(DraftNotFoundError, match="999"):
            await _editor().update_draft(
                client,
                setting_id=999,
                setting_name="renamed",
                config={"key": "updated"},
                expected_hash="fake-hash-to-trigger-read",
            )


async def test_update_draft_not_found_on_put(fake: FakeRobotServer) -> None:
    """GET succeeds but PUT returns 404 → DraftNotFoundError."""
    draft = _draft_dto()
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))
    fake.robot_responses[("PUT", "/v1/factory/drafts/1")] = RobotResponse(
        404,
        json.dumps({"code": 404, "message": "Not Found", "data": {}}).encode(),
    )

    async with _client(fake) as client:
        with pytest.raises(DraftNotFoundError, match="1"):
            await _editor().update_draft(
                client,
                setting_id=1,
                setting_name="renamed",
                config={"key": "updated"},
            )


async def test_update_draft_401(fake: FakeRobotServer) -> None:
    """401 on PUT → AuthRejectedError (LWW path: no GET)."""
    fake.robot_responses[("PUT", "/v1/factory/drafts/1")] = RobotResponse(401, b"unauthorized", "text/plain")

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _editor().update_draft(
                client,
                setting_id=1,
                setting_name="renamed",
                config={"key": "updated"},
            )


async def test_update_draft_401_on_read(fake: FakeRobotServer) -> None:
    """401 on GET (expected_hash triggers read) → AuthRejectedError."""
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(401, b"unauthorized", "text/plain")

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _editor().update_draft(
                client,
                setting_id=1,
                setting_name="renamed",
                config={"key": "updated"},
                expected_hash="any-hash-triggers-read",
            )


async def test_update_draft_403(fake: FakeRobotServer) -> None:
    """403 on PUT → AuthRejectedError (scope insufficient)."""
    draft = _draft_dto()
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))
    fake.robot_responses[("PUT", "/v1/factory/drafts/1")] = RobotResponse(403, b"forbidden", "text/plain")

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _editor().update_draft(
                client,
                setting_id=1,
                setting_name="renamed",
                config={"key": "updated"},
            )


async def test_update_draft_422(fake: FakeRobotServer) -> None:
    """422 on PUT → RobotValidationError with message."""
    draft = _draft_dto()
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))
    fake.robot_responses[("PUT", "/v1/factory/drafts/1")] = RobotResponse(
        422,
        json.dumps({"msg": "config must not be empty"}).encode(),
    )

    async with _client(fake) as client:
        with pytest.raises(RobotValidationError, match="config must not be empty"):
            await _editor().update_draft(
                client,
                setting_id=1,
                setting_name="renamed",
                config={"key": "updated"},
            )


# -- Hash determinism ------------------------------------------------------


def test_compute_config_hash_deterministic() -> None:
    """Same config with different key insertion order → same hash."""
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}
    assert compute_config_hash(a) == compute_config_hash(b)


def test_compute_config_hash_different() -> None:
    """Different config → different hash."""
    assert compute_config_hash({"a": 1}) != compute_config_hash({"a": 2})


def test_compute_config_hash_nested() -> None:
    """Nested dict with different insertion order → same hash."""
    a = {"outer": {"b": 2, "a": 1}}
    b = {"outer": {"a": 1, "b": 2}}
    assert compute_config_hash(a) == compute_config_hash(b)


# -- Response shape --------------------------------------------------------


async def test_update_draft_response_locator(fake: FakeRobotServer) -> None:
    """Response locator follows tcfg:draft/{scene}/{factory}/{setting_id} pattern."""
    draft = _draft_dto(scene="vision", name="detector")
    fake.robot_responses["/v1/factory/drafts/42"] = RobotResponse(200, _tfs(draft))

    updated = _draft_dto(setting_id=42, scene="vision", name="detector")
    fake.robot_responses[("PUT", "/v1/factory/drafts/42")] = RobotResponse(200, _tfs(updated))

    async with _client(fake) as client:
        result = await _editor().update_draft(
            client,
            setting_id=42,
            setting_name="detector-v2",
            config={"key": "value"},
        )

    assert result.locator == "tcfg:draft/vision/detector/42"
    assert result.setting_id == 42


async def test_update_draft_preserves_scene_and_factory(fake: FakeRobotServer) -> None:
    """Update response preserves scene and factory name from the server."""
    draft = _draft_dto(scene="chat", name="llm")
    fake.robot_responses["/v1/factory/drafts/7"] = RobotResponse(200, _tfs(draft))

    updated = _draft_dto(setting_id=7, scene="chat", name="llm", setting_name="llm-v2", factory_version="3.0")
    fake.robot_responses[("PUT", "/v1/factory/drafts/7")] = RobotResponse(200, _tfs(updated))

    async with _client(fake) as client:
        result = await _editor().update_draft(
            client,
            setting_id=7,
            setting_name="llm-v2",
            config={"model": "gpt-5"},
        )

    assert result.scene == "chat"
    assert result.setting_name == "llm-v2"
    assert result.revision == "3.0"
