"""DEPRECATED — old flat skill guides replaced by per-category modules.

Kept for backward compatibility during the transition window.  The actual
content now lives in ``_analyze_config.py``, ``_create_config.py``,
``_update_config.py``, ``_save_template.py``, and ``_publish_config.py``.

This module will be removed in a future release.
"""

from __future__ import annotations

import warnings

from . import build_skill_resource

_SKILLS: dict[str, str] = {}


def _build_legacy() -> dict[str, str]:
    """Lazily build the legacy dict by delegating to the new registry."""
    legacy_aliases = {
        "inspect-robot-config": "analyze-config",
        "edit-robot-draft": "update-config",
        "publish-robot-config": "publish-config",
    }
    result: dict[str, str] = {}
    for old_name, new_name in legacy_aliases.items():
        warnings.warn(
            f"Skill '{old_name}' is deprecated; use '{new_name}' instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        try:
            result[old_name] = build_skill_resource(new_name)
        except Exception:
            result[old_name] = (
                f"---\nname: {old_name}\ndescription: DEPRECATED — use {new_name}\n---\n\n"
                f"# {old_name} (已废弃)\n\n"
                f"此技能已被 **{new_name}** 替代。请使用新的分层技能结构。\n"
            )
    return result


# Populate lazily on first access (kept as module-level dict for backward compat
# with any code that does ``from _content import _SKILLS``).
_SKILLS = _build_legacy()
