"""Real-HTTP tests for Issue #3 — RobotClient typed methods + retry.

Drives the real ``RobotClient`` against :class:`FakeRobotServer` (real TCP
socket, per #3's acceptance bar). Covers ``get_model``, ``get_paginated``,
``post``, ``put``, ``delete``, 422 validation errors, and read-only retry.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator

import pytest
from pydantic import BaseModel, SecretStr

from theseus_kit import (
    AuthRejectedError,
    PaginatedList,
    RobotApiError,
    RobotClient,
    RobotValidationError,
    UserPatConfig,
)
from theseus_kit.routing import RequestContext
from theseus_kit.tokens import build_token_source

from ._fakeserver import FakeRobotServer, RobotResponse

_CTX = dict(robot_id="robot-1", namespace="default", robot_type="tfrobot")


# ── Test DTOs ─────────────────────────────────────────────────────────────────


class _Item(BaseModel):
    id: int
    name: str


# ── Helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_server() -> Iterator[FakeRobotServer]:
    with FakeRobotServer() as server:
        yield server


def _client(fake: FakeRobotServer, *, api_base_url: str | None = None, **kw: object) -> RobotClient:
    cred = UserPatConfig(
        pat=SecretStr("tfp_test_pat"),
        robot_public_id="turingfocus:000042",
    )
    token_source = build_token_source(cred, manager_base_url=fake.manager_base_url)
    context = RequestContext(**_CTX)
    return RobotClient(
        token_source,
        context,
        api_base_url=api_base_url if api_base_url is not None else fake.api_base_url,
        **kw,  # type: ignore[arg-type]
    )


# ── get_model (typed GET) ─────────────────────────────────────────────────────


async def test_get_model_parses_tfs_response(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses["/test"] = RobotResponse(
        200, b'{"code":200,"message":"Success","data":{"id":1,"name":"test"}}'
    )
    async with _client(fake_server) as client:
        result = await client.get_model("/test", _Item)
    assert result.code == 200
    assert isinstance(result.data, _Item)
    assert result.data.id == 1


async def test_get_model_code_not_200_raises(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses["/test"] = RobotResponse(200, b'{"code":500,"message":"internal error","data":{}}')
    async with _client(fake_server) as client:
        with pytest.raises(RobotApiError) as exc_info:
            await client.get_model("/test", _Item)
    assert exc_info.value.status_code == 500


async def test_get_model_injects_bearer_and_routing_headers(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses["/test"] = RobotResponse(
        200, b'{"code":200,"message":"ok","data":{"id":2,"name":"with-headers"}}'
    )
    async with _client(fake_server) as client:
        await client.get_model("/test", _Item)
    headers = fake_server.robot_gets_for("/test")[0].headers
    assert headers["x-tf-namespace"] == "default"
    assert headers["authorization"].startswith("Bearer ")


# ── get_paginated ─────────────────────────────────────────────────────────────


async def test_get_paginated_returns_paginated_list(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses["/v1/factory/templates/query"] = RobotResponse(
        200,
        json.dumps(
            {
                "code": 200,
                "message": "Success",
                "data": {
                    "items": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
                    "total": 42,
                },
            }
        ).encode(),
    )
    async with _client(fake_server) as client:
        result = await client.get_paginated("/v1/factory/templates/query", _Item, page=1, page_size=10)
    assert result.code == 200
    assert isinstance(result.data, PaginatedList)
    assert len(result.data.items) == 2
    assert result.data.total == 42
    assert result.data.items[0].name == "a"


async def test_get_paginated_sends_page_params(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses["/paginated"] = RobotResponse(
        200, b'{"code":200,"message":"ok","data":{"items":[],"total":0}}'
    )
    async with _client(fake_server) as client:
        await client.get_paginated("/paginated", _Item, page=3, page_size=50)
    gets = [r for r in fake_server.robot_gets() if r.path.startswith("/paginated?")]
    assert len(gets) == 1
    assert "page=3" in gets[0].path
    assert "pageSize=50" in gets[0].path


async def test_get_paginated_empty_list(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses["/paginated"] = RobotResponse(
        200, b'{"code":200,"message":"ok","data":{"items":[],"total":0}}'
    )
    async with _client(fake_server) as client:
        result = await client.get_paginated("/paginated", _Item)
    assert len(result.data.items) == 0
    assert result.data.total == 0


# ── POST ─────────────────────────────────────────────────────────────────────


async def test_post_sends_json_body(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses[("POST", "/create")] = RobotResponse(
        200, b'{"code":200,"message":"created","data":{"ok":true,"count":1}}'
    )
    payload = {"name": "new-item"}
    async with _client(fake_server) as client:
        resp = await client.post("/create", json=payload)
    assert resp.status_code == 200
    post_reqs = [r for r in fake_server.requests if r.method == "POST" and r.path == "/create"]
    assert len(post_reqs) == 1
    assert json.loads(post_reqs[0].body) == payload


async def test_post_injects_routing_headers(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses[("POST", "/create")] = RobotResponse(200, b'{"code":200,"message":"ok","data":{}}')
    async with _client(fake_server) as client:
        await client.post("/create", json={})
    post_reqs = [r for r in fake_server.requests if r.method == "POST" and r.path == "/create"]
    headers = post_reqs[0].headers
    assert headers["x-tf-namespace"] == "default"
    assert headers["authorization"].startswith("Bearer ")


async def test_post_returns_422_validation_error(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses[("POST", "/create")] = RobotResponse(
        422, b'{"code":422,"msg":"param [name] validation failed","data":null}'
    )
    async with _client(fake_server) as client:
        with pytest.raises(RobotValidationError) as exc_info:
            await client.post("/create", json={"name": ""})
    assert exc_info.value.status_code == 422
    assert "param" in exc_info.value.validation_message


async def test_post_422_falls_back_to_message_field(fake_server: FakeRobotServer) -> None:
    """When the 422 response uses 'message' instead of 'msg'."""
    fake_server.robot_responses[("POST", "/create")] = RobotResponse(422, b'{"code":422,"message":"Validation failed"}')
    async with _client(fake_server) as client:
        with pytest.raises(RobotValidationError) as exc_info:
            await client.post("/create", json={})
    assert exc_info.value.validation_message == "Validation failed"


# ── PUT ───────────────────────────────────────────────────────────────────────


async def test_put_sends_json_body(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses[("PUT", "/update/1")] = RobotResponse(
        200, b'{"code":200,"message":"updated","data":{"id":1,"name":"updated"}}'
    )
    payload = {"name": "updated"}
    async with _client(fake_server) as client:
        resp = await client.put("/update/1", json=payload)
    assert resp.status_code == 200
    put_reqs = [r for r in fake_server.requests if r.method == "PUT"]
    assert len(put_reqs) == 1
    assert json.loads(put_reqs[0].body) == payload


# ── DELETE ────────────────────────────────────────────────────────────────────


async def test_delete_returns_204(fake_server: FakeRobotServer) -> None:
    fake_server.robot_responses[("DELETE", "/delete/1")] = RobotResponse(204, b"")
    async with _client(fake_server) as client:
        resp = await client.delete("/delete/1")
    assert resp.status_code == 204


async def test_delete_unmapped_defaults_to_204(fake_server: FakeRobotServer) -> None:
    async with _client(fake_server) as client:
        resp = await client.delete("/no-exist")
    assert resp.status_code == 204


# ── Retry: GET (retry enabled) ────────────────────────────────────────────────

# To test retry on the same path with varying responses, we use a _PopDict
# that pops the front of a list on each dict lookup, simulating a sequence
# of responses for the same key.


class _PopDict(dict):  # type: ignore[type-arg]
    """A dict that pops from a shared _store list before falling through."""

    def __init__(self, store: list[RobotResponse]) -> None:
        super().__init__()
        self._store = store

    def get(self, key: object, default: object = None) -> object:
        if self._store:
            return self._store.pop(0)
        return super().get(key, default)


async def test_get_retry_makes_two_attempts_on_503(
    fake_server: FakeRobotServer,
) -> None:
    """GET 503 → retry → second 503 → RobotApiError. 2 attempts made."""
    store = [
        RobotResponse(503, b'{"code":503,"message":"unavailable","data":{}}'),
        RobotResponse(503, b'{"code":503,"message":"still-unavailable","data":{}}'),
    ]
    fake_server.robot_responses = _PopDict(store)

    async with _client(fake_server) as client:
        with pytest.raises(RobotApiError) as exc_info:
            await client.get_model("/retry-twice", _Item)
    assert exc_info.value.status_code == 503
    assert len(fake_server.robot_gets_for("/retry-twice")) == 2


async def test_post_no_retry_single_attempt(fake_server: FakeRobotServer) -> None:
    """POST 503 → single attempt, no retry (mutations are not idempotent)."""
    store = [
        RobotResponse(503, b'{"code":503,"message":"unavailable","data":{}}'),
        RobotResponse(200, b'{"code":200,"message":"would-be-ignored","data":{}}'),
    ]
    fake_server.robot_responses = _PopDict(store)

    async with _client(fake_server) as client:
        with pytest.raises(RobotApiError) as exc_info:
            await client.post("/no-retry-post", json={"x": 1})
    assert exc_info.value.status_code == 503
    post_reqs = [r for r in fake_server.requests if r.method == "POST" and r.path == "/no-retry-post"]
    assert len(post_reqs) == 1


async def test_get_no_retry_on_401(fake_server: FakeRobotServer) -> None:
    """GET 401 → no retry (auth failures never retryable)."""
    store = [
        RobotResponse(401, b'{"code":401,"message":"unauthorized","data":{}}'),
        RobotResponse(200, b'{"code":200,"message":"would-be-ignored","data":{}}'),
    ]
    fake_server.robot_responses = _PopDict(store)

    async with _client(fake_server) as client:
        with pytest.raises(AuthRejectedError):
            await client.get_model("/auth-fail", _Item)
    assert len(fake_server.robot_gets_for("/auth-fail")) == 1


async def test_get_retry_succeeds_after_503(fake_server: FakeRobotServer) -> None:
    """GET 503 then 200 — retry succeeds on second attempt."""
    store = [
        RobotResponse(503, b'{"code":503,"message":"unavailable","data":{}}'),
        RobotResponse(200, b'{"code":200,"message":"ok","data":{"id":1,"name":"win"}}'),
    ]
    fake_server.robot_responses = _PopDict(store)

    async with _client(fake_server) as client:
        result = await client.get_model("/retry-win", _Item)
    assert result.code == 200
    assert result.data.name == "win"
    assert len(fake_server.robot_gets_for("/retry-win")) == 2


# ── Network failure ───────────────────────────────────────────────────────────


async def test_network_failure_maps_to_robot_api_error(fake_server: FakeRobotServer) -> None:
    """Connect to a closed port → RobotApiError with status_code=0."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    async with _client(fake_server, api_base_url=f"http://127.0.0.1:{port}") as client:
        with pytest.raises(RobotApiError) as exc_info:
            await client.get_model("/anything", _Item)
    assert exc_info.value.status_code == 0
