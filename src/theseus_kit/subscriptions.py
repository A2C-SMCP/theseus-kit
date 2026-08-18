"""A2C-SMCP Desktop Resource subscription support.

Declares the ``resources.subscribe`` capability (mandatory for Desktop
participation per the A2C-SMCP desktop spec) and tracks per-session
subscriptions so ``notifications/resources/updated`` only go to sessions
that subscribed (plus the session that caused the change).

The official mcp Python SDK (<2.0) ships the subscribe/unsubscribe request
routing and decorators on ``Server``, but ``FastMCP`` registers no handlers
by default and ``Server.get_capabilities`` hardcodes ``subscribe=False``.
This module patches both gaps on a :class:`FastMCP` subclass.  All
private-API access is confined to this module; the tests assert the wire
behaviour so a future SDK upgrade that breaks the assumptions fails loudly.
"""

from __future__ import annotations

from typing import Any
from weakref import WeakKeyDictionary

from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.server import NotificationOptions, request_ctx
from mcp.server.session import ServerSession
from pydantic import AnyUrl


class SubscriptionRegistry:
    """Session -> set of subscribed window URI keys.

    Weak keys: a dead session is dropped automatically, no cleanup hook.
    Mutations only happen inside MCP request handlers on a single event
    loop, so no locking is required.

    Relies on ``ServerSession`` identity semantics (no custom ``__eq__`` /
    ``__hash__``, weakref-able).  If a future mcp version makes sessions
    value-comparable, switch to ``id()``-keyed dicts with a cleanup hook.
    """

    def __init__(self) -> None:
        self._subs: WeakKeyDictionary[ServerSession, set[str]] = WeakKeyDictionary()

    def add(self, session: ServerSession, uri: str) -> None:
        """Register *session*'s subscription to *uri* (idempotent)."""
        self._subs.setdefault(session, set()).add(uri)

    def remove(self, session: ServerSession, uri: str) -> None:
        """Drop *session*'s subscription to *uri* (idempotent)."""
        uris = self._subs.get(session)
        if uris is not None:
            uris.discard(uri)
            if not uris:
                del self._subs[session]

    def sessions_for(self, uri: str) -> list[ServerSession]:
        """All live sessions subscribed to *uri*."""
        return [s for s, uris in self._subs.items() if uri in uris]


class DesktopFastMCP(FastMCP):
    """FastMCP subclass that declares ``resources.subscribe`` and tracks
    window:// subscriptions.

    Must be used at every construction site in ``create_mcp_server`` (both
    the OAuth and the plain branch) so all transports advertise the
    capability and handle subscribe requests.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # FastMCP.__init__ calls self._setup_handlers() — our override runs
        # during super().__init__(), so the registry must exist FIRST.
        self._subscriptions = SubscriptionRegistry()
        super().__init__(*args, **kwargs)

    @property
    def subscription_registry(self) -> SubscriptionRegistry:
        return self._subscriptions

    def _setup_handlers(self) -> None:
        super()._setup_handlers()
        self._register_subscription_handlers()
        self._patch_resource_capabilities()

    def _register_subscription_handlers(self) -> None:
        server = self._mcp_server

        # The A2C Computer wraps list_windows() in an except-broad that
        # returns [] on any error — a failing subscribe response would
        # silently kill Desktop discovery.  Keep these handlers defensive
        # and never-raising.
        # SDK stubs carry no type annotations (mcp <2.0): ignore their
        # untyped decorators — our handler signatures are fully typed.
        @server.subscribe_resource()  # type: ignore[no-untyped-call, untyped-decorator]
        async def _handle_subscribe(uri: AnyUrl) -> None:
            try:
                session = request_ctx.get().session
            except LookupError:  # pragma: no cover — handler always runs in a request
                return
            self._subscriptions.add(session, str(uri))

        @server.unsubscribe_resource()  # type: ignore[no-untyped-call, untyped-decorator]
        async def _handle_unsubscribe(uri: AnyUrl) -> None:
            try:
                session = request_ctx.get().session
            except LookupError:  # pragma: no cover — handler always runs in a request
                return
            self._subscriptions.remove(session, str(uri))

    def _patch_resource_capabilities(self) -> None:
        """Instance-level patch: advertise ``resources.subscribe=True``.

        ``Server.get_capabilities`` hardcodes ``subscribe=False``; every
        transport builds initialization options via the no-arg
        ``create_initialization_options()``, so patching the bound method
        covers stdio / sse / streamable-http / in-memory alike.
        """
        original = self._mcp_server.get_capabilities

        def patched(
            notification_options: NotificationOptions,
            experimental_capabilities: dict[str, dict[str, Any]],
        ) -> types.ServerCapabilities:
            caps = original(notification_options, experimental_capabilities)
            if caps.resources is not None:
                caps.resources.subscribe = True
            return caps

        self._mcp_server.get_capabilities = patched  # type: ignore[method-assign]
