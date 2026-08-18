"""Draft deleter — wraps ``RobotClient.delete`` for draft configuration.

Exposes a single ``delete_draft`` entry-point.  TFRobotServer's delete
endpoint cascades: inbound references from other drafts are removed
server-side before the node is deleted, so no dangling references result.

This service lives alongside :class:`DraftCreator` / :class:`DraftEditor`
in the application service layer (architecture layer 2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from theseus_kit.errors import DraftNotFoundError, RobotApiError
from theseus_kit.models import DeleteDraftResponse, ResponseMeta

if TYPE_CHECKING:
    from theseus_kit.transport import RobotClient

_FACTORY_DRAFTS = "/v1/factory/drafts"


class DraftDeleter:
    """Delete TFRobotServer draft configuration nodes.

    Wraps a :class:`RobotClient` and issues ``DELETE
    /v1/factory/drafts/{setting_id}``.  The robot removes inbound
    references from other drafts automatically before deleting the node.

    *robot_id* is stored for interface symmetry with the sibling services.
    """

    def __init__(self, robot_id: str) -> None:
        self._robot_id = robot_id

    async def delete_draft(self, client: RobotClient, *, setting_id: int) -> DeleteDraftResponse:
        """Delete a draft configuration node.

        Args:
            client: An active ``RobotClient``.
            setting_id: The draft's setting ID (from the locator).

        Returns:
            ``DeleteDraftResponse`` confirming the deletion.

        Raises:
            DraftNotFoundError: The gateway answered HTTP 404 (route/API
                version skew).  Note: TFRobotServer currently reports a
                missing draft on this route as HTTP 500 "No result found"
                (surfacing as :class:`RobotApiError`), not as 404 — tracked
                as CNB TFRobotServer #68.
            RobotApiError: The robot reports any failure code.
        """
        path = f"{_FACTORY_DRAFTS}/{setting_id}"

        try:
            response = await client.delete(path)
        except RobotApiError as exc:
            if exc.status_code == 404:
                raise DraftNotFoundError(f"Draft {setting_id} not found (404).") from exc
            raise

        body: dict[str, Any] = response.json()
        code: int = body.get("code", 0)
        if code != 200:
            raise RobotApiError(
                f"robot returned code {code} from DELETE {path}: {body.get('message', '')}",
                status_code=code,
            )

        now = datetime.now(UTC).isoformat()
        return DeleteDraftResponse(setting_id=setting_id, **{"_meta": ResponseMeta(fetched_at=now)})
