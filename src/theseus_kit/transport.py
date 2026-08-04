"""Auth-aware httpx layer: Bearer + routing headers, with typed error mapping.

This is theseus-kit's own routing Transport (architecture layer 3). tfrs-auth's
``BearerAuth`` is still a skeleton upstream, and — more importantly — theseus-kit
must inject the three X-TF-* routing headers alongside the Bearer, which is a
theseus-kit concern, not a generic auth one.

Two token-source paths converge here (§8 of the OAuth design):

* **PAT / client_credentials** → :class:`AsyncCachingTokenSource` (exchange +
  cache + refresh, #17).
* **OAuth** → :class:`StaticTokenSource` (pre-validated bearer, no exchange).

Robot-side 401/403 surface as :class:`AuthRejectedError` (no silent refresh-
retry: that needs an upstream ``AsyncCachingTokenSource.invalidate()``, tracked
against tfrs-auth cnb#3). Token-exchange failures surface via
:func:`theseus_kit.errors.map_exchange_error`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass, field

import httpx
from tfrs_auth import AsyncCachingTokenSource
from tfrs_auth.client import Token
from tfrs_auth.errors import TfrsAuthError

from .config import RobotTarget, TheseusSettings
from .errors import AuthRejectedError, RobotApiError, map_exchange_error
from .redaction import redact_secrets
from .routing import RequestContext
from .tokens import build_token_source

_FACTORY_DOCS_PREFIX = "/v1/factory/llm-docs/"

_JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"


@dataclass(frozen=True, slots=True)
class StaticTokenSource:
    """Holds a pre-validated bearer token for the OAuth path.

    Unlike :class:`AsyncCachingTokenSource`, this does **no** token exchange,
    caching, or refresh. The token is validated upstream by
    :class:`TheseusTokenVerifier` (S2) and forwarded directly to TFRobotServer
    (§10.1-new: TFRobotServer natively accepts OAuth AS tokens).

    The ``token()`` / ``aclose()`` interface mirrors ``AsyncCachingTokenSource``
    so :class:`RobotAuth` and :class:`RobotClient` can accept either source
    without branching.
    """

    access_token: str = field(repr=False)
    token_type: str = "Bearer"
    scope: str = ""
    expires_at: float | None = None

    async def token(self) -> Token:
        """Return a synthetic :class:`Token` wrapping the static bearer."""
        return Token(
            access_token=self.access_token,
            token_type=self.token_type,
            scope=self.scope,
            issued_token_type=_JWT_TOKEN_TYPE,
            expires_at=self.expires_at if self.expires_at is not None else float("inf"),
        )

    async def aclose(self) -> None:
        """No-op — no HTTP client to close (cf. ``AsyncCachingTokenSource.aclose``)."""


class RobotAuth(httpx.Auth):
    """httpx.Auth: inject ``Authorization: Bearer <jwt>`` + the X-TF-* headers.

    Only :class:`tfrs_auth.errors.TfrsAuthError` from ``token()`` is mapped. The
    user_pat credential's ``request_form()`` is a skeleton upstream (tfrs-auth
    cnb#1); once it ships, broaden the guard so a non-TfrsAuthError failure
    surfaces as a typed error instead of propagating raw.
    """

    def __init__(
        self,
        token_source: AsyncCachingTokenSource | StaticTokenSource,
        context: RequestContext,
    ) -> None:
        self._token_source = token_source
        self._context = context

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        try:
            token = await self._token_source.token()
        except TfrsAuthError as exc:
            raise map_exchange_error(exc) from exc
        request.headers["Authorization"] = f"Bearer {token.access_token}"
        for name, value in self._context.routing_headers().items():
            request.headers[name] = value
        yield request


class RobotClient:
    """Async HTTP client for a target TFRobotServer.

    Authentication and routing are injected per-request via :class:`RobotAuth`;
    callers never handle tokens or X-TF-* headers directly. Read-only helpers
    cover #17's acceptance surface (``/llms.txt``, factory docs, read APIs); the
    full adapter (TFSResponse mapping, pagination, write/publish) is #3.
    """

    def __init__(
        self,
        token_source: AsyncCachingTokenSource | StaticTokenSource,
        context: RequestContext,
        *,
        api_base_url: str,
        verify: bool | str = True,
        timeout_s: float = 30.0,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._token_source = token_source
        self._client = httpx.AsyncClient(
            base_url=api_base_url.rstrip("/"),
            auth=RobotAuth(token_source, context),
            verify=verify,
            timeout=timeout_s,
            headers=dict(headers) if headers else {},
        )

    @classmethod
    def from_settings(
        cls,
        settings: TheseusSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] | None = None,
    ) -> RobotClient:
        """Build a client from loaded settings (production / E2E path).

        ``http_client`` (if given) is used only for the token *exchange* with the
        Manager; robot requests always use a separate client built from
        ``api_base_url``. ``clock`` is a test hook for deterministic expiry.
        """
        token_source = build_token_source(
            settings.credential,
            manager_base_url=settings.robot.manager_base_url,
            http_client=http_client,
            clock=clock,
        )
        context = RequestContext(
            robot_id=settings.robot.robot_id,
            namespace=settings.robot.namespace,
            robot_type=settings.robot.robot_type,
        )
        verify: bool | str = str(settings.robot.ca_bundle) if settings.robot.ca_bundle else settings.robot.verify
        return cls(
            token_source,
            context,
            api_base_url=settings.robot.api_base_url,
            verify=verify,
            timeout_s=settings.robot.timeout_s,
        )

    @classmethod
    def for_static_token(
        cls,
        token: str,
        *,
        robot: RobotTarget,
        scope: str = "",
    ) -> RobotClient:
        """Build a client that forwards a pre-validated OAuth AS token.

        The *token* is the raw RS256 JWT validated by
        :class:`TheseusTokenVerifier` (S2). It is held in a
        :class:`StaticTokenSource` and injected as a Bearer alongside X-TF-*
        routing headers — no token exchange, caching, or refresh.

        *robot* provides the routing identity (X-TF-*) and the API base URL.
        """
        source = StaticTokenSource(access_token=token, scope=scope)
        context = RequestContext(
            robot_id=robot.robot_id,
            namespace=robot.namespace,
            robot_type=robot.robot_type,
        )
        verify: bool | str = str(robot.ca_bundle) if robot.ca_bundle else robot.verify
        return cls(
            source,
            context,
            api_base_url=robot.api_base_url,
            verify=verify,
            timeout_s=robot.timeout_s,
        )

    async def aclose(self) -> None:
        """Close the robot HTTP client and (if owned) the token source's client."""
        await self._client.aclose()
        await self._token_source.aclose()

    async def __aenter__(self) -> RobotClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def get(self, path: str) -> httpx.Response:
        """GET *path* on the robot; raise typed errors on auth / API failure."""
        try:
            response = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise RobotApiError(
                f"robot request to {path} failed before a response: {type(exc).__name__}",
                status_code=0,
            ) from exc
        return self._ensure_ok(path, response)

    async def get_llms_txt(self) -> str:
        """Fetch the robot's ``/llms.txt`` configuration documentation."""
        return (await self.get("/llms.txt")).text

    async def get_factory_doc(self, doc_path: str) -> str:
        """Fetch a ``/v1/factory/llm-docs/**`` version-specific doc.

        *doc_path* may be given with or without the ``/v1/factory/llm-docs/``
        prefix.
        """
        if not doc_path.startswith(_FACTORY_DOCS_PREFIX):
            doc_path = _FACTORY_DOCS_PREFIX + doc_path.lstrip("/")
        return (await self.get(doc_path)).text

    @staticmethod
    def _ensure_ok(path: str, response: httpx.Response) -> httpx.Response:
        # Defense-in-depth: a caller could misuse `path` (e.g. embed a token as a
        # query string). Scrub PAT/JWT-shaped values so they never reach error text.
        safe_path = redact_secrets(path)
        if response.status_code in (401, 403):
            raise AuthRejectedError(
                f"robot rejected {safe_path} (HTTP {response.status_code}): "
                "凭证无效或 scope 不足（只读访问需 config:read）。"
            )
        if response.status_code >= 400:
            raise RobotApiError(
                f"robot {safe_path} returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response
