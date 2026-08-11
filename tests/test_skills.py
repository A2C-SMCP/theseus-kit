"""Tests for Issue #10 — skill:// resource projection (refactored 2026-08).

Validates the 5-category hierarchical skill structure: main SKILL.md resources
plus sub-resources (references/*.md). Covers both new URIs and deprecated
legacy aliases.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from theseus_kit.config import TheseusSettings
from theseus_kit.server import create_mcp_server

_SKILL_NS = "skill://com.a2c-smcp.theseus-kit"

# New skill categories
_NEW_SKILLS = (
    "analyze-config",
    "create-config",
    "update-config",
    "save-template",
    "publish-config",
)

# Legacy aliases (deprecated)
_LEGACY_ALIASES = {
    "inspect-robot-config": "analyze-config",
    "edit-robot-draft": "update-config",
    "publish-robot-config": "publish-config",
}

_ALL_SKILL_NAMES = (*_NEW_SKILLS, *_LEGACY_ALIASES)


# -- Helpers ---------------------------------------------------------------


def _settings() -> TheseusSettings:
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
    items = list(result)
    assert len(items) == 1
    content: str | bytes = items[0].content
    if isinstance(content, bytes):
        return content.decode()
    return str(content)


# -- Resource listing ------------------------------------------------------


@pytest.mark.parametrize("skill_name", _NEW_SKILLS)
async def test_each_new_skill_resource_exists(skill_name: str) -> None:
    """Each new skill category has a main resource."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert f"{_SKILL_NS}/{skill_name}" in uris


@pytest.mark.parametrize("legacy_name", _LEGACY_ALIASES)
async def test_each_legacy_skill_resource_exists(legacy_name: str) -> None:
    """Each legacy skill name still resolves."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert f"{_SKILL_NS}/{legacy_name}" in uris


async def test_sub_resources_exist() -> None:
    """Sub-resources (references/*.md) appear as separate MCP resources."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}

    # Check that analyze-config has its sub-resources
    expected_subs = [
        f"{_SKILL_NS}/analyze-config/references/llmtext-strategy.md",
        f"{_SKILL_NS}/analyze-config/references/needs-extraction.md",
        f"{_SKILL_NS}/create-config/references/field-design.md",
        f"{_SKILL_NS}/update-config/references/conflict-resolution.md",
        f"{_SKILL_NS}/update-config/references/validation-strategy.md",
        f"{_SKILL_NS}/save-template/references/template-design.md",
        f"{_SKILL_NS}/publish-config/references/preflight-deep-dive.md",
    ]
    for uri in expected_subs:
        assert uri in uris, f"Missing sub-resource: {uri}"


async def test_resource_count() -> None:
    """Total skill resources is 13 new + 3 legacy = 16 (plus 2 window resources = 18)."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]
    # 5 main + 7 sub + 3 legacy = 15
    assert len(skill_uris) == 15, f"Expected 15 skill resources, got {len(skill_uris)}: {skill_uris}"


# -- Resource annotations ---------------------------------------------------


async def test_main_skill_annotations() -> None:
    """Main skill resources have audience=assistant, priority=0.7, markdown."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()

    for skill_name in _NEW_SKILLS:
        matching = [r for r in resources if str(r.uri) == f"{_SKILL_NS}/{skill_name}"]
        assert len(matching) == 1, f"Missing main resource for {skill_name}"
        r = matching[0]
        assert r.annotations is not None
        assert r.annotations.audience == ["assistant"]
        assert r.annotations.priority == 0.7
        assert r.mimeType == "text/markdown"
        assert r.description, f"{r.uri}: description is empty"


async def test_legacy_resource_annotations() -> None:
    """Legacy resources have lower priority and deprecation metadata."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()

    for legacy_name in _LEGACY_ALIASES:
        matching = [r for r in resources if str(r.uri) == f"{_SKILL_NS}/{legacy_name}"]
        assert len(matching) == 1
        r = matching[0]
        assert r.annotations is not None
        assert r.annotations.priority == 0.6, f"{legacy_name}: priority should be 0.6"
        assert r.meta is not None
        assert r.meta.get("deprecated") is True, f"{legacy_name}: missing deprecated flag"
        assert "migrated_to" in r.meta, f"{legacy_name}: missing migrated_to"


# -- Resource content ------------------------------------------------------


async def test_read_analyze_config() -> None:
    """analyze-config SKILL.md contains key tool references."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/analyze-config")
    content = _read_text(result)

    assert "get_config_summary" in content
    assert "get_llms_doc" in content
    assert "list_config_nodes" in content
    assert "get_config_detail" in content
    assert "llmtext-strategy" in content  # sub-resource reference
    assert "needs-extraction" in content


async def test_read_create_config() -> None:
    """create-config SKILL.md contains create_draft and LLMTEXT integration."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/create-config")
    content = _read_text(result)

    assert "create_draft" in content
    assert "update_draft" in content
    assert "validate_draft" in content
    assert "factory-catalog" in content
    assert "field-design" in content


async def test_read_update_config() -> None:
    """update-config SKILL.md contains update_draft, expected_hash, and sub-resources."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/update-config")
    content = _read_text(result)

    assert "update_draft" in content
    assert "expected_hash" in content
    assert "conflict-resolution" in content
    assert "validation-strategy" in content


async def test_read_save_template() -> None:
    """save-template SKILL.md contains save_template and template design."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/save-template")
    content = _read_text(result)

    assert "save_template" in content
    assert "get_template" in content
    assert "template-design" in content


async def test_read_publish_config() -> None:
    """publish-config SKILL.md contains publish_config, acknowledge_publish, and warnings."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/publish-config")
    content = _read_text(result)

    assert "publish_config" in content
    assert "acknowledge_publish" in content
    assert "config:publish" in content
    assert "不可逆" in content
    assert "preflight-deep-dive" in content


async def test_read_sub_resource() -> None:
    """Each sub-resource can be read independently."""
    mcp = create_mcp_server(_settings())

    sub_uris = [
        f"{_SKILL_NS}/analyze-config/references/llmtext-strategy.md",
        f"{_SKILL_NS}/analyze-config/references/needs-extraction.md",
        f"{_SKILL_NS}/create-config/references/field-design.md",
        f"{_SKILL_NS}/update-config/references/conflict-resolution.md",
        f"{_SKILL_NS}/update-config/references/validation-strategy.md",
        f"{_SKILL_NS}/save-template/references/template-design.md",
        f"{_SKILL_NS}/publish-config/references/preflight-deep-dive.md",
    ]
    for uri in sub_uris:
        result = await mcp.read_resource(uri)
        content = _read_text(result)
        assert len(content) > 0, f"Empty sub-resource: {uri}"


# -- Sensitive-field safety ------------------------------------------------


async def test_skill_content_no_secrets() -> None:
    """No skill content leaks PAT / token / secret values."""
    mcp = create_mcp_server(_settings())

    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]

    for uri in skill_uris:
        result = await mcp.read_resource(uri)
        content = _read_text(result)
        for banned in ("tfp_", "Bearer ", "eyJ", "secret:", "password:"):
            assert banned not in content, f"{uri} leaked: {banned!r}"


# -- URI format ------------------------------------------------------------


async def test_skill_uri_format() -> None:
    """All skill URIs follow the no-query conformance rule."""
    from urllib.parse import urlparse

    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]

    for uri in skill_uris:
        parsed = urlparse(uri)
        assert parsed.query == "", f"{uri} must not contain query parameters"
        assert parsed.scheme == "skill", f"{uri} must use skill:// scheme"


# -- Content validity ------------------------------------------------------


async def test_all_skills_utf8() -> None:
    """All skill content is valid UTF-8 text."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]

    for uri in skill_uris:
        result = await mcp.read_resource(uri)
        content = _read_text(result)
        content.encode("utf-8").decode("utf-8")
        assert len(content) > 0, f"{uri} is empty"


async def test_each_main_skill_within_8kib() -> None:
    """Each main SKILL.md is <= 8 KiB."""
    mcp = create_mcp_server(_settings())

    for skill_name in _NEW_SKILLS:
        result = await mcp.read_resource(f"{_SKILL_NS}/{skill_name}")
        content = _read_text(result)
        size_bytes = len(content.encode("utf-8"))
        assert size_bytes <= 8192, f"{skill_name}: {size_bytes} bytes exceeds 8 KiB"


async def test_each_sub_resource_within_4kib() -> None:
    """Each sub-resource is <= 4 KiB."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    sub_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS) and "/references/" in str(r.uri)]

    for uri in sub_uris:
        result = await mcp.read_resource(uri)
        content = _read_text(result)
        size_bytes = len(content.encode("utf-8"))
        assert size_bytes <= 4096, f"{uri}: {size_bytes} bytes exceeds 4 KiB"


async def test_skill_content_has_frontmatter() -> None:
    """Each main SKILL.md starts with YAML frontmatter (name + description)."""
    mcp = create_mcp_server(_settings())

    for skill_name in _NEW_SKILLS:
        result = await mcp.read_resource(f"{_SKILL_NS}/{skill_name}")
        content = _read_text(result)
        assert content.startswith("---"), f"{skill_name}: missing frontmatter"
        assert "name:" in content, f"{skill_name}: missing name"
        assert "description:" in content, f"{skill_name}: missing description"


async def test_skill_resource_names_are_unique() -> None:
    """No duplicate skill resource URIs."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]
    assert len(skill_uris) == len(set(skill_uris)), f"Duplicates: {sorted(skill_uris)}"


async def test_non_existent_skill_returns_error() -> None:
    """Reading a non-existent skill URI surfaces an error."""
    mcp = create_mcp_server(_settings())
    with pytest.raises(ValueError):
        await mcp.read_resource(f"{_SKILL_NS}/nonexistent-skill")


# -- Legacy backward compatibility -----------------------------------------


async def test_legacy_content_matches_new() -> None:
    """Legacy inspect-robot-config content equals new analyze-config content."""
    mcp = create_mcp_server(_settings())

    legacy = _read_text(await mcp.read_resource(f"{_SKILL_NS}/inspect-robot-config"))
    new = _read_text(await mcp.read_resource(f"{_SKILL_NS}/analyze-config"))
    assert legacy == new


# -- Registry integrity ----------------------------------------------------


async def test_skill_registry_matches_resource_list() -> None:
    """SkillRegistry.all() matches the resources actually registered."""
    from theseus_kit.skills import SkillRegistry

    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()

    # All new skill names in registry should have a resource
    for skill in SkillRegistry.all():
        uri = f"{_SKILL_NS}/{skill.name}"
        assert any(str(r.uri) == uri for r in resources), f"Missing resource for {skill.name}"

        # And all sub-resources
        for rel_path in skill.sub_resources:
            sub_uri = f"{_SKILL_NS}/{skill.name}/{rel_path}"
            assert any(str(r.uri) == sub_uri for r in resources), f"Missing sub-resource: {sub_uri}"


# -- build_skill_resource direct -------------------------------------------


def test_build_skill_resource_main() -> None:
    """Direct call to build_skill_resource returns non-empty markdown."""
    from theseus_kit.skills import build_skill_resource

    content = build_skill_resource("analyze-config")
    assert len(content) > 0
    assert "get_config_summary" in content
    assert content.startswith("---")


def test_build_skill_resource_sub() -> None:
    """build_skill_resource with rel_path returns sub-resource content."""
    from theseus_kit.skills import build_skill_resource

    content = build_skill_resource("analyze-config", "references/llmtext-strategy.md")
    assert len(content) > 0
    assert "LLMTEXT" in content


def test_build_skill_resource_legacy() -> None:
    """build_skill_resource resolves legacy skill names."""
    from theseus_kit.skills import build_skill_resource

    content = build_skill_resource("inspect-robot-config")
    assert len(content) > 0
    assert "get_config_summary" in content


def test_build_skill_resource_unknown_name() -> None:
    """build_skill_resource raises ValueError for unknown names."""
    from theseus_kit.skills import build_skill_resource

    with pytest.raises(ValueError, match="Unknown skill"):
        build_skill_resource("nonexistent-skill")


def test_build_skill_resource_unknown_sub_resource() -> None:
    """build_skill_resource raises ValueError for unknown sub-resource paths."""
    from theseus_kit.skills import build_skill_resource

    with pytest.raises(ValueError, match="Unknown sub-resource"):
        build_skill_resource("analyze-config", "references/nonexistent.md")
