"""MCP server composition root."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl

from .config import OAuthConfig, TheseusSettings

if TYPE_CHECKING:
    from .oauth import TheseusTokenVerifier

_INSTRUCTIONS = (
    "Inspect and manage TFRobot configuration. Read the exposed editing skills "
    "and version-specific llms.txt documentation before mutating configuration."
)


def create_mcp_server(settings: TheseusSettings | None = None) -> FastMCP:
    """Build the FastMCP instance, conditionally configured for OAuth.

    When *settings* carries an ``oauth`` credential, the server is wired with
    a :class:`TheseusTokenVerifier` (bearer-auth middleware + Protected Resource
    Metadata routes) so it can act as an OAuth RS (Topology A).  Otherwise the
    plain stdio server is returned unchanged.
    """
    if settings is not None and isinstance(settings.credential, OAuthConfig):
        cred: OAuthConfig = settings.credential

        resource_url = cred.resource_server_url
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(cred.authorization_server),
            resource_server_url=(AnyHttpUrl(resource_url) if resource_url is not None else None),
            required_scopes=list(_parse_space_separated(cred.scopes)),
        )

        verifier = _LazyOAuthTokenVerifier(
            authorization_server=cred.authorization_server,
            required_scope=cred.scopes,
            audience=resource_url,
        )

        return FastMCP(
            name="theseus-kit",
            instructions=_INSTRUCTIONS,
            token_verifier=verifier,
            auth=auth,
        )

    return FastMCP(name="theseus-kit", instructions=_INSTRUCTIONS)


class _LazyOAuthTokenVerifier:
    """TokenVerifier that lazily discovers the AS and builds a TheseusTokenVerifier.

    FastMCP requires ``token_verifier`` at construction time (sync), but AS
    metadata discovery is async.  This shim defers the async discovery to the
    first ``verify_token`` call and caches the result under an async lock
    (single-flight).
    """

    def __init__(
        self,
        authorization_server: str,
        required_scope: str,
        audience: str | None = None,
    ) -> None:
        self._authorization_server = authorization_server
        self._required_scope = required_scope
        self._audience = audience
        self._verifier: TheseusTokenVerifier | None = None
        self._lock = asyncio.Lock()

    async def verify_token(self, token: str) -> AccessToken | None:
        if self._verifier is None:
            async with self._lock:
                if self._verifier is None:
                    from .oauth import build_token_verifier

                    self._verifier = await build_token_verifier(
                        self._authorization_server,
                        required_scope=self._required_scope,
                        audience=self._audience,
                    )
        result: AccessToken | None = await self._verifier.verify_token(token)
        return result


def _parse_space_separated(scopes: str) -> frozenset[str]:
    """Parse a space-separated scope string into an immutable set."""
    return frozenset(s for s in scopes.split() if s)


def main() -> None:
    """Run the MCP server over the portable stdio transport."""
    create_mcp_server().run(transport="stdio")
