"""Template saver — wraps ``RobotClient`` to save a draft subtree as a template.

Exposes a single ``save_template`` entry-point that implements optimistic
concurrency via content-hash comparison (read-check-write), since
TFRobotServer does not provide ETag / If-Match for conditional saves.

Calls ``POST /v1/factory/drafts/{setting_id}/savetemplate`` with a template
name in the body.  The draft's current config hash is compared against
*expected_hash* (when provided) before POSTing, giving the caller a chance
to detect intervening modifications.

This service lives alongside :class:`ConfigReader` (read),
:class:`DraftEditor` (write), and :class:`ConfigPublisher` (release) in
the application service layer (architecture layer 2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from theseus_kit.errors import DraftConflictError, DraftNotFoundError, RobotApiError
from theseus_kit.models import SaveTemplateResponse, compute_config_hash

if TYPE_CHECKING:
    from theseus_kit.transport import RobotClient

_FACTORY_DRAFTS = "/v1/factory/drafts"


class TemplateSaver:
    """Save a draft subtree as a reusable template.

    Wraps a :class:`RobotClient` and implements the "read-check-write"
    pattern — reads the current draft, optionally compares a content hash
    for optimistic concurrency, then POSTs to the savetemplate endpoint.

    *robot_id* is stored for interface symmetry with :class:`DraftEditor`
    and :class:`ConfigPublisher`.
    """

    def __init__(self, robot_id: str) -> None:
        self._robot_id = robot_id

    async def save_template(
        self,
        client: RobotClient,
        *,
        setting_id: int,
        template_name: str,
        expected_hash: str | None = None,
    ) -> SaveTemplateResponse:
        """Save a draft subtree as a reusable template.

        Args:
            client: An active ``RobotClient``.
            setting_id: The draft setting ID to save as a template.
            template_name: A non-empty name for the new template.
            expected_hash: Optional content hash from a prior
                ``get_config_detail`` call.  When provided, the current
                draft is read first and its config hash is compared —
                mismatch raises :class:`DraftConflictError`.

        Returns:
            ``SaveTemplateResponse`` with the new ``template_id`` and a
            locator suitable for ``get_template``.

        Raises:
            ValueError: *template_name* is empty.
            DraftConflictError: *expected_hash* does not match the current
                draft config hash.
            DraftNotFoundError: The draft *setting_id* does not exist.
        """
        if not template_name.strip():
            raise ValueError("template_name must be a non-empty string.")

        path = f"{_FACTORY_DRAFTS}/{setting_id}"

        # 1. Compare content hash (optimistic concurrency guard).
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

        # 2. POST to savetemplate.
        now = datetime.now(UTC).isoformat()
        save_path = f"{path}/savetemplate"
        post_body = {"name": template_name}
        try:
            response = await client.post(save_path, json=post_body)
        except RobotApiError as exc:
            if exc.status_code == 404:
                raise DraftNotFoundError(f"Draft {setting_id} not found (404).") from exc
            raise
        body: dict[str, Any] = response.json()
        code: int = body.get("code", 0)

        if code != 200:
            raise RobotApiError(
                f"robot returned code {code} from POST {save_path}: {body.get('message', '')}",
                status_code=code,
            )

        data: dict[str, Any] = body.get("data", {})
        template_id: int = data.get("templateId", 0)
        locator = f"tcfg:template/{template_id}"

        return SaveTemplateResponse(
            template_id=template_id,
            locator=locator,
            **{"_meta": {"fetched_at": now}},
        )
