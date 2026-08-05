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
from typing import Any

import httpx
from pydantic import BaseModel
from tfrs_auth import AsyncCachingTokenSource
from tfrs_auth.client import Token
from tfrs_auth.errors import TfrsAuthError

from .config import RobotTarget, TheseusSettings
from .errors import AuthRejectedError, DraftNotFoundError, RobotApiError, RobotValidationError, map_exchange_error
from .models import PaginatedList, TFSResponse, parse_tfs_response
from .redaction import redact_secrets
from .routing import RequestContext
from .tokens import build_token_source

_FACTORY_DOCS_PREFIX = "/v1/factory/llm-docs/"

_JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"

# HTTP statuses for which GET retry is safe (Issue #3: read-only retry gate).
# 429 + 5xx are transient server-side conditions; all others are not retried.
_RETRYABLE_STATUSES: frozenset[int] = frozenset({429} | set(range(500, 600)))


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
        expires_at: float | None = None,
    ) -> RobotClient:
        """Build a client that forwards a pre-validated OAuth AS token.

        The *token* is the raw RS256 JWT validated by
        :class:`TheseusTokenVerifier` (S2). It is held in a
        :class:`StaticTokenSource` and injected as a Bearer alongside X-TF-*
        routing headers — no token exchange, caching, or refresh.

        *robot* provides the routing identity (X-TF-*) and the API base URL.

        Callers SHOULD extract ``exp`` from the validated
        :class:`~mcp.server.auth.provider.AccessToken.claims` and pass it as
        *expires_at*. When omitted, the token is treated as never-expiring
        (``float("inf")``), which means theseus-kit cannot distinguish an
        expired token from a genuinely unauthorized one.
        """
        source = StaticTokenSource(access_token=token, scope=scope, expires_at=expires_at)
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

    async def get_draft_dict(self, setting_id: int) -> dict[str, Any]:
        """Fetch a draft DTO as a raw dict for hash comparison.

        Unlike the read helpers, this returns the raw ``data`` dict without
        building a full response model — optimized for the read-check-write
        optimistic concurrency pattern used by :class:`DraftEditor` and
        :class:`TemplateSaver`.

        Raises :class:`DraftNotFoundError` when *setting_id* does not exist
        (HTTP 404 or body code 404).
        """
        path = f"/v1/factory/drafts/{setting_id}"
        try:
            response = await self.get(path)
        except RobotApiError as exc:
            if exc.status_code == 404:
                raise DraftNotFoundError(f"Draft {setting_id} not found (404).") from exc
            raise
        body: dict[str, Any] = response.json()
        code: int = body.get("code", 0)
        if code == 404:
            raise DraftNotFoundError(f"Draft {setting_id} not found (404).")
        if code != 200:
            raise RobotApiError(
                f"robot returned code {code} from GET {path}: {body.get('message', '')}",
                status_code=code,
            )
        # ``or {}`` guards against ``"data": null`` (key present, value None).
        return body.get("data") or {}

    # -- typed read helpers -------------------------------------------------

    async def get_model(self, path: str, model_type: type[BaseModel]) -> Any:
        """GET *path* and parse the response as ``TFSResponse[model_type]``.

        Uses retry for transient failures (5xx / 429 / network).  The
        returned object is a :class:`TFSResponse` whose ``data`` field is
        an instance of *model_type*.
        """

        response = await self._request("GET", path, retry=True)
        return parse_tfs_response(response, model_type)

    async def get_paginated(
        self,
        path: str,
        model_type: type[BaseModel],
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> TFSResponse:  # type: ignore[type-arg]
        """GET *path* with pagination params; return ``TFSResponse[PaginatedList[model_type]]``.

        *page* is 1-based (matching TFRobotServer's contract).  Uses retry.
        """
        response = await self._request("GET", path, params={"page": page, "pageSize": page_size}, retry=True)
        body = response.json()
        code: int = body.get("code", 0)
        message: str = body.get("message", "")
        if code != 200:
            raise RobotApiError(f"robot returned code {code}: {message}", status_code=code)
        raw_data: dict[str, Any] = body.get("data", {})
        items = [model_type.model_validate(item) for item in raw_data.get("items", [])]
        paginated: PaginatedList[Any] = PaginatedList(items=items, total=raw_data.get("total", 0))
        return TFSResponse(code=code, message=message, data=paginated)

    # -- write endpoints (no retry) -----------------------------------------

    async def post(self, path: str, *, json: Any = None, data: Any = None, files: Any = None) -> httpx.Response:
        """POST to *path*; no automatic retry (mutations are not idempotent)."""
        return await self._request("POST", path, json=json, content=data, files=files, retry=False)

    async def put(self, path: str, *, json: Any = None, data: Any = None) -> httpx.Response:
        """PUT to *path*; no automatic retry."""
        return await self._request("PUT", path, json=json, content=data, retry=False)

    async def delete(self, path: str) -> httpx.Response:
        """DELETE *path*; no automatic retry."""
        return await self._request("DELETE", path, retry=False)

    # -- internal -----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        retry: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue an HTTP request, conditionally retrying on transient failures.

        *retry* enables a single retry (2 total attempts) for 5xx / 429 /
        network errors. 4xx (auth, validation, not-found) are never retried.
        """
        max_attempts = 2 if retry else 1
        safe_path = redact_secrets(path)
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                if not retry or attempt == max_attempts - 1:
                    raise RobotApiError(
                        f"robot request to {safe_path} failed before a response: {type(exc).__name__}",
                        status_code=0,
                    ) from exc
                continue

            try:
                return self._ensure_ok(path, response)
            except (AuthRejectedError, RobotValidationError):
                raise  # never retry auth / validation failures
            except RobotApiError as exc:
                if not retry or attempt == max_attempts - 1 or exc.status_code not in _RETRYABLE_STATUSES:
                    raise
                last_error = exc

        # Should be unreachable — the loop always raises or returns.
        if last_error is not None:
            raise last_error
        raise RuntimeError("unreachable: _request loop fell through")

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
        if response.status_code == 422:
            msg = RobotClient._extract_422_message(response)
            raise RobotValidationError(
                f"robot rejected {safe_path} (HTTP 422): {msg}",
                status_code=422,
                validation_message=msg,
            )
        if response.status_code >= 400:
            raise RobotApiError(
                f"robot {safe_path} returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response

    @staticmethod
    def _extract_422_message(response: httpx.Response) -> str:
        """Extract the validation message from a 422 TFRobotServer response.

        TFRobotServer's 422 handler uses ``msg`` instead of ``message``
        (an inconsistency in the codebase).  Fall back to ``message``.
        """
        try:
            body = response.json()
        except ValueError:
            return ""
        return str(body.get("msg") or body.get("message", ""))
