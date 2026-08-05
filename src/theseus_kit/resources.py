"""Resource projection layer — architecture layer 4.

Exposes two ``window://`` resources for A2C-SMCP compatibility. No caching —
every read fetches live data from the target robot via
:class:`~theseus_kit.services.config_reader.ConfigReader`.

The only in-process state is a single ``_last_locator`` pointer (the locator
from the most recent :meth:`ConfigReader.get_detail` call). When it is
``None`` the ``config/recent`` resource returns a clear empty-state sentinel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from theseus_kit.services.config_reader import ConfigReader

if TYPE_CHECKING:
    from theseus_kit.transport import RobotClient


_last_locator: str | None = None

_EMPTY_STATE: dict[str, Any] = {
    "available": False,
    "message": "No configuration detail has been opened yet. Use get_config_detail to open one.",
}


def set_last_locator(locator: str | None) -> None:
    """Update (or clear) the most-recently-viewed locator."""
    global _last_locator
    _last_locator = locator


async def build_summary(client: RobotClient, robot_id: str) -> dict[str, Any]:
    """Build the ``config/summary`` resource payload.

    Fetches live state via :meth:`ConfigReader.get_summary` — robot identity
    plus the three-state (draft / template / online) overview.
    """
    reader = ConfigReader(robot_id=robot_id)
    summary = await reader.get_summary(client)
    return summary.model_dump(by_alias=True, mode="json")


async def build_recent(client: RobotClient, robot_id: str) -> dict[str, Any]:
    """Build the ``config/recent`` resource payload.

    When no detail has been opened yet (*_last_locator* is ``None``) this
    returns an explicit empty-state sentinel.  Otherwise it fetches a fresh
    :class:`~theseus_kit.models.ConfigDetail` via
    :meth:`ConfigReader.get_detail` using the stored locator.
    """
    if _last_locator is None:
        return _EMPTY_STATE

    reader = ConfigReader(robot_id=robot_id)
    try:
        detail = await reader.get_detail(client, locator=_last_locator)
    except Exception:
        return {
            "available": False,
            "message": f"Failed to read {_last_locator}. Re-open with get_config_detail.",
        }

    return detail.model_dump(by_alias=True, mode="json")
