"""Skill registry — single source of truth for skill name → resource mapping.

Each skill is defined by a :class:`SkillDef` that bundles the main SKILL.md
and its optional sub-resources (``references/*.md``).  The registry is the
canonical list that ``server.py`` iterates to register MCP resources and
``build_skill_resource()`` resolves against.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SkillDef:
    """Definition of one skill category with its resources.

    *name* is the skill identifier used in URIs and lookup.  *description* is
    a one-line Chinese+English summary shown in MCP resource listings.
    *content* maps ``rel_path`` (e.g. ``"SKILL.md"``, ``"references/foo.md"``)
    to the markdown string body.
    """

    name: str
    description: str
    content: dict[str, str] = field(default_factory=dict)

    @property
    def sub_resources(self) -> list[str]:
        """Return ``rel_path`` entries that are NOT the main ``SKILL.md``."""
        return sorted(k for k in self.content if k != "SKILL.md")


class _Registry:
    """Skill registry — loads skill defs lazily to avoid import-time cost."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDef] | None = None

    def all(self) -> list[SkillDef]:
        """Return every registered skill definition."""
        return list(self._ensure_loaded().values())

    def get(self, name: str) -> SkillDef | None:
        """Look up a skill by *name*."""
        return self._ensure_loaded().get(name)

    def resolve_legacy(self, name: str) -> SkillDef | None:
        """Map an old flat skill name to the new category, or ``None``."""
        alias = _LEGACY_ALIASES.get(name)
        if alias is not None:
            return self.get(alias)
        return None

    def resolve(self, name: str) -> SkillDef:
        """Look up *name* (new or legacy alias), raising ``ValueError``."""
        skill = self.get(name) or self.resolve_legacy(name)
        if skill is not None:
            return skill
        raise ValueError(f"Unknown skill {name!r}. Available: {sorted(self._ensure_loaded())}")

    def _ensure_loaded(self) -> dict[str, SkillDef]:
        if self._skills is None:
            self._skills = _load_all()
        return self._skills


# -- Legacy aliases -----------------------------------------------------------

_LEGACY_ALIASES: dict[str, str] = {
    "inspect-robot-config": "analyze-config",
    "edit-robot-draft": "update-config",
    "publish-robot-config": "publish-config",
}


# -- Lazy loading -------------------------------------------------------------


def _load_all() -> dict[str, SkillDef]:
    """Import and index every skill def from the per-category modules."""
    from . import _analyze_config, _create_config, _publish_config, _save_template, _update_config

    modules = [_analyze_config, _create_config, _update_config, _save_template, _publish_config]
    result: dict[str, SkillDef] = {}
    for mod in modules:
        skill: SkillDef = mod.SKILL
        result[skill.name] = skill
    return result


# Singleton instance.
SkillRegistry = _Registry()
