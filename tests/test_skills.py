"""Tests for Issue #10 — skill:// resource projection.

Validates the three ``skill://`` resources via FastMCP's ``list_resources``
and ``read_resource``. Skills are static markdown guides — no robot server
needed.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from theseus_kit.config import TheseusSettings
from theseus_kit.server import create_mcp_server

_SKILL_NS = "skill://com.a2c-smcp.theseus-kit"
_SKILL_NAMES = ("inspect-robot-config", "edit-robot-draft", "publish-robot-config")


# -- Helpers ---------------------------------------------------------------


def _settings() -> TheseusSettings:
    """Build minimal settings (skill resources don't need a real robot)."""
    return TheseusSettings(
        robot={
            "robot_id": "test-robot",
            "namespace": "default",
            "robot_type": "tfrobot",
            "api_base_url": "https://localhost:1",
            "manager_base_url": "https://localhost:1",
        },
        credential={
            "kind": "user_pat",
            "pat": SecretStr("tfp_test_pat"),
            "robot_public_id": "test:1",
        },
    )


def _read_text(result: Any) -> str:
    """Extract text content from ``read_resource`` result."""
    items = list(result)
    assert len(items) == 1
    content: str | bytes = items[0].content
    if isinstance(content, bytes):
        return content.decode()
    return str(content)


# -- Resource listing ------------------------------------------------------


async def test_list_resources_includes_three_skills() -> None:
    """resources/list returns the three skill:// resources."""
    mcp = create_mcp_server(_settings())

    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}

    for skill_name in _SKILL_NAMES:
        assert f"{_SKILL_NS}/{skill_name}" in uris


async def test_skill_resource_annotations() -> None:
    """Each skill resource has correct audience, priority, and mime_type."""
    mcp = create_mcp_server(_settings())

    resources = await mcp.list_resources()
    skill_resources = [r for r in resources if str(r.uri).startswith(_SKILL_NS)]

    assert len(skill_resources) == 3
    for r in skill_resources:
        assert r.annotations is not None
        assert r.annotations.audience == ["assistant"]
        assert r.annotations.priority == 0.7
        assert r.mimeType == "text/markdown"
        # Description must be non-empty (guard against silent
        # empty-description regression when adding a new skill).
        assert r.description, f"{r.uri}: description is empty"


async def test_skill_resource_meta() -> None:
    """Each skill resource has _meta.source == 'resources'."""
    mcp = create_mcp_server(_settings())

    resources = await mcp.list_resources()
    skill_resources = [r for r in resources if str(r.uri).startswith(_SKILL_NS)]

    for r in skill_resources:
        assert r.meta is not None
        assert r.meta.get("source") == "resources", f"{r.uri}: missing source=resources in meta"


# -- Resource content ------------------------------------------------------


async def test_read_inspect_skill() -> None:
    """inspect-robot-config contains key tool references and guidance."""
    mcp = create_mcp_server(_settings())

    result = await mcp.read_resource(f"{_SKILL_NS}/inspect-robot-config")
    content = _read_text(result)

    assert "get_config_summary" in content
    assert "list_config_nodes" in content
    assert "get_config_detail" in content
    assert "get_llms_doc" in content
    assert "llms.txt" in content
    assert "config:read" in content


async def test_read_edit_skill() -> None:
    """edit-robot-draft contains update_draft, expected_hash, and llms.txt."""
    mcp = create_mcp_server(_settings())

    result = await mcp.read_resource(f"{_SKILL_NS}/edit-robot-draft")
    content = _read_text(result)

    assert "update_draft" in content
    assert "expected_hash" in content
    assert "get_llms_doc" in content
    assert "llms.txt" in content
    assert "config:write" in content
    assert "save_template" in content


async def test_read_publish_skill() -> None:
    """publish-robot-config contains publish_config, acknowledge_publish, pre-check."""
    mcp = create_mcp_server(_settings())

    result = await mcp.read_resource(f"{_SKILL_NS}/publish-robot-config")
    content = _read_text(result)

    assert "publish_config" in content
    assert "acknowledge_publish" in content
    assert "config:publish" in content
    assert "不可逆" in content


# -- Sensitive-field safety ------------------------------------------------


async def test_skill_content_no_secrets() -> None:
    """No skill content leaks PAT / token / secret values."""
    mcp = create_mcp_server(_settings())

    for skill_name in _SKILL_NAMES:
        result = await mcp.read_resource(f"{_SKILL_NS}/{skill_name}")
        content = _read_text(result)

        for banned in ("tfp_", "Bearer ", "eyJ", "secret:", "password:"):
            assert banned not in content, f"{skill_name} leaked: {banned!r}"


# -- URI format ------------------------------------------------------------


async def test_skill_uri_format() -> None:
    """All skill:// URIs follow the no-query conformance rule."""
    from urllib.parse import urlparse

    for skill_name in _SKILL_NAMES:
        uri = f"{_SKILL_NS}/{skill_name}"
        parsed = urlparse(uri)
        assert parsed.query == "", f"{uri} must not contain query parameters"
        assert parsed.scheme == "skill", f"{uri} must use skill:// scheme"


# -- Content validity ------------------------------------------------------


async def test_all_skills_utf8() -> None:
    """All skill content is valid UTF-8 text."""
    mcp = create_mcp_server(_settings())

    for skill_name in _SKILL_NAMES:
        result = await mcp.read_resource(f"{_SKILL_NS}/{skill_name}")
        content = _read_text(result)
        # Encode/decode as UTF-8 round-trip; any non-UTF-8 would raise.
        content.encode("utf-8").decode("utf-8")
        assert len(content) > 0, f"{skill_name} is empty"


async def test_skill_content_size_limit() -> None:
    """Each skill is ≤ 32 KiB."""
    mcp = create_mcp_server(_settings())

    for skill_name in _SKILL_NAMES:
        result = await mcp.read_resource(f"{_SKILL_NS}/{skill_name}")
        content = _read_text(result)
        size_bytes = len(content.encode("utf-8"))
        assert size_bytes <= 32768, f"{skill_name}: {size_bytes} bytes exceeds 32 KiB limit"


async def test_skill_content_has_frontmatter() -> None:
    """Each skill starts with YAML-like frontmatter (name + description)."""
    mcp = create_mcp_server(_settings())

    for skill_name in _SKILL_NAMES:
        result = await mcp.read_resource(f"{_SKILL_NS}/{skill_name}")
        content = _read_text(result)

        assert content.startswith("---"), f"{skill_name}: missing frontmatter delimiter"
        assert "name:" in content, f"{skill_name}: missing name in frontmatter"
        assert "description:" in content, f"{skill_name}: missing description in frontmatter"


async def test_skill_resource_names_are_unique() -> None:
    """No duplicate skill resource URIs."""
    mcp = create_mcp_server(_settings())

    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]

    assert len(skill_uris) == len(set(skill_uris)), "Duplicate skill URIs found"


async def test_non_existent_skill_returns_error() -> None:
    """Reading a non-existent skill:// resource surfaces an error."""
    mcp = create_mcp_server(_settings())

    with pytest.raises(ValueError):
        await mcp.read_resource(f"{_SKILL_NS}/nonexistent-skill")


# -- Registry integrity ----------------------------------------------------


async def test_skill_names_match_content_keys() -> None:
    """_SKILL_NAMES in server.py matches _SKILLS keys in _content.py.

    Adding a skill to one registry but not the other is a silent integration
    bug — the resource would be registered with no content, or content would
    be written but never exposed. This test cross-validates them.
    """
    from theseus_kit.server import _SKILL_NAMES as registered_names
    from theseus_kit.skills._content import _SKILLS as content_skills

    assert set(registered_names) == set(content_skills.keys()), (
        f"Mismatch: _SKILL_NAMES={set(registered_names)} vs _SKILLS.keys()={set(content_skills.keys())}"
    )


async def test_skill_descriptions_match_names() -> None:
    """_SKILL_DESCRIPTIONS covers all names in _SKILL_NAMES.

    Adding a skill to _SKILL_NAMES but not _SKILL_DESCRIPTIONS causes a
    startup KeyError — catching it at test time is friendlier.
    """
    from theseus_kit.server import _SKILL_DESCRIPTIONS
    from theseus_kit.server import _SKILL_NAMES as registered_names

    assert set(registered_names) == set(_SKILL_DESCRIPTIONS.keys()), (
        f"Mismatch: _SKILL_NAMES={set(registered_names)} vs "
        f"_SKILL_DESCRIPTIONS.keys()={set(_SKILL_DESCRIPTIONS.keys())}"
    )


# -- build_skill_resource direct -------------------------------------------


def test_build_skill_resource_direct() -> None:
    """Direct call to build_skill_resource returns non-empty markdown."""
    from theseus_kit.skills import build_skill_resource

    content = build_skill_resource("inspect-robot-config")
    assert len(content) > 0
    assert "get_config_summary" in content
    assert content.startswith("---")


def test_build_skill_resource_unknown_name() -> None:
    """build_skill_resource raises ValueError for unknown skill names."""
    from theseus_kit.skills import build_skill_resource

    with pytest.raises(ValueError, match="Unknown skill"):
        build_skill_resource("nonexistent-skill")
