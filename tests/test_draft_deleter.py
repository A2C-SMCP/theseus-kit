"""Real-HTTP tests for Issue #39 — delete_draft tool backing service.

Drives :class:`DraftDeleter` against :class:`FakeRobotServer` (real TCP
socket, per project acceptance bar).  Covers successful deletion, 404
mapping, and robot-level error codes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from theseus_kit import DraftNotFoundError, RobotApiError, RobotClient, UserPatConfig
from theseus_kit.routing import RequestContext
from theseus_kit.services.draft_deleter import DraftDeleter
from theseus_kit.tokens import build_token_source

from ._fakeserver import FakeRobotServer, RobotResponse

_CTX = dict(robot_id="robot-1", namespace="default", robot_type="tfrobot")
_ROBOT_ID = "robot-1"
_DELETE_PATH = "/v1/factory/drafts/42"


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


def _deleter() -> DraftDeleter:
    return DraftDeleter(robot_id=_ROBOT_ID)


def _tfs(data: object) -> bytes:
    """Encode a TFSResponse-style JSON body with *data*."""
    return json.dumps({"code": 200, "message": "Success", "data": data}, ensure_ascii=False).encode()


def _error_response(code: int, message: str = "") -> bytes:
    return json.dumps({"code": code, "message": message or "error"}).encode()


# -- Success path ----------------------------------------------------------


async def test_delete_draft_success(fake: FakeRobotServer) -> None:
    """DELETE succeeds → confirmation response with the setting_id echo."""
    fake.robot_responses[("DELETE", _DELETE_PATH)] = RobotResponse(200, _tfs({}))
    client = _client(fake)

    result = await _deleter().delete_draft(client, setting_id=42)

    assert result.deleted is True
    assert result.setting_id == 42
    assert result.meta.fetched_at

    # The fake records the request with the correct method + path.
    assert fake.requests[-1].method == "DELETE"
    assert fake.requests[-1].path == _DELETE_PATH


# -- Error paths -----------------------------------------------------------


async def test_delete_draft_gateway_404_maps_to_not_found(fake: FakeRobotServer) -> None:
    """Route-level HTTP 404 (e.g. API version skew) maps to DraftNotFoundError.

    Note: TFRobotServer does NOT emit 404 for a missing draft on this route
    today — that case surfaces as HTTP 500 "No result found" (see
    test_delete_draft_missing_draft_robot_500), tracked as an upstream
    improvement request.
    """
    fake.robot_responses[("DELETE", _DELETE_PATH)] = RobotResponse(404, b'{"code": 404, "message": "not found"}')
    client = _client(fake)

    with pytest.raises(DraftNotFoundError):
        await _deleter().delete_draft(client, setting_id=42)


async def test_delete_draft_missing_draft_robot_500(fake: FakeRobotServer) -> None:
    """Real wire format for a missing draft: HTTP 500 'No result found' → RobotApiError."""
    fake.robot_responses[("DELETE", _DELETE_PATH)] = RobotResponse(
        500, b'{"code": 500, "message": "No result found", "data": {}}'
    )
    client = _client(fake)

    with pytest.raises(RobotApiError) as excinfo:
        await _deleter().delete_draft(client, setting_id=42)
    assert excinfo.value.status_code == 500


async def test_delete_draft_robot_error_code(fake: FakeRobotServer) -> None:
    """HTTP 200 with a non-200 TFS code maps to RobotApiError."""
    fake.robot_responses[("DELETE", _DELETE_PATH)] = RobotResponse(200, _error_response(500, "boom"))
    client = _client(fake)

    with pytest.raises(RobotApiError):
        await _deleter().delete_draft(client, setting_id=42)
