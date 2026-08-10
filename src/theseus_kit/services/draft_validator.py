"""Draft configuration validation service.

Calls ``POST /v1/factory/drafts/validate`` on TFRobotServer to verify draft
configuration before publishing.  Supports two modes:

- **Full pre-release check** — omit *setting_id*; the server validates every
  draft in the ROBOT scene.
- **Targeted check** — pass *setting_id*; the server validates that node and
  all of its recursive dependencies.
"""

from __future__ import annotations

from typing import Any

from theseus_kit.models import DraftValidateResponse
from theseus_kit.transport import RobotClient


class DraftValidator:
    """Validate draft configuration before publishing.

    Wraps the ``POST /v1/factory/drafts/validate`` endpoint on TFRobotServer.
    Every call is live — no caching.
    """

    def __init__(self, robot_id: str) -> None:
        self._robot_id = robot_id

    async def validate(
        self,
        client: RobotClient,
        *,
        setting_id: int | None = None,
    ) -> DraftValidateResponse:
        """Validate draft configuration.

        Args:
            client: A configured :class:`RobotClient`.
            setting_id: Optional draft setting ID to validate a specific subtree.
                When omitted (or ``None``), the server performs a full pre-release
                check of all drafts.

        Returns:
            :class:`DraftValidateResponse` with per-node results and a summary
            pass/fail count.  The ``_meta`` block carries ``fetched_at``.
        """
        payload: dict[str, int] = {}
        if setting_id is not None:
            payload["setting_id"] = setting_id

        resp = await client.post(
            "/v1/factory/drafts/validate",
            json=payload,
        )
        body: dict[str, Any] = resp.json()
        code: int = body.get("code", 0)

        from theseus_kit.errors import RobotApiError

        if code != 200:
            raise RobotApiError(
                f"validate endpoint returned code {code}: {body.get('message', '')}",
                status_code=code,
            )

        data: dict[str, Any] = body.get("data", {})

        return DraftValidateResponse(
            valid=data.get("valid", True),
            total_count=data.get("total_count", data.get("totalCount", 0)),
            pass_count=data.get("pass_count", data.get("passCount", 0)),
            fail_count=data.get("fail_count", data.get("failCount", 0)),
            results=data.get("results", []),
        )
