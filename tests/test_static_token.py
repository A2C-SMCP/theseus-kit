"""Hermetic tests for S3 — StaticTokenSource + RobotClient OAuth path.

Uses the real :class:`FakeRobotServer` from ``tests/_fakeserver.py`` to verify
that ``RobotClient`` with a ``StaticTokenSource`` correctly injects the Bearer
token and X-TF-* routing headers — a complete end-to-end round-trip through
real TCP sockets, per the acceptance bar.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest
from tfrs_auth.client import Token

from theseus_kit import (
    RequestContext,
    RobotClient,
    RobotTarget,
    StaticTokenSource,
)
from theseus_kit.transport import RobotAuth

from ._fakeserver import FakeRobotServer

_CTX = dict(robot_id="robot-1", namespace="default", robot_type="tfrobot")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _h(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup (httpx normalises to lowercase)."""
    return headers.get(name.lower()) or headers.get(name)


# ── Fixture ────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_server() -> Iterator[FakeRobotServer]:
    with FakeRobotServer() as server:
        yield server


# ── StaticTokenSource unit ─────────────────────────────────────────────────


class TestStaticTokenSource:
    """Unit tests for StaticTokenSource — no network."""

    def test_token_returns_token_with_access_token(self) -> None:
        src = StaticTokenSource(access_token="test.jwt.token")
        result = asyncio.run(src.token())
        assert isinstance(result, Token)
        assert result.access_token == "test.jwt.token"
        assert result.token_type == "Bearer"

    def test_token_uses_custom_token_type(self) -> None:
        src = StaticTokenSource(access_token="t", token_type="DPoP")
        result = asyncio.run(src.token())
        assert result.token_type == "DPoP"

    def test_token_default_scope_is_empty(self) -> None:
        src = StaticTokenSource(access_token="t")
        result = asyncio.run(src.token())
        assert result.scope == ""

    def test_token_passes_through_scope(self) -> None:
        src = StaticTokenSource(access_token="t", scope="config:read config:write")
        result = asyncio.run(src.token())
        assert "config:read" in result.scope
        assert "config:write" in result.scope

    def test_token_issued_token_type_is_jwt(self) -> None:
        src = StaticTokenSource(access_token="t")
        result = asyncio.run(src.token())
        assert result.issued_token_type == "urn:ietf:params:oauth:token-type:jwt"

    def test_token_default_expires_at_is_inf(self) -> None:
        src = StaticTokenSource(access_token="t")
        result = asyncio.run(src.token())
        assert result.expires_at == float("inf")

    def test_token_respects_explicit_expires_at(self) -> None:
        src = StaticTokenSource(access_token="t", expires_at=1234567890.0)
        result = asyncio.run(src.token())
        assert result.expires_at == 1234567890.0

    def test_aclose_is_noop(self) -> None:
        src = StaticTokenSource(access_token="t")
        # Should not raise
        asyncio.run(src.aclose())

    def test_frozen_instance_rejects_mutation(self) -> None:
        from dataclasses import FrozenInstanceError

        src = StaticTokenSource(access_token="immutable.token")
        with pytest.raises(FrozenInstanceError):
            src.access_token = "mutated"  # type: ignore[misc]


# ── RobotAuth with StaticTokenSource ────────────────────────────────────────


class TestRobotAuthWithStaticTokenSource:
    """RobotAuth wired with StaticTokenSource — no network, pure header check."""

    async def test_injects_bearer_and_routing_headers(self) -> None:
        src = StaticTokenSource(access_token="my.static.jwt")
        ctx = RequestContext(**_CTX)
        auth = RobotAuth(src, ctx)

        request = httpx.Request("GET", "http://example.com/")
        async for authed in auth.async_auth_flow(request):
            assert authed.headers["Authorization"] == "Bearer my.static.jwt"
            assert authed.headers["X-TF-Namespace"] == "default"
            assert authed.headers["X-TF-RobotId"] == "robot-1"
            assert authed.headers["X-TF-RobotType"] == "tfrobot"

    async def test_robot_auth_accepts_static_token_source_directly(self) -> None:
        """Constructor type annotation accepts StaticTokenSource directly."""
        src = StaticTokenSource(access_token="direct.jwt")
        auth = RobotAuth(src, RequestContext(**_CTX))
        assert auth._token_source is src


# ── RobotClient.for_static_token ────────────────────────────────────────────


class TestRobotClientForStaticToken:
    """RobotClient.for_static_token() factory method."""

    def test_builds_client_with_correct_api_base_url(self) -> None:
        robot = RobotTarget(
            robot_id="r1",
            namespace="ns",
            robot_type="tfrobot",
            api_base_url="https://api.example.com",
            manager_base_url="https://manager.example.com",
        )
        client = RobotClient.for_static_token("my.jwt", robot=robot)
        # Use private attribute to verify — public API doesn't expose base_url.
        assert str(client._client.base_url).rstrip("/") == "https://api.example.com"
        asyncio.run(client.aclose())

    async def test_builds_with_default_verify(self) -> None:
        """for_static_token with default verify=True builds successfully."""
        robot = RobotTarget(
            robot_id="r1",
            namespace="ns",
            robot_type="tfrobot",
            api_base_url="https://api.example.com",
            manager_base_url="https://manager.example.com",
        )
        client = RobotClient.for_static_token("jwt", robot=robot)
        # Construction succeeded + aclose clean.
        await client.aclose()

    def test_accepts_scope_argument(self) -> None:
        robot = RobotTarget(
            robot_id="r1",
            namespace="ns",
            robot_type="tfrobot",
            api_base_url="https://api.example.com",
            manager_base_url="https://manager.example.com",
        )
        client = RobotClient.for_static_token("jwt", robot=robot, scope="config:read")
        assert isinstance(client._token_source, StaticTokenSource)
        assert client._token_source.scope == "config:read"
        asyncio.run(client.aclose())


# ── RobotClient E2E with FakeRobotServer ────────────────────────────────────


class TestRobotClientStaticTokenE2E:
    """End-to-end: RobotClient + StaticTokenSource → real TCP → fake robot."""

    async def test_static_token_client_gets_llms_txt(self, fake_server: FakeRobotServer) -> None:
        """RobotClient with StaticTokenSource retrieves /llms.txt from fake robot."""
        client = RobotClient(
            StaticTokenSource(access_token="e2e.static.jwt"),
            RequestContext(**_CTX),
            api_base_url=fake_server.api_base_url,
        )
        text = await client.get_llms_txt()
        assert "# robot llms.txt" in text
        await client.aclose()

    async def test_static_token_request_carries_routing_headers(
        self,
        fake_server: FakeRobotServer,
    ) -> None:
        """Fake robot receives correct Authorization + X-TF-* from static client."""
        client = RobotClient(
            StaticTokenSource(access_token="routing.test.jwt"),
            RequestContext(**_CTX),
            api_base_url=fake_server.api_base_url,
        )
        await client.get("/llms.txt")

        reqs = fake_server.robot_gets_for("/llms.txt")
        assert len(reqs) >= 1
        headers = reqs[-1].headers
        assert _h(headers, "Authorization") == "Bearer routing.test.jwt"
        assert headers.get("x-tf-namespace") == "default"
        assert headers.get("x-tf-robotid") == "robot-1"
        assert headers.get("x-tf-robottype") == "tfrobot"
        await client.aclose()

    async def test_for_static_token_e2e(self, fake_server: FakeRobotServer) -> None:
        """for_static_token factory → correct routing + Bearer on fake robot."""
        robot = RobotTarget(
            robot_id="r2",
            namespace="prod",
            robot_type="tfrobot",
            api_base_url=fake_server.api_base_url,
            manager_base_url=fake_server.base_url,
        )
        client = RobotClient.for_static_token("factory.e2e.jwt", robot=robot)
        text = await client.get_llms_txt()
        assert "# robot llms.txt" in text

        reqs = fake_server.robot_gets_for("/llms.txt")
        assert len(reqs) >= 1
        h = reqs[-1].headers
        assert _h(h, "Authorization") == "Bearer factory.e2e.jwt"
        assert h.get("x-tf-robotid") == "r2"
        assert h.get("x-tf-namespace") == "prod"
        await client.aclose()

    async def test_get_factory_doc_with_static_token(self, fake_server: FakeRobotServer) -> None:
        """Read-only helper get_factory_doc works with static token."""
        client = RobotClient(
            StaticTokenSource(access_token="doc.jwt"),
            RequestContext(**_CTX),
            api_base_url=fake_server.api_base_url,
        )
        doc = await client.get_factory_doc("my-version")
        assert "factory-doc" in doc
        await client.aclose()

    async def test_context_manager_acloses_cleanly(self, fake_server: FakeRobotServer) -> None:
        """Async context manager with StaticTokenSource closes without error."""
        async with RobotClient(
            StaticTokenSource(access_token="ctx.jwt"),
            RequestContext(**_CTX),
            api_base_url=fake_server.api_base_url,
        ) as client:
            text = await client.get_llms_txt()
            assert "# robot llms.txt" in text

    async def test_multiple_requests_same_token(self, fake_server: FakeRobotServer) -> None:
        """Each request carries the same static token (no rotation)."""
        client = RobotClient(
            StaticTokenSource(access_token="same.old.token"),
            RequestContext(**_CTX),
            api_base_url=fake_server.api_base_url,
        )
        await client.get("/llms.txt")
        await client.get("/v1/factory/llm-docs/v1")

        for req in fake_server.robot_gets():
            assert _h(req.headers, "Authorization") == "Bearer same.old.token"
        await client.aclose()
