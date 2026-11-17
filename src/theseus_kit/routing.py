"""Robot access address — the canonical Host + ``/c/{type}/{id}`` path.

The Operator gateway derives the tenant namespace from the request Host and the
robot identity from the ``/c/{robotType}/{robotId}`` path.  theseus-kit does
**not** send ``X-TF-*`` routing headers anymore; it only sends the Bearer token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import RoutingConfigError

_ROBOT_PATH_RE = re.compile(r"^/c/(?P<robot_type>tfrobot|openclaw)/(?P<robot_id>[a-z0-9-]+)/?$")


@dataclass(frozen=True, slots=True)
class RobotAddress:
    """A validated canonical robot base URL.

    ``access_url`` is Manager's authoritative ``accessURL``, e.g.
    ``https://acme.test.turingfocus.cn/c/tfrobot/robot-1/``.  Append
    ``/v1/factory/**`` or ``/llms.txt`` to reach TFRobotServer through the
    gateway.
    """

    access_url: str
    robot_id: str
    robot_type: str = "tfrobot"

    def __post_init__(self) -> None:
        parsed = urlsplit(self.access_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise RoutingConfigError(f"access_url must be an absolute http(s) URL; got {self.access_url!r}")
        if parsed.username is not None or parsed.password is not None:
            raise RoutingConfigError("access_url must not contain userinfo")
        if parsed.query or parsed.fragment:
            raise RoutingConfigError("access_url must not contain query or fragment")
        match = _ROBOT_PATH_RE.fullmatch(parsed.path)
        if match is None:
            raise RoutingConfigError(f"access_url path must be /c/{{robotType}}/{{robotId}}/; got {parsed.path!r}")
        if match.group("robot_type") != self.robot_type:
            raise RoutingConfigError(
                f"access_url robotType {match.group('robot_type')!r} != robot_type {self.robot_type!r}"
            )
        if match.group("robot_id") != self.robot_id:
            raise RoutingConfigError(f"access_url robotId {match.group('robot_id')!r} != robot_id {self.robot_id!r}")

    @property
    def base_url(self) -> str:
        """The canonical base URL used as ``httpx.AsyncClient(base_url=...)``."""
        return self.access_url
