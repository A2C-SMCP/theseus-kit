"""Wire an ``AsyncCachingTokenSource`` for one ``RobotClient``.

One source per RobotClient — its cache and single-flight are shared across that
client's requests (the intended reuse unit; callers building one client per
target robot get one cached source each). Reuses tfrs-auth's token source
verbatim for cache hit / near-expiry refresh / concurrent single-flight /
transient-failure backoff; theseus-kit adds nothing to that machinery — only
the ``token_url`` (the Manager exchange endpoint) and optional test hooks
(``clock`` for deterministic expiry, ``http_client``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from tfrs_auth import AsyncCachingTokenSource

from .config import CredentialConfig
from .credentials import build_credential

#: Manager token-exchange endpoint path (RFC 8693 token-exchange / client_credentials).
TOKEN_PATH = "/api/v1/oauth/token"


def build_token_source(
    credential: CredentialConfig,
    *,
    manager_base_url: str,
    http_client: httpx.AsyncClient | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    **source_kwargs: Any,
) -> AsyncCachingTokenSource:
    """Build the (process-singleton) token source.

    ``clock`` / ``sleep`` are test hooks (deterministic expiry / instant retry
    backoff). Extra ``source_kwargs`` forward to ``AsyncCachingTokenSource``
    (e.g. ``expiry_skew``, ``max_retries``, ``backoff_base``, ``backoff_factor``).
    """
    cred = build_credential(credential)
    token_url = manager_base_url.rstrip("/") + TOKEN_PATH
    kwargs: dict[str, Any] = dict(source_kwargs)
    if clock is not None:
        kwargs["clock"] = clock
    if sleep is not None:
        kwargs["sleep"] = sleep
    return AsyncCachingTokenSource(
        cred,
        token_url=token_url,
        http_client=http_client,
        **kwargs,
    )
