"""Skill registry — single source of truth for skill name → resource mapping.

Each skill is defined by a :class:`SkillDef` that bundles the main SKILL.md
and its optional sub-resources (``references/*.md``).  The registry is the
canonical list that ``server.py`` iterates to register MCP resources and
``build_skill_resource()`` resolves against.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path


def _iter_content_files(root_path: Path) -> Iterator[Path]:
    """Yield the text content files of a skill package, in walk order.

    Runtime artifacts are never skill content: ``__pycache__`` (pip
    byte-compiles every ``.py`` at install time, so a wheel install carries
    compiled files a source checkout never has) and hidden files/dirs
    (``.DS_Store`` etc. on developer machines).  Both are matched
    segment-wise, at any nesting depth **below the package root only** —
    ancestor segments are not content, and install layouts like uv's
    ``.venv`` carry dot-prefixed ancestors by design.
    """
    for path in root_path.rglob("*"):
        rel_parts = path.relative_to(root_path).parts
        if path.is_file() and all(not part.startswith(".") and part != "__pycache__" for part in rel_parts):
            yield path


def load_package_skill(package_rel: str, name: str, description: str) -> SkillDef:
    """Load a :class:`SkillDef` whose content lives in a skill package folder.

    *package_rel* is a folder under ``theseus_kit.skills`` following the
    marketplace SKILL v1 package shape — ``SKILL.md`` plus optional
    ``references/`` and ``scripts/`` subfolders.  Every content file is read
    verbatim and becomes a ``rel_path`` content key (runtime artifacts like
    ``__pycache__`` and dotfiles are skipped).  A missing ``SKILL.md``
    raises ``ValueError`` (fail-loud packaging guard).
    """
    from importlib.resources import as_file, files

    root = files("theseus_kit.skills").joinpath(package_rel)
    with as_file(root) as root_path:
        content: dict[str, str] = {}
        for path in _iter_content_files(root_path):
            content[path.relative_to(root_path).as_posix()] = path.read_text(encoding="utf-8")
    if "SKILL.md" not in content:
        raise ValueError(f"Skill package {package_rel!r} is missing SKILL.md — packaging bug?")
    return SkillDef(name=name, description=description, content=content)


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
    "edit-robot-draft": "tune-config",
    "publish-robot-config": "publish-config",
}


# -- Lazy loading -------------------------------------------------------------


def _load_all() -> dict[str, SkillDef]:
    """Import and index every skill def from the per-category modules."""
    from . import (
        _analyze_config,
        _apply_config_plan,
        _enhance,
        _manage_topology,
        _persona_interview,
        _persona_optimize,
        _plan_config,
        _publish_config,
        _save_template,
        _theseus,
        _tune_config,
        _write_tfonto,
    )

    modules = [
        _analyze_config,
        _apply_config_plan,
        _enhance,
        _manage_topology,
        _persona_interview,
        _persona_optimize,
        _plan_config,
        _publish_config,
        _save_template,
        _theseus,
        _tune_config,
        _write_tfonto,
    ]
    result: dict[str, SkillDef] = {}
    for mod in modules:
        skill: SkillDef = mod.SKILL
        result[skill.name] = skill
    return result


# Singleton instance.
SkillRegistry = _Registry()
