"""Configuration publisher — POST ``/v1/factory/drafts/release`` with
pre-publish guards.

Exposes ``publish_config`` with two client-side pre-publish gates:

1. **Explicit acknowledgement** — ``acknowledge_publish`` must be ``True``.
   Publishing is global and irreversible; the flag forces the caller to
   confirm it understood the consequences.

2. **Root-hash comparison** — when ``expected_root_hash`` is provided, the
   current draft scene list is read and its deterministic hash is compared.
   Mismatch proves the draft structure changed since the caller last read it
   and refuses the publish with :class:`PublishPreCheckError`.

The actual ``POST /v1/factory/drafts/release`` carries no request body
(TFRobotServer locates the ROBOT-scene draft and cascades the release across
the full configuration tree).  The call uses :meth:`RobotClient.post`, which
never retries — publish mutations are not idempotent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from theseus_kit.errors import PublishNotConfirmedError, PublishPreCheckError, RobotApiError
from theseus_kit.models import PublishConfigResponse, compute_config_hash

if TYPE_CHECKING:
    from theseus_kit.transport import RobotClient

_RELEASE_PATH = "/v1/factory/drafts/release"
_DRAFT_SCENES_PATH = "/v1/factory/drafts/scenes"


class ConfigPublisher:
    """Publish draft configuration to the online state.

    Wraps a :class:`RobotClient` and implements pre-publish guards
    (acknowledgement + root-hash comparison) before POSTing to the
    TFRobotServer release endpoint.

    *robot_id* is stored for interface symmetry with :class:`ConfigReader`
    and :class:`DraftEditor`.
    """

    def __init__(self, robot_id: str) -> None:
        self._robot_id = robot_id

    async def publish_config(
        self,
        client: RobotClient,
        *,
        expected_root_hash: str | None = None,
        acknowledge_publish: bool = False,
    ) -> PublishConfigResponse:
        """Publish all draft configuration to the online state.

        Args:
            client: An active ``RobotClient``.
            expected_root_hash: Optional hash of the draft scene list from a
                prior read.  When provided, the current scene list is fetched
                and hashed — mismatch raises :class:`PublishPreCheckError`.
            acknowledge_publish: Must be ``True`` to proceed.  This flag is
                the caller's explicit confirmation that it understands
                publishing is a global, irreversible side-effect.

        Returns:
            ``PublishConfigResponse`` with the ``online_robot_id`` of the
            newly published ROBOT-scene configuration.

        Raises:
            PublishNotConfirmedError: *acknowledge_publish* is not ``True``.
            PublishPreCheckError: *expected_root_hash* does not match the
                current draft scene list hash.
        """
        # 1. Explicit confirmation gate.
        if not acknowledge_publish:
            raise PublishNotConfirmedError(
                "Publishing draft configuration is a global, irreversible "
                "side-effect. Set acknowledge_publish=True to confirm you "
                "understand the consequences."
            )

        # 2. Root-hash comparison (optional optimistic-concurrency guard).
        if expected_root_hash is not None:
            current_root_hash = await self._compute_root_hash(client)
            if current_root_hash != expected_root_hash:
                raise PublishPreCheckError(
                    f"The draft root structure has changed since you last "
                    f"read it (expected hash {expected_root_hash[:12]}…, "
                    f"current {current_root_hash[:12]}…). Re-read the draft "
                    f"state and retry."
                )

        # 3. Publish — no body, no retry.
        now = datetime.now(UTC).isoformat()
        response = await client.post(_RELEASE_PATH, scope_hint="config:publish")
        body: dict[str, Any] = response.json()
        code: int = body.get("code", 0)

        if code != 200:
            raise RobotApiError(
                f"robot returned code {code} from POST {_RELEASE_PATH}: {body.get('message', '')}",
                status_code=code,
            )

        data: dict[str, Any] = body.get("data", {})
        online_robot_id: int = data.get("onlineRobotId", 0)

        return PublishConfigResponse(
            online_robot_id=online_robot_id,
            locator="tcfg:online",
            **{"_meta": {"fetched_at": now}},
        )

    # -- internal ----------------------------------------------------------

    @staticmethod
    async def _compute_root_hash(client: RobotClient) -> str:
        """Fetch the draft scene list and compute a deterministic hash.

        The hash covers the sorted list of scene names — a lightweight
        fingerprint that proves the caller has seen the current draft
        structure.
        """
        response = await client.get(_DRAFT_SCENES_PATH)
        body: dict[str, Any] = response.json()
        code: int = body.get("code", 0)
        if code != 200:
            raise RobotApiError(
                f"robot returned code {code} from GET {_DRAFT_SCENES_PATH}: {body.get('message', '')}",
                status_code=code,
            )
        raw_data = body.get("data", [])
        scenes: list[str] = sorted(raw_data) if isinstance(raw_data, list) else []
        return compute_config_hash({"scenes": scenes})
