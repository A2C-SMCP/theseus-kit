"""Real listening HTTP server for theseus-kit auth/routing tests.

A stdlib :class:`http.server.ThreadingHTTPServer` (no extra dependencies) that
combines a fake Manager token-exchange endpoint (``POST /api/v1/oauth/token``)
with a fake robot on one ephemeral port.  Robot requests are accepted only under
the canonical ``/c/tfrobot/{rid}/...`` path (the fake gateway strips the prefix
before matching TFRobotServer routes), and must carry a Bearer token — mirroring
the new Host/path routing contract.

This is a *real* socket server — httpx makes real TCP round-trips through the
real transport boundary (not respx/ASGITransport), per #17's acceptance bar.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from theseus_kit.tokens import TOKEN_PATH

_ROBOT_PREFIX_RE = re.compile(r"^/c/(?:tfrobot|openclaw)/[a-z0-9-]+")


@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class TokenState:
    """Scriptable behaviour for the fake token endpoint."""

    access_token: str = "test.jwt.token"
    expires_in: int = 300
    scope: str = "config:read"
    # Ordered (status, oauth_error_code) failures drained before a success.
    # Empty => always success. Used for 401/403 and 429/503 retry tests.
    fail_sequence: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class RobotResponse:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


class _Handler(BaseHTTPRequestHandler):
    # Keep default HTTP/1.0 so each response closes the connection — simplest
    # for a threaded test server; httpx handles it transparently.
    def log_message(self, format: str, *args: Any) -> None:
        pass

    @property
    def _state(self) -> FakeRobotServer:
        # `state` is attached dynamically by FakeRobotServer; getattr avoids an
        # imprecise `type: ignore` (ruff B009 is intentional for dynamic access).
        state: object = getattr(self.server, "state")  # noqa: B009
        assert isinstance(state, FakeRobotServer)
        return state

    def _record(self, method: str, body: bytes) -> None:
        # Header names are case-insensitive on the wire (httpx lowercases them);
        # normalise to lowercase so assertions are stable.
        headers = {k.lower(): v for k, v in self.headers.items()}
        self._state.requests.append(RecordedRequest(method, self.path, headers, body))

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        self._record("POST", body)
        if self.path == TOKEN_PATH:
            self._handle_token()
            return
        if not self._check_bearer():
            return
        resp = self._state.robot_responses.get(("POST", self._path_key()))
        if resp is None:
            resp = self._default_response(self._path_key())
        self._send(resp.status, resp.body, resp.content_type, resp.headers)

    def _check_bearer(self) -> bool:
        """Require ``Authorization: Bearer`` on robot requests (gateway/TFRS auth)."""
        if self._state.require_bearer:
            auth = self.headers.get("authorization", "")
            if not auth.lower().startswith("bearer "):
                self._send(401, json.dumps({"error": "missing bearer"}).encode())
                return False
        return True

    def _handle_token(self) -> None:
        state = self._state.token
        if state.fail_sequence:
            status, code = state.fail_sequence.pop(0)
            self._send(status, json.dumps({"error": code}).encode())
            return
        payload = {
            "access_token": state.access_token,
            "token_type": "Bearer",
            "expires_in": state.expires_in,
            "scope": state.scope,
            "issued_token_type": "urn:ietf:params:oauth:token-type:jwt",
        }
        self._send(200, json.dumps(payload).encode())

    def _path_key(self) -> str:
        """Return the upstream path (canonical prefix stripped) without query."""
        return self._state.path_key(self.path)

    def do_GET(self) -> None:
        self._record("GET", b"")
        if not self._check_bearer():
            return
        resp = self._state.robot_responses.get(self._path_key())
        if resp is None:
            resp = self._default_response(self._path_key())
        self._send(resp.status, resp.body, resp.content_type, resp.headers)

    def do_PUT(self) -> None:
        length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        self._record("PUT", body)
        if not self._check_bearer():
            return
        resp = self._state.robot_responses.get(("PUT", self._path_key()))
        if resp is None:
            resp = self._default_response(self._path_key())
        self._send(resp.status, resp.body, resp.content_type, resp.headers)

    def do_DELETE(self) -> None:
        self._record("DELETE", b"")
        if not self._check_bearer():
            return
        resp = self._state.robot_responses.get(("DELETE", self._path_key()))
        if resp is None:
            resp = RobotResponse(204, b"")
        self._send(resp.status, resp.body, resp.content_type, resp.headers)

    @staticmethod
    def _default_response(path: str) -> RobotResponse:
        if path == "/llms.txt":
            return RobotResponse(200, b"# robot llms.txt\n", "text/plain; charset=utf-8")
        if path.startswith("/v1/factory/llm-docs/"):
            return RobotResponse(200, b'{"doc":"factory-doc"}', "application/json")
        return RobotResponse(200, b'{"ok":true}', "application/json")


class FakeRobotServer:
    """Fake Manager + fake robot on one ephemeral port. Use as a context manager."""

    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self.token = TokenState()
        self.robot_responses: dict[str | tuple[str, str], RobotResponse] = {}
        self.require_bearer = True
        self.robot_id = "robot-1"
        self.robot_type = "tfrobot"
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.state = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        host_str = host.decode() if isinstance(host, bytes) else host
        return f"http://{host_str}:{port}"

    @property
    def manager_api_base_url(self) -> str:
        # Token endpoint lives on the same host at /api/v1/oauth/token.
        return self.base_url

    @property
    def access_url(self) -> str:
        """Canonical robot base URL, mirroring Manager's ``accessURL``."""
        return f"{self.base_url}/c/{self.robot_type}/{self.robot_id}/"

    @property
    def api_base_url(self) -> str:
        """Deprecated alias retained for older tests; use :attr:`access_url`."""
        return self.access_url

    @staticmethod
    def path_key(path: str) -> str:
        """Strip the canonical ``/c/{type}/{id}`` gateway prefix and query."""
        path = path.split("?")[0]
        match = _ROBOT_PREFIX_RE.match(path)
        if match:
            stripped = path[len(match.group(0)) :]
            return stripped or "/"
        return path

    def token_posts(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method == "POST" and r.path == TOKEN_PATH]

    def robot_gets(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method == "GET"]

    def robot_gets_for(self, path: str) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method == "GET" and self.path_key(r.path) == path]

    def __enter__(self) -> FakeRobotServer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def parse_form(body: bytes) -> dict[str, str]:
    """Parse a urlencoded form body into a flat dict (last value wins)."""
    from urllib.parse import parse_qsl

    return {k: v for k, v in parse_qsl(body.decode(), keep_blank_values=True)}


def first_value(pairs: Iterable[tuple[str, str]], key: str) -> str | None:
    for k, v in pairs:
        if k == key:
            return v
    return None
