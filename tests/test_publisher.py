"""Real-HTTP tests for Issue #7 — publish_config with pre-publish guards.

Drives :class:`ConfigPublisher` against :class:`FakeRobotServer` (real TCP
socket, per project acceptance bar).  Covers successful publish,
pre-publish guards (acknowledgement + root-hash), error paths, and
the "at most one upstream request" invariant.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from theseus_kit import (
    AuthRejectedError,
    ClientCredentialsConfig,
    ConfigPublisher,
    PublishConfigResponse,
    PublishNotConfirmedError,
    PublishPreCheckError,
    RobotApiError,
    RobotClient,
    RobotValidationError,
    compute_config_hash,
)
from theseus_kit.routing import RequestContext
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


def _publisher() -> ConfigPublisher:
    return ConfigPublisher(robot_id=_ROBOT_ID)


def _json(data: object) -> bytes:
    """Encode a TFSResponse-style JSON body with *data*."""
    return json.dumps({"code": 200, "message": "Success", "data": data}, ensure_ascii=False).encode()


def _scenes_response(scenes: list[str]) -> bytes:
    """Encode a GET /v1/factory/drafts/scenes response."""
    return _json(scenes)


def _release_response(online_robot_id: int) -> bytes:
    """Encode a POST /v1/factory/drafts/release response."""
    return _json({"onlineRobotId": online_robot_id})


def _error_response(code: int, message: str = "Error") -> bytes:
    return json.dumps({"code": code, "message": message, "data": {}}).encode()


def _scenes_hash(scenes: list[str]) -> str:
    return compute_config_hash({"scenes": sorted(scenes)})


# -- Success paths ---------------------------------------------------------


async def test_publish_success(fake: FakeRobotServer) -> None:
    """POST /release returns 200 with onlineRobotId."""
    scenes = ["brain", "vision"]
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _scenes_response(scenes))
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(200, _release_response(42))

    root_hash = _scenes_hash(scenes)

    async with _client(fake) as client:
        result = await _publisher().publish_config(
            client,
            expected_root_hash=root_hash,
            acknowledge_publish=True,
        )

    assert result.online_robot_id == 42
    assert result.locator == "tcfg:online"
    assert result.meta.fetched_at
    assert "T" in result.meta.fetched_at


async def test_publish_root_hash_match(fake: FakeRobotServer) -> None:
    """expected_root_hash matches current scenes → succeeds."""
    scenes = ["brain", "chat"]
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _scenes_response(scenes))
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(200, _release_response(7))

    async with _client(fake) as client:
        result = await _publisher().publish_config(
            client,
            expected_root_hash=_scenes_hash(scenes),
            acknowledge_publish=True,
        )

    assert result.online_robot_id == 7


async def test_publish_no_root_hash(fake: FakeRobotServer) -> None:
    """No expected_root_hash → skips scene-list read, succeeds."""
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(200, _release_response(1))

    async with _client(fake) as client:
        result = await _publisher().publish_config(
            client,
            acknowledge_publish=True,
        )

    assert result.online_robot_id == 1
    # When expected_root_hash is omitted, no GET should have been issued.
    get_paths = [r.path for r in fake.requests if r.method == "GET"]
    # Allow token exchange POSTs but disallow scene-list GET.
    scene_gets = [p for p in get_paths if "scenes" in p]
    assert len(scene_gets) == 0


async def test_publish_empty_scenes(fake: FakeRobotServer) -> None:
    """Empty scene list → hash matches empty list, publish proceeds."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _scenes_response([]))
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(200, _release_response(99))

    async with _client(fake) as client:
        result = await _publisher().publish_config(
            client,
            expected_root_hash=_scenes_hash([]),
            acknowledge_publish=True,
        )

    assert result.online_robot_id == 99


# -- Pre-publish guards ----------------------------------------------------


async def test_publish_not_confirmed(fake: FakeRobotServer) -> None:
    """acknowledge_publish=False → PublishNotConfirmedError (no POST issued)."""
    async with _client(fake) as client:
        with pytest.raises(PublishNotConfirmedError, match="acknowledge_publish"):
            await _publisher().publish_config(
                client,
                acknowledge_publish=False,
            )

    # No POST to /release should have been made.
    posts = [r for r in fake.requests if r.method == "POST" and "release" in r.path]
    assert len(posts) == 0


async def test_publish_root_hash_mismatch(fake: FakeRobotServer) -> None:
    """expected_root_hash doesn't match → PublishPreCheckError."""
    current_scenes = ["brain", "vision"]
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _scenes_response(current_scenes))

    wrong_hash = _scenes_hash(["brain"])  # only one scene

    async with _client(fake) as client:
        with pytest.raises(PublishPreCheckError, match="root structure has changed"):
            await _publisher().publish_config(
                client,
                expected_root_hash=wrong_hash,
                acknowledge_publish=True,
            )

    # No POST should have been made.
    posts = [r for r in fake.requests if r.method == "POST" and "release" in r.path]
    assert len(posts) == 0


# -- Error paths -----------------------------------------------------------


async def test_publish_401(fake: FakeRobotServer) -> None:
    """401 on POST → AuthRejectedError."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _scenes_response(["brain"]))
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(401, b"unauthorized", "text/plain")

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _publisher().publish_config(
                client,
                expected_root_hash=_scenes_hash(["brain"]),
                acknowledge_publish=True,
            )


async def test_publish_403(fake: FakeRobotServer) -> None:
    """403 on POST → AuthRejectedError (scope insufficient: need config:publish)."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _scenes_response(["brain"]))
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(403, b"forbidden", "text/plain")

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _publisher().publish_config(
                client,
                expected_root_hash=_scenes_hash(["brain"]),
                acknowledge_publish=True,
            )


async def test_publish_401_on_scenes_read(fake: FakeRobotServer) -> None:
    """401 on GET scenes (during root hash check) → AuthRejectedError."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(401, b"unauthorized", "text/plain")

    async with _client(fake) as client:
        with pytest.raises(AuthRejectedError):
            await _publisher().publish_config(
                client,
                expected_root_hash="any-hash-triggers-read",
                acknowledge_publish=True,
            )


async def test_publish_500_robot_config_missing(fake: FakeRobotServer) -> None:
    """500 from server (e.g. no ROBOT config) → RobotApiError."""
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(
        500, _error_response(500, "No result found")
    )

    async with _client(fake) as client:
        with pytest.raises(RobotApiError):
            await _publisher().publish_config(
                client,
                acknowledge_publish=True,
            )


async def test_publish_500_non_200_code_in_body(fake: FakeRobotServer) -> None:
    """Body code != 200 after successful HTTP → RobotApiError."""
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(
        200, _error_response(500, "No result found")
    )

    async with _client(fake) as client:
        with pytest.raises(RobotApiError):
            await _publisher().publish_config(
                client,
                acknowledge_publish=True,
            )


async def test_publish_422_validation_error(fake: FakeRobotServer) -> None:
    """422 on POST → RobotValidationError (e.g. online validation failed)."""
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(
        422,
        json.dumps({"msg": "当前Robot配置不满足上线，无法发布"}).encode(),
    )

    async with _client(fake) as client:
        with pytest.raises(RobotValidationError, match="当前Robot配置不满足上线"):
            await _publisher().publish_config(
                client,
                acknowledge_publish=True,
            )


# -- Request contract ------------------------------------------------------


async def test_publish_no_request_body(fake: FakeRobotServer) -> None:
    """POST /release sends no body (TFRobotServer contract)."""
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _scenes_response(["brain"]))
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(200, _release_response(1))

    async with _client(fake) as client:
        await _publisher().publish_config(
            client,
            expected_root_hash=_scenes_hash(["brain"]),
            acknowledge_publish=True,
        )

    posts = [r for r in fake.requests if r.method == "POST" and "release" in r.path]
    assert len(posts) == 1
    # Body should be empty or negligible (RobotClient.post with no json/data args).
    assert posts[0].body == b"" or posts[0].body == b"null"


async def test_publish_single_request(fake: FakeRobotServer) -> None:
    """One tool call → at most one upstream POST /release (no retry)."""
    # Simulate a server error — since retry=False, there should be exactly 1 attempt.
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(500, _error_response(500))

    async with _client(fake) as client:
        with pytest.raises(RobotApiError):
            await _publisher().publish_config(
                client,
                acknowledge_publish=True,
            )

    posts = [r for r in fake.requests if r.method == "POST" and "release" in r.path]
    assert len(posts) == 1


# -- Response shape --------------------------------------------------------


async def test_publish_response_shape(fake: FakeRobotServer) -> None:
    """Response carries online_robot_id, locator, and _meta."""
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(200, _release_response(123))

    async with _client(fake) as client:
        result = await _publisher().publish_config(
            client,
            acknowledge_publish=True,
        )

    assert isinstance(result, PublishConfigResponse)
    assert result.online_robot_id == 123
    assert result.locator == "tcfg:online"
    assert result.meta is not None
    assert isinstance(result.meta.fetched_at, str)


# -- Root hash determinism -------------------------------------------------


def test_root_hash_deterministic() -> None:
    """Same scene list different order → same hash."""
    a = _scenes_hash(["brain", "vision", "chat"])
    b = _scenes_hash(["chat", "brain", "vision"])
    assert a == b


def test_root_hash_different() -> None:
    """Different scene lists → different hash."""
    assert _scenes_hash(["brain"]) != _scenes_hash(["brain", "vision"])


# -- Integration: hash agreement -------------------------------------------


async def test_publish_scenes_must_match_read(fake: FakeRobotServer) -> None:
    """The hash consumed by publish must match what a real GET /scenes returns."""
    scenes = ["brain", "vision", "chat"]
    fake.robot_responses["/v1/factory/drafts/scenes"] = RobotResponse(200, _scenes_response(scenes))
    fake.robot_responses[("POST", "/v1/factory/drafts/release")] = RobotResponse(200, _release_response(42))

    # Simulate what a caller would do: read scenes, compute hash, pass to publish.
    async with _client(fake) as client:
        # Step 1: read scenes (what get_config_summary would do internally).
        resp = await client.get("/v1/factory/drafts/scenes")
        body = resp.json()
        read_scenes: list[str] = body["data"]
        read_hash = compute_config_hash({"scenes": sorted(read_scenes)})

        # Step 2: publish with that hash.
        result = await _publisher().publish_config(
            client,
            expected_root_hash=read_hash,
            acknowledge_publish=True,
        )

    assert result.online_robot_id == 42
