"""Request routing context — the single place that produces the X-TF-* headers.

tfrs-operator confirmed (2026-07-22): on the ``api.<clusterDomain>`` entry the
three headers ``X-TF-Namespace`` / ``X-TF-RobotId`` / ``X-TF-RobotType`` are
**mandatory** (missing any => 400) and their values must match ``^[a-z0-9-]+$``
(the gateway refuses to build a malformed in-cluster DNS name). theseus-kit
never self-derives these values — they come from explicit config.

Note: ``X-TF-RobotId`` carries the **rid**, which is a *different* identifier
from the numeric Account.ID used as the token ``audience`` (``robot:<id>``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import RoutingConfigError

_ROUTING_VALUE_RE = re.compile(r"^[a-z0-9-]+$")


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Routing metadata for one target robot.

    Fields are the three X-TF-* header sources plus nothing else: the token
    ``audience`` (numeric Account.ID) is a *credential* concern, not routing,
    so it does not live here.
    """

    robot_id: str
    namespace: str
    robot_type: str

    def __post_init__(self) -> None:
        # Validate once at construction: values are immutable, and a bad config
        # should fail immediately rather than on the first robot request.
        _validate("robot_id", self.robot_id)
        _validate("namespace", self.namespace)
        _validate("robot_type", self.robot_type)

    def routing_headers(self) -> dict[str, str]:
        """Produce the three mandatory X-TF-* routing headers."""
        return {
            "X-TF-Namespace": self.namespace,
            "X-TF-RobotId": self.robot_id,
            "X-TF-RobotType": self.robot_type,
        }


def _validate(field: str, value: str) -> None:
    """Reject values the gateway would 400 on, rather than discovering at runtime."""
    if not value or not _ROUTING_VALUE_RE.match(value):
        raise RoutingConfigError(f"routing field {field!r} must match ^[a-z0-9-]+$; got {value!r}")
