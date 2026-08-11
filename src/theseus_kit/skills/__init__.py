"""Skill resource loading — architecture layer 4.

Returns static skill guide text for exposure through the ``skill://`` MCP
resources.  Pure data — no cache, no network, no credentials.

As of the 2026-08 refactoring, skills are organised as 5 workflow categories
each with a main ``SKILL.md`` and optional ``references/*.md`` sub-resources.
The old 3-skill flat structure (``_content.py``) is deprecated and will be
removed in a future release.
"""

from __future__ import annotations

from ._registry import SkillDef, SkillRegistry


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
__all__ = ["SkillDef", "SkillRegistry", "build_skill_resource"]
