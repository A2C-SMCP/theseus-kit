"""Real-HTTP tests for Issue #6 — save_template with conflict protection.

Drives :class:`TemplateSaver` against :class:`FakeRobotServer` (real TCP
socket, per project acceptance bar).  Covers successful save,
optimistic-concurrency hash checks, error paths, and contract validation.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from theseus_kit import (
    AuthRejectedError,
    ClientCredentialsConfig,
    DraftConflictError,
    DraftNotFoundError,
    RobotApiError,
    RobotClient,
    RobotValidationError,
    SaveTemplateResponse,
    compute_config_hash,
)
from theseus_kit.routing import RequestContext
from theseus_kit.services.template_saver import TemplateSaver
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
    )


def _saver() -> TemplateSaver:
    return TemplateSaver(robot_id=_ROBOT_ID)


def _tfs(data: object) -> bytes:
    """Encode a TFSResponse-style JSON body with *data*."""
    return json.dumps({"code": 200, "message": "Success", "data": data}, ensure_ascii=False).encode()


def _error_response(code: int, message: str = "Error") -> bytes:
    return json.dumps({"code": code, "message": message, "data": {}}).encode()


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


def _saveresp(template_id: int) -> bytes:
    """Encode a POST /savetemplate response."""
    return _tfs({"templateId": template_id})


# -- Success paths ---------------------------------------------------------


async def test_save_template_success(fake: FakeRobotServer) -> None:
    """POST /savetemplate returns 200 with templateId."""
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(200, _saveresp(42))

    async with _client(fake) as client:
        result = await _saver().save_template(
            client,
            setting_id=1,
            template_name="my-template",
        )

    assert result.template_id == 42
    assert result.locator == "tcfg:template/42"
    assert result.meta.fetched_at
    assert "T" in result.meta.fetched_at


async def test_save_template_hash_match(fake: FakeRobotServer) -> None:
    """expected_hash matches current config → succeeds."""
    config = {"key": "original"}
    draft = _draft_dto(config=config)
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(200, _saveresp(7))

    expected = compute_config_hash(config)

    async with _client(fake) as client:
        result = await _saver().save_template(
            client,
            setting_id=1,
            template_name="my-template",
            expected_hash=expected,
        )

    assert result.template_id == 7


async def test_save_template_no_hash(fake: FakeRobotServer) -> None:
    """No expected_hash → skips conflict check, succeeds."""
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(200, _saveresp(99))

    async with _client(fake) as client:
        result = await _saver().save_template(
            client,
            setting_id=1,
            template_name="my-template",
        )

    assert result.template_id == 99
    # No GET should have been issued for the draft.
    get_paths = [r.path for r in fake.requests if r.method == "GET"]
    draft_gets = [p for p in get_paths if "drafts/1" in p and "savetemplate" not in p]
    assert len(draft_gets) == 0


async def test_save_template_empty_config(fake: FakeRobotServer) -> None:
    """Empty config {} is accepted and hash is computed correctly."""
    draft = _draft_dto(config={})
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(200, _saveresp(1))

    async with _client(fake) as client:
        result = await _saver().save_template(
            client,
            setting_id=1,
            template_name="empty-config-template",
            expected_hash=compute_config_hash({}),
        )

    assert result.template_id == 1


# -- Pre-save guards -------------------------------------------------------


async def test_save_template_empty_name(fake: FakeRobotServer) -> None:
    """Empty template_name → ValueError (no request issued)."""
    async with _client(fake) as client:
        with pytest.raises(ValueError, match="template_name"):
            await _saver().save_template(
                client,
                setting_id=1,
                template_name="",
            )

    # No POST should have been made.
    posts = [r for r in fake.requests if r.method == "POST"]
    assert len(posts) == 0


async def test_save_template_whitespace_name(fake: FakeRobotServer) -> None:
    """Whitespace-only template_name → ValueError."""
    async with _client(fake) as client:
        with pytest.raises(ValueError, match="template_name"):
            await _saver().save_template(
                client,
                setting_id=1,
                template_name="   ",
            )

    posts = [r for r in fake.requests if r.method == "POST"]
    assert len(posts) == 0


# -- Conflict --------------------------------------------------------------


async def test_save_template_hash_mismatch(fake: FakeRobotServer) -> None:
    """expected_hash mismatch → DraftConflictError with current_hash."""
    config = {"key": "original"}
    draft = _draft_dto(config=config)
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))

    wrong_hash = compute_config_hash({"key": "someone-else-changed-this"})

    async with _client(fake) as client:
        with pytest.raises(DraftConflictError) as exc_info:
            await _saver().save_template(
                client,
                setting_id=1,
                template_name="my-template",
                expected_hash=wrong_hash,
            )

    assert exc_info.value.current_hash == compute_config_hash(config)


# -- Error paths -----------------------------------------------------------


async def test_save_template_not_found_on_read(fake: FakeRobotServer) -> None:
    """GET returns 404 when expected_hash is provided → DraftNotFoundError."""
    fake.robot_responses["/v1/factory/drafts/999"] = RobotResponse(
        404,
        json.dumps({"code": 404, "message": "Not Found", "data": {}}).encode(),
    )

    async with _client(fake) as client:
        with pytest.raises(DraftNotFoundError, match="999"):
            await _saver().save_template(
                client,
                setting_id=999,
                template_name="my-template",
                expected_hash="fake-hash-to-trigger-read",
            )


async def test_save_template_not_found_on_post(fake: FakeRobotServer) -> None:
    """POST /savetemplate returns 404 → DraftNotFoundError."""
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(
        404,
        json.dumps({"code": 404, "message": "Not Found", "data": {}}).encode(),
    )

    async with _client(fake) as client:
        with pytest.raises(DraftNotFoundError, match="1"):
            await _saver().save_template(
                client,
                setting_id=1,
                template_name="my-template",
            )


async def test_save_template_401(fake: FakeRobotServer) -> None:
    """401 on POST → AuthRejectedError (LWW path: no GET)."""
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(
        401, b"unauthorized", "text/plain"
    )

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _saver().save_template(
                client,
                setting_id=1,
                template_name="my-template",
            )


async def test_save_template_403(fake: FakeRobotServer) -> None:
    """403 on POST → AuthRejectedError (scope insufficient: need config:write)."""
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(403, b"forbidden", "text/plain")

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _saver().save_template(
                client,
                setting_id=1,
                template_name="my-template",
            )


async def test_save_template_401_on_read(fake: FakeRobotServer) -> None:
    """401 on GET (expected_hash triggers read) → AuthRejectedError."""
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(401, b"unauthorized", "text/plain")

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _saver().save_template(
                client,
                setting_id=1,
                template_name="my-template",
                expected_hash="any-hash-triggers-read",
            )


async def test_save_template_422(fake: FakeRobotServer) -> None:
    """422 on POST → RobotValidationError with message (e.g. illegal name)."""
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(
        422,
        json.dumps({"msg": "模板名称不能为空"}).encode(),
    )

    async with _client(fake) as client:
        with pytest.raises(RobotValidationError, match="模板名称不能为空"):
            await _saver().save_template(
                client,
                setting_id=1,
                template_name="my-template",
            )


async def test_save_template_name_conflict(fake: FakeRobotServer) -> None:
    """Body code != 200 (e.g. name conflict) → RobotApiError."""
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(
        200, _error_response(409, "模板名称已存在")
    )

    async with _client(fake) as client:
        with pytest.raises(RobotApiError, match="409"):
            await _saver().save_template(
                client,
                setting_id=1,
                template_name="my-template",
            )


# -- Request contract ------------------------------------------------------


async def test_save_template_request_body(fake: FakeRobotServer) -> None:
    """POST body carries {name: template_name}."""
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(200, _saveresp(42))

    async with _client(fake) as client:
        await _saver().save_template(
            client,
            setting_id=1,
            template_name="my-template",
        )

    posts = [r for r in fake.requests if r.method == "POST" and "savetemplate" in r.path]
    assert len(posts) == 1
    body = json.loads(posts[0].body)
    assert body == {"name": "my-template"}


async def test_save_template_single_request(fake: FakeRobotServer) -> None:
    """One tool call → at most one upstream POST (no retry)."""
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(500, _error_response(500))

    async with _client(fake) as client:
        with pytest.raises(RobotApiError):
            await _saver().save_template(
                client,
                setting_id=1,
                template_name="my-template",
            )

    posts = [r for r in fake.requests if r.method == "POST" and "savetemplate" in r.path]
    assert len(posts) == 1


# -- Response shape --------------------------------------------------------


async def test_save_template_response_shape(fake: FakeRobotServer) -> None:
    """Response carries template_id, locator, and _meta."""
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(200, _saveresp(123))

    async with _client(fake) as client:
        result = await _saver().save_template(
            client,
            setting_id=1,
            template_name="my-template",
        )

    assert isinstance(result, SaveTemplateResponse)
    assert result.template_id == 123
    assert result.locator == "tcfg:template/123"
    assert result.meta is not None
    assert isinstance(result.meta.fetched_at, str)


# -- Hash determinism ------------------------------------------------------


def test_hash_deterministic() -> None:
    """Same config different order → same hash."""
    a = compute_config_hash({"b": 2, "a": 1})
    b = compute_config_hash({"a": 1, "b": 2})
    assert a == b


def test_hash_different() -> None:
    """Different config → different hash."""
    assert compute_config_hash({"a": 1}) != compute_config_hash({"a": 2})


# -- Integration: read-check-save ------------------------------------------


async def test_save_template_read_then_save(fake: FakeRobotServer) -> None:
    """The hash consumed by save_template must match what a real GET returns."""
    config = {"key": "original"}
    draft = _draft_dto(config=config)
    fake.robot_responses["/v1/factory/drafts/1"] = RobotResponse(200, _tfs(draft))
    fake.robot_responses[("POST", "/v1/factory/drafts/1/savetemplate")] = RobotResponse(200, _saveresp(42))

    async with _client(fake) as client:
        # Step 1: read draft (what get_config_detail would do internally).
        resp = await client.get("/v1/factory/drafts/1")
        body = resp.json()
        read_config: dict[str, Any] = body["data"]["config"]
        read_hash = compute_config_hash(read_config)

        # Step 2: save with that hash.
        result = await _saver().save_template(
            client,
            setting_id=1,
            template_name="my-template",
            expected_hash=read_hash,
        )

    assert result.template_id == 42
