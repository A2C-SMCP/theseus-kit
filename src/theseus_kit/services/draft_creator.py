"""Draft configuration creation service.

Calls ``POST /v1/factory/drafts`` on TFRobotServer to create a new draft
configuration setting.  This is the entry point for building new configuration
from scratch — the returned ``content_hash`` can be used immediately in
subsequent ``update_draft`` calls for optimistic concurrency control.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from theseus_kit.models import CreateDraftResponse, ResponseMeta, compute_config_hash
from theseus_kit.transport import RobotClient


class DraftCreator:
    """Create new draft configuration settings.

    Wraps the ``POST /v1/factory/drafts`` endpoint on TFRobotServer.
    Requires ``config:write`` scope.
    """

    def __init__(self, robot_id: str) -> None:
        self._robot_id = robot_id

    async def create(
        self,
        client: RobotClient,
        *,
        scene: str,
        factory_name: str,
        setting_name: str,
        config: dict[str, Any] | None = None,
    ) -> CreateDraftResponse:
        """Create a new draft configuration setting.

        Args:
            client: A configured :class:`RobotClient`.
            scene: Functional domain (e.g. ``"LLM"``, ``"BRAIN"``, ``"TOOL"``).
            factory_name: Factory class name as it appears in the LLMTEXT
                factory catalog (e.g. ``"GLM草稿"``, ``"DeepSeek草稿"``).
            setting_name: User-assigned name for this configuration instance.
            config: Optional initial configuration dictionary. When ``None``
                or empty, the draft is created with default field values from
                the factory schema.

        Returns:
            :class:`CreateDraftResponse` with the new ``setting_id``,
            ``content_hash`` (for optimistic concurrency), and ``_meta``.

        Raises:
            RobotApiError: On server errors (4xx/5xx).
            AuthRejectedError: When scope is insufficient.
        """
        payload: dict[str, Any] = {
            "scene": scene,
            "factoryName": factory_name,
            "settingName": setting_name,
        }
        if config:
            payload["config"] = config

        resp = await client.post(
            "/v1/factory/drafts",
            json=payload,
        )
        body: dict[str, Any] = resp.json()
        code: int = body.get("code", 0)

        from theseus_kit.errors import RobotApiError

        if code != 200:
            raise RobotApiError(
                f"create draft returned code {code}: {body.get('message', '')}",
                status_code=code,
            )

        data: dict[str, Any] = body.get("data", {})
        response_config: dict[str, Any] = data.get("config", {})
        content_hash = compute_config_hash(response_config)
        now = datetime.now(UTC).isoformat()

        return CreateDraftResponse(
            setting_id=data.get("setting_id", data.get("settingId", 0)),
            setting_name=data.get("setting_name", data.get("settingName", "")),
            scene=data.get("scene", ""),
            config=response_config,
            content_hash=content_hash,
            revision=data.get("factory_version", data.get("factoryVersion")),
            **{"_meta": ResponseMeta(fetched_at=now)},
        )
