"""Draft editor — wraps ``RobotClient`` write methods for draft configuration.

Exposes a single ``update_draft`` entry-point that implements optimistic
concurrency via content-hash comparison (read-check-write), since
TFRobotServer does not provide ETag / If-Match for conditional writes.

This service lives alongside :class:`ConfigReader` (read) and
:class:`LlmsDocReader` (docs) in the application service layer (architecture
layer 2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from theseus_kit.errors import DraftConflictError, DraftNotFoundError, RobotApiError
from theseus_kit.models import UpdateDraftResponse, compute_config_hash

if TYPE_CHECKING:
    from theseus_kit.transport import RobotClient

_FACTORY_DRAFTS = "/v1/factory/drafts"


class DraftEditor:
    """Update TFRobotServer draft configuration with conflict protection.

    Wraps a :class:`RobotClient` and implements the "read-check-write"
    pattern — reads the current draft, optionally compares a content hash
    for optimistic concurrency, then PUTs the update.

    *robot_id* is stored for interface symmetry with :class:`ConfigReader`
    and may be used in future error messages or audit logging.
    """

    def __init__(self, robot_id: str) -> None:
        self._robot_id = robot_id

    async def update_draft(
        self,
        client: RobotClient,
        *,
        setting_id: int,
        setting_name: str,
        config: dict[str, Any],
        expected_hash: str | None = None,
    ) -> UpdateDraftResponse:
        """Update an existing draft configuration.

        Args:
            client: An active ``RobotClient``.
            setting_id: The draft's setting ID (from the locator).
            setting_name: New human-readable name for the draft.
            config: Config fields to merge into the existing draft config.
            expected_hash: Optional content hash from a prior
                ``get_config_detail`` call.  When provided, the current
                draft is read first and its config hash is compared —
                mismatch raises :class:`DraftConflictError`.

        Returns:
            ``UpdateDraftResponse`` with the updated draft DTO, the new
            content hash (for the next ``expected_hash``), and metadata.
        """
        path = f"{_FACTORY_DRAFTS}/{setting_id}"

        # 1. Compare content hash (optimistic concurrency guard).
        #    Only fetch the current draft when we actually need to compare
        #    — last-write-wins callers skip the extra round-trip.
        if expected_hash is not None:
            current = await client.get_draft_dict(setting_id)
            current_hash = compute_config_hash(current.get("config", {}))
            if current_hash != expected_hash:
                raise DraftConflictError(
                    f"Draft {setting_id} was modified by another actor "
                    f"since it was read (expected hash {expected_hash[:12]}…, "
                    f"current {current_hash[:12]}…). Re-read the draft and retry.",
                    current_hash=current_hash,
                )

        # 2. PUT the update.
        now = datetime.now(UTC).isoformat()
        # TFRobotServer's PUT endpoint expects a flat JSON body whose public
        # field names use camelCase.  ``draftInfo`` is the FastAPI body alias,
        # not an additional JSON wrapper.
        put_body = {"settingName": setting_name, "config": config}
        try:
            response = await client.put(path, json=put_body)
        except RobotApiError as exc:
            if exc.status_code == 404:
                raise DraftNotFoundError(f"Draft {setting_id} not found (404).") from exc
            raise
        body: dict[str, Any] = response.json()
        code: int = body.get("code", 0)

        if code != 200:
            raise RobotApiError(
                f"robot returned code {code} from PUT {path}: {body.get('message', '')}",
                status_code=code,
            )

        data: dict[str, Any] = body.get("data", {})
        updated_config: dict[str, Any] = data.get("config", {})

        # 3. Build response.
        scene = data.get("scene", "")
        factory_name = data.get("name", "")
        locator = f"tcfg:draft/{scene}/{factory_name}/{setting_id}"

        new_hash = compute_config_hash(updated_config)

        return UpdateDraftResponse(
            locator=locator,
            setting_id=setting_id,
            setting_name=data.get("setting_name", data.get("settingName", setting_name)),
            scene=scene,
            config=updated_config,
            content_hash=new_hash,
            revision=data.get("factory_version", data.get("factoryVersion")),
            **{"_meta": {"fetched_at": now}},
        )
