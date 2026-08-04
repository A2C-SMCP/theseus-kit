"""OAuth 2.0 RS integration: TokenVerifier adapter and AS discovery.

Theseus-kit acts as an OAuth Protected Resource (RS): it validates the MCP
Client's Bearer token locally (RS256 + JWKS from TFRSManager AS), extracts the
resource owner's identity, and makes the verified token available for forwarding
to TFRobotServer (S3).

This module bridges two worlds:

* **tfrs-auth** ``JwtVerifier`` — sync RS256 verifier with JWKS caching/refresh
* **MCP SDK** ``TokenVerifier`` protocol — async ``verify_token(token) →
  AccessToken | None``

The adapter uses ``asyncio.to_thread()`` to run the sync verifier in a thread
pool; ``JwtVerifier`` is internally thread-safe (``threading.Lock``).

.. seealso:: :ref:`docs/auth-oauth-design.md` §6 (Topology A), §12 S2.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from mcp.server.auth.provider import AccessToken
from tfrs_auth import Claims, JwtVerifier, TokenVerificationError
from tfrs_auth.discovery import fetch_as_metadata


class TheseusTokenVerifier:
    """Adapts tfrs-auth :class:`JwtVerifier` → MCP SDK :class:`TokenVerifier`.

    Runs the sync RS256 verifier via :func:`asyncio.to_thread` so the async
    MCP HTTP stack is never blocked by JWKS fetches or RSA operations.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        required_scope: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._required_scope = required_scope
        self._issuer = issuer
        self._audience = audience
        self._verifier = JwtVerifier.from_url(
            jwks_url,
            issuer=issuer,
            audience=audience,
            http_client=http_client,
        )

    # -- TokenVerifier protocol ------------------------------------------

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a Bearer token and return :class:`AccessToken` if valid.

        Returns ``None`` (rather than raising) for any verification failure,
        matching the MCP SDK protocol's expectation that the middleware handles
        missing/invalid auth silently.
        """
        try:
            claims = await asyncio.to_thread(
                self._verifier.verify,
                token,
                required_scope=self._required_scope,
            )
        except TokenVerificationError:
            return None

        return AccessToken(
            token=token,
            client_id=claims.sub,
            scopes=list(claims.scopes),
            expires_at=claims.exp,
            subject=claims.sub,
            claims=_claims_to_dict(claims),
        )


def _claims_to_dict(claims: Claims) -> dict[str, Any]:
    """Extract the full claims payload for downstream forwarding (S3)."""
    return {
        "iss": claims.iss,
        "sub": claims.sub,
        "aud": claims.aud,
        "org": claims.org,
        "scope": claims.scope,
        "exp": claims.exp,
        "iat": claims.iat,
        "jti": claims.jti,
        "act": dict(claims.act) if claims.act else None,
        **claims.extra,
    }


async def build_token_verifier(
    authorization_server: str,
    *,
    audience: str | None = None,
    required_scope: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TheseusTokenVerifier:
    """Discover AS metadata and construct a :class:`TheseusTokenVerifier`.

    Performs an RFC 8414 OAuth 2.0 Authorization Server Metadata discovery
    against *authorization_server* to obtain the ``jwks_uri`` and ``issuer``,
    then builds a ``TheseusTokenVerifier`` wired to that JWKS endpoint.

    Args:
        authorization_server: TFRSManager OAuth AS base URL (e.g.
            ``https://manager.example.com``).
        audience: Expected ``aud`` claim (theseus-kit PRM resource URL).
            ``None`` skips audience validation (lenient, for development).
        required_scope: Scope required for token acceptance (e.g.
            ``"config:read"``). ``None`` skips scope enforcement.
        http_client: External ``httpx.AsyncClient``; created internally if
            not provided.

    Returns:
        A configured :class:`TheseusTokenVerifier` ready for FastMCP
        ``token_verifier`` injection.
    """
    as_meta = await fetch_as_metadata(authorization_server, http_client=http_client)
    return TheseusTokenVerifier(
        jwks_url=as_meta.jwks_uri,
        issuer=as_meta.issuer,
        audience=audience,
        required_scope=required_scope,
    )
