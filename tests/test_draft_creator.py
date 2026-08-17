"""Real-HTTP tests for create_draft — new draft configuration creation.

Drives :class:`DraftCreator` against :class:`FakeRobotServer` (real TCP
socket). Covers successful creation, validation errors, auth failures, and
response model integrity.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from theseus_kit import (
    AuthRejectedError,
    CreateDraftResponse,
    RobotApiError,
    RobotClient,
    RobotValidationError,
    UserPatConfig,
)
from theseus_kit.routing import RequestContext
from theseus_kit.services.draft_creator import DraftCreator
from theseus_kit.tokens import build_token_source

from ._fakeserver import FakeRobotServer, RobotResponse

_CTX = dict(robot_id="robot-1", namespace="default", robot_type="tfrobot")
_ROBOT_ID = "robot-1"
_CREATE_PATH = "/v1/factory/drafts"


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


def _creator() -> DraftCreator:
    return DraftCreator(robot_id=_ROBOT_ID)


def _tfs(data: object) -> bytes:
    return json.dumps({"code": 200, "message": "Success", "data": data}, ensure_ascii=False).encode()


def _created_draft_dto(**overrides: object) -> dict[str, Any]:
    """Build a minimal created-draft DTO for FakeRobotServer responses."""
    d: dict[str, Any] = {
        "settingId": 42,
        "settingName": "my-llm",
        "scene": "LLM",
        "factoryName": "GLM草稿",
        "config": {"model": "glm-4", "temperature": 0.7},
        "factoryVersion": "1.0",
    }
    d.update(overrides)
    return d


def _error_response(code: int, message: str = "") -> bytes:
    return json.dumps({"code": code, "message": message or "error"}).encode()


# -- Successful creation ---------------------------------------------------


async def test_create_draft_success(fake: FakeRobotServer) -> None:
    """Normal creation returns setting_id and content_hash."""
    dto = _created_draft_dto()
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(200, _tfs(dto))

    async with _client(fake) as client:
        result = await _creator().create(
            client,
            scene="LLM",
            factory_name="GLM草稿",
            setting_name="my-llm",
            config={"model": "glm-4", "temperature": 0.7},
        )

    assert isinstance(result, CreateDraftResponse)
    assert result.setting_id == 42
    assert result.setting_name == "my-llm"
    assert result.scene == "LLM"
    assert result.content_hash != ""
    assert result.revision == "1.0"


async def test_create_draft_minimal_config(fake: FakeRobotServer) -> None:
    """Creation without config produces empty config and a valid hash."""
    dto = _created_draft_dto(config={})
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(200, _tfs(dto))

    async with _client(fake) as client:
        result = await _creator().create(
            client,
            scene="LLM",
            factory_name="GLM草稿",
            setting_name="bare",
        )

    assert result.setting_id == 42
    assert result.config == {}
    # Empty config hash is a deterministic SHA-256 of "{}"
    assert result.content_hash != ""


async def test_create_draft_response_has_meta(fake: FakeRobotServer) -> None:
    """Response carries _meta.fetched_at (ISO 8601 timestamp)."""
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(200, _tfs(_created_draft_dto()))

    async with _client(fake) as client:
        result = await _creator().create(
            client,
            scene="LLM",
            factory_name="GLM草稿",
            setting_name="x",
        )

    dumped = result.model_dump(by_alias=True, mode="json")
    assert "_meta" in dumped
    assert "fetched_at" in dumped["_meta"]


# -- Request body validation -----------------------------------------------


async def test_create_draft_sends_robot_api_contract_payload(fake: FakeRobotServer) -> None:
    """Creation uses the canonical route and DTO expected by TFRobotServer."""
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(200, _tfs(_created_draft_dto()))

    async with _client(fake) as client:
        await _creator().create(
            client,
            scene="LLM",
            factory_name="CLAUDE草稿",
            setting_name="claude-v1",
            config={"key": "val"},
        )

    assert len(fake.requests) >= 1
    # Find the create request
    create_req = None
    for req in fake.requests:
        if req.method == "POST" and req.path == _CREATE_PATH:
            create_req = req
            break
    assert create_req is not None
    body = json.loads(create_req.body or "{}")
    assert body["scene"] == "LLM"
    assert body["name"] == "CLAUDE草稿"
    assert body["settingName"] == "claude-v1"
    assert body["config"] == {"key": "val"}
    assert "factoryName" not in body


async def test_create_draft_null_config_sends_empty_object(fake: FakeRobotServer) -> None:
    """When config is None, the required Robot API field is an empty object."""
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(200, _tfs(_created_draft_dto()))

    async with _client(fake) as client:
        await _creator().create(
            client,
            scene="LLM",
            factory_name="GLM草稿",
            setting_name="x",
        )

    create_req = None
    for req in fake.requests:
        if req.method == "POST" and req.path == _CREATE_PATH:
            create_req = req
            break
    assert create_req is not None
    body = json.loads(create_req.body or "{}")
    assert body["config"] == {}


# -- Error paths -----------------------------------------------------------


async def test_create_draft_422_validation_error(fake: FakeRobotServer) -> None:
    """Server 422 maps to RobotValidationError."""
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(
        422, _error_response(422, "factory not found: NopeFactory")
    )

    async with _client(fake) as client:
        with pytest.raises(RobotValidationError):
            await _creator().create(
                client,
                scene="LLM",
                factory_name="NopeFactory",
                setting_name="x",
            )


async def test_create_draft_401_auth_error(fake: FakeRobotServer) -> None:
    """Server 401 maps to AuthRejectedError."""
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(401, b"unauthorized", "text/plain")

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _creator().create(
                client,
                scene="LLM",
                factory_name="GLM草稿",
                setting_name="x",
            )


async def test_create_draft_403_auth_error(fake: FakeRobotServer) -> None:
    """Server 403 maps to AuthRejectedError."""
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(403, b"forbidden", "text/plain")

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _creator().create(
                client,
                scene="LLM",
                factory_name="GLM草稿",
                setting_name="x",
            )


async def test_create_draft_500_error(fake: FakeRobotServer) -> None:
    """Server 500 maps to RobotApiError (via the transport layer)."""
    from theseus_kit.errors import RobotApiError

    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(500, _error_response(500, "internal error"))

    async with _client(fake) as client:
        with pytest.raises(RobotApiError):
            await _creator().create(
                client,
                scene="LLM",
                factory_name="GLM草稿",
                setting_name="x",
            )


# -- Content hash -----------------------------------------------------------


async def test_create_draft_content_hash_matches_compute(fake: FakeRobotServer) -> None:
    """The returned content_hash equals compute_config_hash(returned_config)."""
    from theseus_kit import compute_config_hash

    config = {"model": "deepseek-v3", "max_tokens": 8192}
    dto = _created_draft_dto(config=config)
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(200, _tfs(dto))

    async with _client(fake) as client:
        result = await _creator().create(
            client,
            scene="LLM",
            factory_name="DeepSeek草稿",
            setting_name="ds",
            config=config,
        )

    expected_hash = compute_config_hash(config)
    assert result.content_hash == expected_hash


# -- Response contract -----------------------------------------------------


async def test_create_draft_uses_camelcase_response_contract(fake: FakeRobotServer) -> None:
    """The frozen rc5 camelCase response fields are parsed directly."""
    dto = _created_draft_dto()
    dto["settingId"] = 99
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(200, _tfs(dto))

    async with _client(fake) as client:
        result = await _creator().create(
            client,
            scene="LLM",
            factory_name="GLM草稿",
            setting_name="x",
        )

    assert result.setting_id == 99


async def test_create_draft_rejects_snake_case_response_alias(fake: FakeRobotServer) -> None:
    """A snake_case-only response fails loudly instead of hiding contract drift."""
    dto = _created_draft_dto()
    dto["setting_id"] = dto.pop("settingId")
    fake.robot_responses[("POST", _CREATE_PATH)] = RobotResponse(200, _tfs(dto))

    async with _client(fake) as client:
        with pytest.raises(RobotApiError, match=r"rc5 camelCase contract: missing data\.settingId"):
            await _creator().create(
                client,
                scene="LLM",
                factory_name="GLM草稿",
                setting_name="x",
            )
