"""Real listening HTTP server for theseus-kit auth/routing tests.

A stdlib :class:`http.server.ThreadingHTTPServer` (no extra dependencies) that
combines a fake Manager token-exchange endpoint (``POST /api/v1/oauth/token``)
with a fake robot (``/llms.txt``, ``/v1/factory/llm-docs/**``, arbitrary GET
paths) on one ephemeral port. It records every received request so tests can
assert the on-the-wire exchange form and the injected X-TF-* headers, and it
enforces the gateway contract (missing routing header => 400) so a theseus-kit
omission fails loudly rather than silently.

This is a *real* socket server — httpx makes real TCP round-trips through the
real transport boundary (not respx/ASGITransport), per #17's acceptance bar.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from theseus_kit.tokens import TOKEN_PATH

ROUTING_HEADERS = ("X-TF-Namespace", "X-TF-RobotId", "X-TF-RobotType")


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
        if not self._check_routing_headers():
            return
        resp = self._state.robot_responses.get(("POST", self._path_key()))
        if resp is None:
            resp = self._default_response(self._path_key())
        self._send(resp.status, resp.body, resp.content_type, resp.headers)

    def _check_routing_headers(self) -> bool:
        """Check X-TF-* headers; return False (and send 400) if missing."""
        if self._state.require_routing_headers:
            missing = [h for h in ROUTING_HEADERS if h not in self.headers]
            if missing:
                self._send(
                    400,
                    json.dumps({"error": "missing routing context", "missing": missing}).encode(),
                )
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
        """Return the path without query string for response lookup."""
        return self.path.split("?")[0]

    def do_GET(self) -> None:
        self._record("GET", b"")
        if not self._check_routing_headers():
            return
        resp = self._state.robot_responses.get(self._path_key())
        if resp is None:
            resp = self._default_response(self._path_key())
        self._send(resp.status, resp.body, resp.content_type, resp.headers)

    def do_PUT(self) -> None:
        length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        self._record("PUT", body)
        if not self._check_routing_headers():
            return
        resp = self._state.robot_responses.get(("PUT", self._path_key()))
        if resp is None:
            resp = self._default_response(self._path_key())
        self._send(resp.status, resp.body, resp.content_type, resp.headers)

    def do_DELETE(self) -> None:
        self._record("DELETE", b"")
        if not self._check_routing_headers():
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
        self.require_routing_headers = True
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
    def manager_base_url(self) -> str:
        # Token endpoint lives on the same host at /api/v1/oauth/token.
        return self.base_url

    @property
    def api_base_url(self) -> str:
        return self.base_url

    def token_posts(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method == "POST" and r.path == TOKEN_PATH]

    def robot_gets(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method == "GET"]

    def robot_gets_for(self, path: str) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method == "GET" and r.path == path]

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
