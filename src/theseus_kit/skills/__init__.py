"""Skill resource loading — architecture layer 4.

Returns static skill guide text for exposure through the ``skill://`` MCP
resources.  Pure data — no cache, no network, no credentials.

Skills are organised as 7 categories, each a marketplace SKILL v1 package:
a main ``SKILL.md`` plus optional ``references/`` and ``scripts/``
sub-resources (A2C-SMCP skill.md §3 mode C — every sub-file is an
independent MCP Resource the Computer stages by rel_path).  Complex skills
carry their content as real package folders (``write-tfonto/`` — files keep
real extensions, scripts stay runnable); simple skills embed markdown in
per-category modules.  The old 3-skill flat structure (``_content.py``) is
deprecated and will be removed in a future release.
"""

from __future__ import annotations

from pathlib import Path

from ._registry import SkillDef, SkillRegistry

# Deterministic built-in extension → MIME mapping (A2C-SMCP skill.md §6.4):
# MUST NOT depend on host OS MIME registries.  The spec's normative baseline
# is the minimal table below; ``.tfo`` is YAML content (RFC 9512) and
# ``text/x-python`` is IANA-registered.
_MIME_BY_EXTENSION: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".tfo": "application/yaml",
    ".toml": "application/toml",
    ".py": "text/x-python",
}


def mime_for_rel_path(rel_path: str) -> str:
    """Return the deterministic MIME type for a skill sub-resource *rel_path*.

    Unknown extensions fall back to ``text/plain`` (safe text default; binary
    assets are not yet used by theseus-kit skills).
    """
    return _MIME_BY_EXTENSION.get(Path(rel_path).suffix.lower(), "text/plain")


def build_skill_resource(skill_name: str, rel_path: str = "SKILL.md") -> str:
    """Return the markdown content for *skill_name* and optional *rel_path*.

    Args:
        skill_name: Skill identifier (e.g. ``"analyze-config"``).  Legacy names
            (``"inspect-robot-config"``, ``"edit-robot-draft"``,
            ``"publish-robot-config"``) are resolved to their new equivalents
            (``"analyze-config"``, ``"tune-config"``, ``"publish-config"``).
        rel_path: Relative path within the skill package.  Defaults to
            ``"SKILL.md"`` (the main entry point).  Sub-resources use paths
            like ``"references/llmtext-strategy.md"``.

    Returns:
        The markdown content as a string.

    Raises:
        ValueError: When *skill_name* or *rel_path* is unknown.
    """
    skill = SkillRegistry.resolve(skill_name)
    try:
        return skill.content[rel_path]
    except KeyError:
        available = sorted(skill.content.keys())
        raise ValueError(
            f"Unknown sub-resource {rel_path!r} for skill {skill.name!r}. Available: {available}"
        ) from None


# Re-export for server.py
__all__ = ["SkillDef", "SkillRegistry", "build_skill_resource", "mime_for_rel_path"]
