"""Skill resource loading — architecture layer 4.

Returns static skill guide text for exposure through the ``skill://`` MCP
resources. Pure data — no cache, no network, no credentials.
"""

from __future__ import annotations

from ._content import _SKILLS


def build_skill_resource(skill_name: str) -> str:
    """Return the markdown content for *skill_name*.

    Raises :class:`ValueError` when *skill_name* is unknown.
    """
    try:
        return _SKILLS[skill_name]
    except KeyError:
        raise ValueError(f"Unknown skill {skill_name!r}. Available: {sorted(_SKILLS)}") from None
