"""Real-HTTP tests for Issue #8 — llms.txt documentation reader.

Drives :class:`LlmsDocReader` against :class:`FakeRobotServer` (real TCP
socket, per project acceptance bar).  Covers index retrieval, doc retrieval,
path security, content-budget truncation, and edge cases.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from theseus_kit import (
    ClientCredentialsConfig,
    LlmsDocError,
    RobotClient,
)
from theseus_kit.routing import RequestContext
from theseus_kit.services.llms_doc_reader import LlmsDocReader
from theseus_kit.tokens import build_token_source

from ._fakeserver import FakeRobotServer, RobotResponse

_CTX = dict(robot_id="robot-1", namespace="default", robot_type="tfrobot")


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


def _reader() -> LlmsDocReader:
    return LlmsDocReader()


# -- Index (path="") -------------------------------------------------------


async def test_get_index(fake: FakeRobotServer) -> None:
    """path="" fetches /llms.txt, is_index=True."""
    async with _client(fake) as client:
        result = await _reader().get_doc(client, path="")

    assert result.path == ""
    assert result.is_index is True
    assert "# robot llms.txt" in result.content
    assert result.truncated is False
    assert result.bytes_returned > 0
    assert result.bytes_total == result.bytes_returned


# -- Doc (path="schema/main") ----------------------------------------------


async def test_get_doc(fake: FakeRobotServer) -> None:
    """path="schema/main" fetches /v1/factory/llm-docs/schema/main."""
    async with _client(fake) as client:
        result = await _reader().get_doc(client, path="schema/main")

    assert result.path == "schema/main"
    assert result.is_index is False
    assert "factory-doc" in result.content
    assert result.truncated is False


# -- Path security ---------------------------------------------------------


async def test_rejects_parent_traversal(fake: FakeRobotServer) -> None:
    """Paths with '..' raise LlmsDocError."""
    async with _client(fake) as client:
        with pytest.raises(LlmsDocError, match="'..'"):
            await _reader().get_doc(client, path="../secret")


async def test_rejects_percent_encoded_traversal(fake: FakeRobotServer) -> None:
    """URL-encoded '..' (%2e%2e%2f) is rejected."""
    async with _client(fake) as client:
        with pytest.raises(LlmsDocError, match=r"%2e%2e"):
            await _reader().get_doc(client, path="%2e%2e%2fsecret")


async def test_rejects_absolute_path(fake: FakeRobotServer) -> None:
    """Paths starting with '/' (not under allowed prefix) raise LlmsDocError."""
    async with _client(fake) as client:
        with pytest.raises(LlmsDocError, match="must be relative"):
            await _reader().get_doc(client, path="/etc/passwd")


async def test_rejects_url(fake: FakeRobotServer) -> None:
    """Paths containing '://' raise LlmsDocError."""
    async with _client(fake) as client:
        with pytest.raises(LlmsDocError, match="looks like a URL"):
            await _reader().get_doc(client, path="http://evil.com/x")


# -- Truncation ------------------------------------------------------------


async def test_truncation(fake: FakeRobotServer) -> None:
    """Large doc with small max_bytes → truncated=True."""
    async with _client(fake) as client:
        result = await _reader().get_doc(client, path="", max_bytes=10)

    assert result.truncated is True
    assert result.bytes_returned <= 10  # strict: never exceeds budget
    assert result.bytes_total is not None
    assert result.bytes_total > result.bytes_returned


async def test_no_truncation_when_under_budget(fake: FakeRobotServer) -> None:
    """Content within budget is not truncated."""
    async with _client(fake) as client:
        result = await _reader().get_doc(client, path="", max_bytes=32768)

    assert result.truncated is False
    assert result.bytes_returned == result.bytes_total


async def test_truncation_non_index_path(fake: FakeRobotServer) -> None:
    """Truncation works for non-empty path (get_factory_doc code path)."""
    async with _client(fake) as client:
        result = await _reader().get_doc(client, path="schema/main", max_bytes=10)

    assert result.truncated is True
    assert result.bytes_returned <= 10


async def test_truncation_multibyte_boundary(fake: FakeRobotServer) -> None:
    """Truncation at a multi-byte codepoint boundary does not exceed budget."""
    # Content where byte 10 cuts through a multi-byte CJK character.
    # "hello世界" = 5 ascii + 6 bytes CJK = 11 bytes total.
    # budget=8 cuts into the CJK chars.
    fake.robot_responses["/llms.txt"] = RobotResponse(200, "hello世界世界".encode(), "text/plain; charset=utf-8")
    async with _client(fake) as client:
        result = await _reader().get_doc(client, path="", max_bytes=8)

    assert result.truncated is True
    assert result.bytes_returned <= 8  # strict: never exceeds budget
    assert "hello" in result.content


# -- Edge cases ------------------------------------------------------------


async def test_max_bytes_clamped(fake: FakeRobotServer) -> None:
    """max_bytes > 32768 is clamped, max_bytes <= 0 is clamped to 1."""
    async with _client(fake) as client:
        # Over max — clamped to 32768.
        result = await _reader().get_doc(client, path="", max_bytes=99999)
        assert result.truncated is False  # default doc is small

        # Zero — clamped to 1.
        result2 = await _reader().get_doc(client, path="", max_bytes=0)
        assert result2.truncated is True

        # Negative — clamped to 1.
        result3 = await _reader().get_doc(client, path="", max_bytes=-1)
        assert result3.truncated is True


async def test_meta_fields(fake: FakeRobotServer) -> None:
    """Response carries _meta with fetched_at."""
    async with _client(fake) as client:
        result = await _reader().get_doc(client, path="")

    assert result.meta.fetched_at
    assert "T" in result.meta.fetched_at  # ISO 8601


async def test_doc_with_prefix(fake: FakeRobotServer) -> None:
    """Path starting with /v1/factory/llm-docs/ works (get_factory_doc handles it)."""
    async with _client(fake) as client:
        result = await _reader().get_doc(client, path="/v1/factory/llm-docs/schema/main")

    assert "factory-doc" in result.content
    assert result.is_index is False
