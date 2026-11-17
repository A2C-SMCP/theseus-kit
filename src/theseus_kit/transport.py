"""Auth-aware httpx layer: Bearer-only, canonical robot base URL, typed errors.

The Operator gateway derives the tenant namespace from the request Host and the
robot identity from ``/c/{robotType}/{robotId}``; theseus-kit no longer sends
``X-TF-*`` routing headers.  The configured ``access_url`` is the full canonical
base (Host + robot path), so ``/v1/factory/**`` / ``/llms.txt`` pass through the
gateway unchanged.

Two token-source paths converge here (§8 of the OAuth design):

* **user_pat** → :class:`AsyncCachingTokenSource` (exchange +
  cache + refresh, #17).
* **OAuth** → :class:`StaticTokenSource` (pre-validated bearer, no exchange).

Robot-side 401/403 surface as :class:`AuthRejectedError` (no silent refresh-
retry: that needs an upstream ``AsyncCachingTokenSource.invalidate()``, tracked
against tfrs-auth cnb#3). A retained old Host returns 409
``ROBOT_ADDRESS_CHANGED``; an unknown Host / robot-not-on-Host returns 404.
Token-exchange failures surface via
:func:`theseus_kit.errors.map_exchange_error`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel
from tfrs_auth import AsyncCachingTokenSource
from tfrs_auth.client import Token
from tfrs_auth.errors import TfrsAuthError

from .config import RobotTarget, TheseusSettings
from .errors import (
    AuthRejectedError,
    DraftNotFoundError,
    RobotAddressChangedError,
    RobotApiError,
    RobotPathRejectedError,
    RobotRouteNotFoundError,
    RobotValidationError,
    map_exchange_error,
)
from .models import PaginatedList, TFSResponse, parse_tfs_response
from .redaction import redact_secrets
from .tokens import build_token_source

_M = TypeVar("_M", bound=BaseModel)

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
    """httpx.Auth: inject ``Authorization: Bearer <jwt>`` only.

    The Operator gateway resolves namespace/robot from the Host + canonical
    ``/c/{robotType}/{robotId}`` path; sending ``X-TF-*`` is obsolete and is
    removed here.  Only :class:`tfrs_auth.errors.TfrsAuthError` from ``token()``
    is mapped; any other failure propagates raw.
    """

    def __init__(self, token_source: AsyncCachingTokenSource | StaticTokenSource) -> None:
        self._token_source = token_source

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        try:
            token = await self._token_source.token()
        except TfrsAuthError as exc:
            raise map_exchange_error(exc) from exc
        request.headers["Authorization"] = f"Bearer {token.access_token}"
        yield request


class RobotClient:
    """Async HTTP client for a target TFRobotServer.

    *base_url* is the Manager-authoritative canonical robot base
    (``https://{alias}/c/{robotType}/{robotId}/``); the gateway strips the
    ``/c/...`` prefix before forwarding to TFRobotServer.  Authentication is
    injected per-request via :class:`RobotAuth`; callers never handle tokens or
    routing headers directly.
    """

    def __init__(
        self,
        token_source: AsyncCachingTokenSource | StaticTokenSource,
        *,
        base_url: str,
        verify: bool | str = True,
        timeout_s: float = 30.0,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._token_source = token_source
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            auth=RobotAuth(token_source),
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
        ``access_url``. ``clock`` is a test hook for deterministic expiry.
        """
        token_source = build_token_source(
            settings.credential,
            manager_base_url=settings.robot.manager_api_base_url,
            http_client=http_client,
            clock=clock,
        )
        verify: bool | str = str(settings.robot.ca_bundle) if settings.robot.ca_bundle else settings.robot.verify
        return cls(
            token_source,
            base_url=settings.robot.access_url,
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
        :class:`StaticTokenSource` and injected as a Bearer — no token exchange,
        caching, or refresh.

        *robot* provides the Manager-authoritative ``access_url``.

        Callers SHOULD extract ``exp`` from the validated
        :class:`~mcp.server.auth.provider.AccessToken.claims` and pass it as
        *expires_at*. When omitted, the token is treated as never-expiring
        (``float("inf")``), which means theseus-kit cannot distinguish an
        expired token from a genuinely unauthorized one.
        """
        source = StaticTokenSource(access_token=token, scope=scope, expires_at=expires_at)
        verify: bool | str = str(robot.ca_bundle) if robot.ca_bundle else robot.verify
        return cls(
            source,
            base_url=robot.access_url,
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
        return self._ensure_ok(path, response, scope_hint="config:read")

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
        except RobotRouteNotFoundError:
            # Gateway-level 404 (unknown Host / robot not on this Host) must not
            # be mistaken for a TFRobotServer draft-not-found.
            raise
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

    async def get_model(self, path: str, model_type: type[_M]) -> TFSResponse[_M]:
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

    async def post(
        self,
        path: str,
        *,
        json: Any = None,
        data: Any = None,
        files: Any = None,
        scope_hint: str = "config:write",
    ) -> httpx.Response:
        """POST to *path*; no automatic retry (mutations are not idempotent).

        ``scope_hint`` is used only in 403 error guidance.  The scopes requested
        during PAT token exchange come from ``THESEUS_CREDENTIAL__SCOPES``.
        """
        return await self._request(
            "POST",
            path,
            json=json,
            content=data,
            files=files,
            retry=False,
            scope_hint=scope_hint,
        )

    async def put(
        self,
        path: str,
        *,
        json: Any = None,
        data: Any = None,
        scope_hint: str = "config:write",
    ) -> httpx.Response:
        """PUT to *path*; no automatic retry.

        ``scope_hint`` affects only 403 error guidance, not token exchange.
        """
        return await self._request(
            "PUT",
            path,
            json=json,
            content=data,
            retry=False,
            scope_hint=scope_hint,
        )

    async def delete(self, path: str, *, scope_hint: str = "config:write") -> httpx.Response:
        """DELETE *path* without retry; ``scope_hint`` only labels 403 errors."""
        return await self._request("DELETE", path, retry=False, scope_hint=scope_hint)

    # -- internal -----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        retry: bool = False,
        scope_hint: str | None = None,
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
                return self._ensure_ok(
                    path,
                    response,
                    scope_hint=(scope_hint or ("config:read" if method == "GET" else "config:write")),
                )
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
    def _ensure_ok(
        path: str,
        response: httpx.Response,
        *,
        scope_hint: str,
    ) -> httpx.Response:
        # Defense-in-depth: a caller could misuse `path` (e.g. embed a token as a
        # query string). Scrub PAT/JWT-shaped values so they never reach error text.
        safe_path = redact_secrets(path)
        if 300 <= response.status_code < 400:
            # A retained old Host may 302 page navigations; API requests should
            # get 409, but never silently parse a redirect body as success.
            raise RobotAddressChangedError(
                f"robot entry redirected {safe_path} (HTTP {response.status_code}): "
                "机器人地址已变更，请从 Manager 重新获取 accessURL 并更新 "
                "THESEUS_ROBOT__ACCESS_URL。",
                status_code=response.status_code,
            )
        if response.status_code == 404:
            body_text = RobotClient._body_text(response)
            if "unknown ingress host" in body_text or "robot not found on this host" in body_text:
                raise RobotRouteNotFoundError(
                    f"robot route not found for {safe_path} (HTTP 404): "
                    "请确认 access_url 的 Host/alias、robotType、robotId 来自当前环境的 Manager，"
                    "且 target robot 属于该 Host。",
                    status_code=404,
                )
        if response.status_code == 409:
            body_json = RobotClient._json_body(response)
            if body_json.get("errorCode") == "ROBOT_ADDRESS_CHANGED":
                raise RobotAddressChangedError(
                    f"robot address changed for {safe_path} (HTTP 409): "
                    "机器人地址已变更，请从 Manager 重新获取 accessURL（"
                    "https://{alias}/c/tfrobot/{rid}/），更新 THESEUS_ROBOT__ACCESS_URL 后重启。"
                    "旧地址仅保留 30 天。"
                )
        if response.status_code == 400:
            body_text = RobotClient._body_text(response)
            if "invalid robot" in body_text:
                raise RobotPathRejectedError(
                    f"robot path rejected for {safe_path} (HTTP 400): "
                    "access_url 的 robotType/robotId 不合法或与网关路径规则不匹配。",
                    status_code=400,
                )
        if response.status_code == 401:
            raise AuthRejectedError(f"robot rejected {safe_path} (HTTP 401): 凭证无效、已过期或目标 Robot 不匹配。")
        if response.status_code == 403:
            raise AuthRejectedError(
                f"robot rejected {safe_path} (HTTP 403): 无权访问目标 Robot，"
                f"或当前短 Token 缺少此操作所需 scope（{scope_hint}）。"
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
    def _json_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _body_text(response: httpx.Response) -> str:
        try:
            return response.text
        except Exception:  # pragma: no cover - defensive only
            return ""

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
