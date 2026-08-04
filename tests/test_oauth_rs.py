"""Hermetic tests for S2 — RS PRM + Bearer validation.

Uses a local :class:`http.server.ThreadingHTTPServer` that serves JWKS and AS
metadata endpoints. RSA keys are generated on the fly; tokens are signed with
the private key and verified against the public JWKS — a complete RS256
round-trip without external dependencies.

E2E smoke tests (``THESEUS_E2E=1``) test against a real TFRSManager AS.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.provider import AccessToken

from theseus_kit.oauth import TheseusTokenVerifier, build_token_verifier

#: Fixture type alias — avoids repeating the verbose tuple annotation everywhere.
_RSAKeyPair = tuple[rsa.RSAPrivateKey, dict[str, Any]]

# ── RSA key generation ──────────────────────────────────────────────────


def _generate_rsa_key_pair() -> _RSAKeyPair:
    """Generate a fresh RSA 2048 key pair; return (private_key, jwks_dict)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    public_key = private_key.public_key()
    pub_numbers = public_key.public_numbers()

    import base64

    def _b64url(n: int) -> str:
        length = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(length, byteorder="big")).rstrip(b"=").decode("ascii")

    kid = "test-kid-001"
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _b64url(pub_numbers.n),
                "e": _b64url(pub_numbers.e),
            }
        ]
    }
    return private_key, jwks


# ── Local fake server (JWKS + AS metadata) ──────────────────────────────


@dataclass
class _ServerState:
    jwks: dict[str, Any]
    issuer: str
    requests: list[tuple[str, str]]  # (method, path)


class _FakeOAuthHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    @property
    def _state(self) -> _ServerState:
        obj = getattr(self.server, "state")  # noqa: B009
        assert isinstance(obj, _ServerState)
        return obj

    def _send_json(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._state.requests.append(("GET", self.path))
        if self.path == "/.well-known/oauth-authorization-server":
            self._send_json(
                200,
                {
                    "issuer": self._state.issuer,
                    "authorization_endpoint": f"{self._state.issuer}/authorize",
                    "token_endpoint": f"{self._state.issuer}/token",
                    "jwks_uri": f"{self._state.issuer}/jwks",
                    "response_types_supported": ["code"],
                },
            )
        elif self.path == "/jwks":
            self._send_json(200, self._state.jwks)
        else:
            self._send_json(404, {"error": "not_found"})


@dataclass
class _FakeOAuthServer:
    """Real TCP server for JWKS + AS metadata."""

    server: ThreadingHTTPServer
    base_url: str
    state: _ServerState

    def close(self) -> None:
        self.server.shutdown()

    @property
    def requests(self) -> list[tuple[str, str]]:
        return self.state.requests


def _make_fake_oauth_server(jwks: dict[str, Any], *, issuer: str | None = None) -> _FakeOAuthServer:
    """Start a fake OAuth infra server on an ephemeral port."""
    host = "127.0.0.1"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    sock.close()

    base_url = f"http://{host}:{port}"
    state = _ServerState(
        jwks=jwks,
        issuer=issuer or base_url,
        requests=[],
    )

    server = ThreadingHTTPServer((host, port), _FakeOAuthHandler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return _FakeOAuthServer(server=server, base_url=base_url, state=state)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def rsa_keys() -> _RSAKeyPair:
    return _generate_rsa_key_pair()


@pytest.fixture
def fake_oauth_server(rsa_keys: _RSAKeyPair) -> Iterator[_FakeOAuthServer]:
    """Start a fake OAuth infra server with JWKS and AS metadata."""
    _, jwks = rsa_keys
    srv = _make_fake_oauth_server(jwks)
    try:
        yield srv
    finally:
        srv.close()


# ── Helpers ──────────────────────────────────────────────────────────────


def _sign_token(
    private_key: rsa.RSAPrivateKey,
    *,
    iss: str,
    sub: str = "user:42",
    aud: str = "http://localhost:8000/mcp",
    scope: str = "config:read",
    exp: int | None = None,
    iat: int | None = None,
    kid: str = "test-kid-001",
    org: str = "1",
    typ: str = "at+jwt",
    extra: dict[str, Any] | None = None,
) -> str:
    """Sign a test RS256 JWT with the given private key."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "scope": scope,
        "exp": exp or (now + 300),
        "iat": iat or now,
        "jti": f"jti-{now}",
        "org": org,
        **(extra or {}),
    }
    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": kid, "typ": typ},
    )


# ── TheseusTokenVerifier ─────────────────────────────────────────────────


class TestTheseusTokenVerifier:
    """Direct verifier tests — JwtVerifier.from_url against local JWKS."""

    def test_valid_token_returns_access_token(self, rsa_keys: _RSAKeyPair, fake_oauth_server: _FakeOAuthServer) -> None:
        priv, _ = rsa_keys
        token = _sign_token(priv, iss=fake_oauth_server.base_url, aud="rs-1")
        verifier = TheseusTokenVerifier(
            jwks_url=f"{fake_oauth_server.base_url}/jwks",
            issuer=fake_oauth_server.base_url,
            audience="rs-1",
        )

        result = asyncio.run(verifier.verify_token(token))

        assert result is not None
        assert result.token == token
        assert result.subject == "user:42"
        assert "config:read" in result.scopes
        assert result.claims is not None
        assert result.claims["iss"] == fake_oauth_server.base_url
        assert result.claims["org"] == "1"

    def test_wrong_audience_returns_none(self, rsa_keys: _RSAKeyPair, fake_oauth_server: _FakeOAuthServer) -> None:
        priv, _ = rsa_keys
        token = _sign_token(priv, iss=fake_oauth_server.base_url, aud="wrong-aud")
        verifier = TheseusTokenVerifier(
            jwks_url=f"{fake_oauth_server.base_url}/jwks",
            issuer=fake_oauth_server.base_url,
            audience="rs-1",  # expected audience differs from token
        )

        result = asyncio.run(verifier.verify_token(token))

        assert result is None

    def test_expired_token_returns_none(self, rsa_keys: _RSAKeyPair, fake_oauth_server: _FakeOAuthServer) -> None:
        priv, _ = rsa_keys
        token = _sign_token(priv, iss=fake_oauth_server.base_url, aud="rs-1", exp=1, iat=0)
        verifier = TheseusTokenVerifier(
            jwks_url=f"{fake_oauth_server.base_url}/jwks",
            issuer=fake_oauth_server.base_url,
            audience="rs-1",
        )

        result = asyncio.run(verifier.verify_token(token))

        assert result is None

    def test_wrong_issuer_returns_none(self, rsa_keys: _RSAKeyPair, fake_oauth_server: _FakeOAuthServer) -> None:
        priv, _ = rsa_keys
        token = _sign_token(priv, iss="https://wrong-issuer.example.com", aud="rs-1")
        verifier = TheseusTokenVerifier(
            jwks_url=f"{fake_oauth_server.base_url}/jwks",
            issuer=fake_oauth_server.base_url,  # expected issuer differs
            audience="rs-1",
        )

        result = asyncio.run(verifier.verify_token(token))

        assert result is None

    def test_wrong_signature_returns_none(self, fake_oauth_server: _FakeOAuthServer) -> None:
        # Generate a different key pair — so the JWKS won't have the right pubkey
        other_priv, _ = _generate_rsa_key_pair()
        token = _sign_token(other_priv, iss=fake_oauth_server.base_url, aud="rs-1")
        verifier = TheseusTokenVerifier(
            jwks_url=f"{fake_oauth_server.base_url}/jwks",
            issuer=fake_oauth_server.base_url,
            audience="rs-1",
        )

        result = asyncio.run(verifier.verify_token(token))

        assert result is None

    def test_required_scope_enforced(self, rsa_keys: _RSAKeyPair, fake_oauth_server: _FakeOAuthServer) -> None:
        priv, _ = rsa_keys
        token = _sign_token(priv, iss=fake_oauth_server.base_url, aud="rs-1", scope="config:read")
        verifier = TheseusTokenVerifier(
            jwks_url=f"{fake_oauth_server.base_url}/jwks",
            issuer=fake_oauth_server.base_url,
            audience="rs-1",
            required_scope="config:write",  # token only has config:read
        )

        result = asyncio.run(verifier.verify_token(token))

        assert result is None

    def test_no_audience_validation_when_none(self, rsa_keys: _RSAKeyPair, fake_oauth_server: _FakeOAuthServer) -> None:
        """When audience is None, the verifier accepts any audience (lenient mode)."""
        priv, _ = rsa_keys
        token = _sign_token(priv, iss=fake_oauth_server.base_url, aud="anything")
        verifier = TheseusTokenVerifier(
            jwks_url=f"{fake_oauth_server.base_url}/jwks",
            issuer=fake_oauth_server.base_url,
            audience=None,
        )

        result = asyncio.run(verifier.verify_token(token))

        assert result is not None

    def test_no_issuer_validation_when_none(self, rsa_keys: _RSAKeyPair, fake_oauth_server: _FakeOAuthServer) -> None:
        """When issuer is None, the verifier accepts any issuer (lenient mode)."""
        priv, _ = rsa_keys
        token = _sign_token(priv, iss="https://any-issuer.example.com", aud="rs-1")
        verifier = TheseusTokenVerifier(
            jwks_url=f"{fake_oauth_server.base_url}/jwks",
            issuer=None,
            audience="rs-1",
        )

        result = asyncio.run(verifier.verify_token(token))

        assert result is not None

    def test_claims_extra_fields_preserved(self, rsa_keys: _RSAKeyPair, fake_oauth_server: _FakeOAuthServer) -> None:
        """Extra claims beyond the standard set are preserved in result.claims."""
        priv, _ = rsa_keys
        token = _sign_token(
            priv,
            iss=fake_oauth_server.base_url,
            aud="rs-1",
            extra={"client_id": "cli-99", "act": {"sub": "nested"}},
        )
        verifier = TheseusTokenVerifier(
            jwks_url=f"{fake_oauth_server.base_url}/jwks",
            issuer=fake_oauth_server.base_url,
            audience="rs-1",
        )

        result = asyncio.run(verifier.verify_token(token))

        assert result is not None
        assert result.claims is not None
        assert result.claims["client_id"] == "cli-99"
        assert result.claims["act"] == {"sub": "nested"}


# ── build_token_verifier (AS discovery) ──────────────────────────────────


class TestBuildTokenVerifier:
    def test_discovers_as_and_builds_verifier(self, rsa_keys: _RSAKeyPair, fake_oauth_server: _FakeOAuthServer) -> None:
        """End-to-end: discover AS metadata → build verifier → verify token."""
        priv, _ = rsa_keys

        async def _test() -> AccessToken | None:
            verifier = await build_token_verifier(
                fake_oauth_server.base_url,
                audience="rs-1",
                required_scope="config:read",
            )
            token = _sign_token(
                priv,
                iss=fake_oauth_server.base_url,
                aud="rs-1",
                scope="config:read",
            )
            return await verifier.verify_token(token)

        result = asyncio.run(_test())

        assert result is not None
        assert result.subject == "user:42"
        # Verify the discovery request hit the AS metadata endpoint.
        paths = [p for m, p in fake_oauth_server.requests if m == "GET"]
        assert "/.well-known/oauth-authorization-server" in paths


# ── E2E smoke test (THESEUS_E2E=1) ───────────────────────────────────────


@pytest.mark.e2e
class TestOAuthE2E:
    """End-to-end tests requiring a real TFRSManager AS.

    Export ``THESEUS_E2E=1`` and configure the OAuth target via env::

        THESEUS_E2E=1 \\
        THESEUS_OAUTH_TEST_AS_URL=https://manager.example.com \\
        uv run pytest tests/test_oauth_rs.py -v -m e2e
    """

    @pytest.mark.skipif(
        "not __import__('os').getenv('THESEUS_E2E')",
        reason="E2E requires THESEUS_E2E=1 and a real TFRSManager AS",
    )
    def test_discover_real_as_jwks(self) -> None:
        """Smoke test: discover a real AS and verify its JWKS is reachable."""
        import os

        as_url = os.getenv("THESEUS_OAUTH_TEST_AS_URL")
        if not as_url:
            pytest.skip("THESEUS_OAUTH_TEST_AS_URL not set")

        async def _test() -> None:
            # Just verify discovery works — no token signing since we don't
            # have the AS's private key.
            verifier = await build_token_verifier(as_url)
            assert verifier is not None
            # The verifier's internal JwtVerifier should be configured.
            assert verifier._verifier is not None

        asyncio.run(_test())
