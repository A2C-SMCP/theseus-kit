"""Typed errors for theseus-kit's auth + routing layer.

theseus-kit surfaces tfrs-auth exchange failures and robot-side failures as
typed exceptions so callers branch on failure mode rather than string-matching.
All messages are scrubbed of credentials/tokens via :mod:`theseus_kit.redaction`.
"""

from __future__ import annotations

from tfrs_auth.errors import (
    InvalidClientError,
    InvalidGrantError,
    InvalidScopeError,
    InvalidTargetError,
    PaymentRequiredError,
    RateLimitedError,
    TemporarilyUnavailableError,
    TfrsAuthError,
    TransportError,
)

from .redaction import redact_secrets


class TheseusError(Exception):
    """Base class for all theseus-kit errors."""


class ConfigError(TheseusError):
    """Invalid theseus-kit configuration."""


class RoutingConfigError(ConfigError):
    """A routing field (namespace / rid / robot_type) is missing or malformed."""


class CredentialError(TheseusError):
    """The configured credential was rejected by the token endpoint."""


class ScopeOrAudienceError(TheseusError):
    """The requested audience / scope is not grantable for this credential."""


class ExchangeUnavailableError(TheseusError):
    """The token endpoint is temporarily unavailable (may be retryable)."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__(message)


class AuthRejectedError(TheseusError):
    """The robot rejected the request (401 / 403): credential invalid or scope insufficient."""


class SubscriptionFrozenError(TheseusError):
    """The target robot's organization subscription is frozen (HTTP 402)."""

    def __init__(self, message: str, *, renew_url: str | None) -> None:
        self.renew_url = renew_url
        super().__init__(message)


class RobotApiError(TheseusError):
    """A non-auth robot API failure (4xx / 5xx, or network with no HTTP response)."""

    def __init__(self, message: str, *, status_code: int) -> None:
        # ``status_code == 0`` means no HTTP response was received (network failure).
        self.status_code = status_code
        super().__init__(message)


def map_exchange_error(exc: TfrsAuthError) -> TheseusError:
    """Map a tfrs-auth exchange/network error to a theseus-kit typed error.

    Called when ``AsyncCachingTokenSource.token()`` raises. Covers both the
    OAuth error body (``TokenExchangeError`` subclasses) and the network-layer
    ``TransportError``. Messages are scrubbed defensively.
    """
    message = redact_secrets(str(exc))
    if isinstance(exc, PaymentRequiredError):
        return SubscriptionFrozenError(
            _hint(message, "目标机器人所属组织订阅已冻结（402），需续费后重试。"),
            renew_url=exc.renew_url,
        )
    if isinstance(exc, TransportError):
        return ExchangeUnavailableError(
            _hint(message, "换发端点网络不可达（连接 / 超时 / DNS），可稍后重试。"),
            retryable=True,
        )
    if isinstance(exc, (TemporarilyUnavailableError, RateLimitedError)):
        return ExchangeUnavailableError(
            _hint(message, "换发暂不可用（限流 429 / 服务不可用 503），可稍后重试。"),
            retryable=exc.retryable,
        )
    if isinstance(exc, (InvalidGrantError, InvalidClientError)):
        return CredentialError(_hint(message, "凭证被拒：检查 PAT / 机器凭证是否有效、未撤销或未过期。"))
    if isinstance(exc, (InvalidTargetError, InvalidScopeError)):
        return ScopeOrAudienceError(
            _hint(
                message,
                "受众 / Scope 不足：确认 audience=robot:{public_id}、scope（只读=config:read）。",
            )
        )
    return TheseusError(_hint(message, "令牌换发失败。"))


def _hint(message: str, hint: str) -> str:
    body = message.strip()
    return f"{hint}（上游：{body}）" if body else hint
