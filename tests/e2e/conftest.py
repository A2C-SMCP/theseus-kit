"""Fixtures for the SDK-driven e2e suite — real A2C-SMCP trio, no mocks.

Assembles the same real-process chain the a2c-smcp SDK's own e2e suite uses
(see its tests/e2e/conftest.py): a real Socket.IO signaling server (sync
namespace + permissive auth, wrapped by the real protocol-version WSGI
middleware so the ``a2c_version`` handshake is production-equivalent), served
by werkzeug in a separate process.  The Computer (real MCP stdio subprocess
running the kit) and the Agent (async client) connect to it over real HTTP +
WebSocket/polling.

Also provides an in-process fake robot (``tests._fakeserver``) that the kit
subprocess reaches over real TCP for its credential token-exchange and
``/llms.txt`` reads.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import socket
import time
from collections.abc import Iterator
from multiprocessing.synchronize import Event
from typing import Any

import pytest
from a2c_smcp import PROTOCOL_VERSION
from a2c_smcp.server import SyncSMCPNamespace
from a2c_smcp.server.middleware import A2CProtocolVersionWSGIMiddleware
from a2c_smcp.server.sync_auth import SyncAuthenticationProvider
from socketio import Server, WSGIApp
from werkzeug.serving import make_server

from tests._fakeserver import FakeRobotServer

SIO_PATH = "/socket.io"


class _PassSyncAuth(SyncAuthenticationProvider):
    """Permissive auth for the local test signaling server."""

    def authenticate(
        self, sio: Server, environ: dict[str, Any], auth: dict[str, Any] | None, headers: list[Any]
    ) -> bool:
        return True


class LocalSyncSMCPNamespace(SyncSMCPNamespace):
    """Real sync namespace, only the auth provider swapped for tests."""

    def __init__(self) -> None:
        super().__init__(auth_provider=_PassSyncAuth())


def create_local_sync_server() -> WSGIApp:
    """Sync Socket.IO server wrapped by the real version-handshake middleware."""
    sio = Server(
        cors_allowed_origins="*",
        ping_timeout=60,
        ping_interval=25,
        # Required for the `call` relay pattern (mirrors SDK's own conftest).
        async_handlers=True,
        always_connect=True,
    )
    sio.register_namespace(LocalSyncSMCPNamespace())
    wsgi_app = WSGIApp(sio, socketio_path=SIO_PATH)
    return A2CProtocolVersionWSGIMiddleware(wsgi_app, socketio_path=SIO_PATH, server_version=PROTOCOL_VERSION)


def _run_server_process(port: int, ready_event: Event) -> None:
    """Target for the signaling-server child process."""
    try:
        sio_app = create_local_sync_server()
        # The middleware wraps WSGIApp; the engine's service task must be off
        # for clean shutdown (mirrors SDK's own conftest).
        server = make_server("127.0.0.1", port, sio_app, threaded=True)
        ready_event.set()
        server.serve_forever()
    except Exception as exc:  # pragma: no cover - only on child failure
        print(f"signaling server process failed: {exc}")
        ready_event.set()


@contextlib.contextmanager
def run_http_server() -> Iterator[str]:
    """Start the signaling server in a real process; yield its base URL."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])

    ready_event = multiprocessing.Event()
    proc = multiprocessing.Process(target=_run_server_process, args=(port, ready_event), daemon=True)
    proc.start()
    try:
        if not ready_event.wait(timeout=15):
            raise RuntimeError("signaling server process startup timed out")
        time.sleep(0.3)  # let the listener settle
        yield f"http://127.0.0.1:{port}"
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=3)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=1)


@pytest.fixture
def signaling_endpoint() -> Iterator[str]:
    """Base URL of the real-process A2C-SMCP signaling server."""
    with run_http_server() as base:
        yield base


@pytest.fixture
def fake_robot() -> Iterator[FakeRobotServer]:
    """In-process fake Manager+robot the kit subprocess reaches over TCP."""
    with FakeRobotServer() as server:
        yield server


@pytest.fixture
def kit_env(signaling_endpoint: str, fake_robot: FakeRobotServer, monkeypatch: pytest.MonkeyPatch) -> None:
    """Export THESEUS_* settings for the kit subprocess (inherits parent env)."""
    base = fake_robot.base_url
    monkeypatch.setenv("THESEUS_ROBOT__ROBOT_ID", "e2e-robot")
    monkeypatch.setenv("THESEUS_ROBOT__NAMESPACE", "e2e-ns")
    monkeypatch.setenv("THESEUS_ROBOT__ROBOT_TYPE", "tfrobot")
    monkeypatch.setenv("THESEUS_ROBOT__API_BASE_URL", base)
    monkeypatch.setenv("THESEUS_ROBOT__MANAGER_BASE_URL", base)
    monkeypatch.setenv("THESEUS_CREDENTIAL__KIND", "user_pat")
    monkeypatch.setenv("THESEUS_CREDENTIAL__PAT", "tfp_e2e_test_pat")
    monkeypatch.setenv("THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID", "e2eorg:10001")
