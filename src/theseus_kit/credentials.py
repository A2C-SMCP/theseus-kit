"""Build a tfrs-auth ``Credential`` from theseus-kit config.

``user_pat`` yields a ``tfrs_auth.PatCredential`` driven by
``AsyncCachingTokenSource`` (token-exchange + cache + refresh).
OAuth is handled via ``StaticTokenSource`` — see :mod:`theseus_kit.oauth`.
theseus-kit never reimplements the exchange / refresh / retry algorithm.
"""

from __future__ import annotations

from collections.abc import Sequence

from tfrs_auth import PatCredential, Scope, robot_audience, scopes_to_str

from .config import CredentialConfig, OAuthConfig, UserPatConfig, parse_public_id
from .errors import ConfigError


def build_credential(
    cred: CredentialConfig,
    *,
    scopes: Sequence[Scope | str] | None = None,
) -> PatCredential:
    """Construct the tfrs-auth credential for the configured source.

    ``user_pat`` exchanges the user's PAT for a robot-scoped JWT via Manager's
    token-exchange endpoint (RFC 8693).  The token ``audience`` is
    ``robot:{public_id}`` where *public_id* identifies the target robot. By
    default the configured theseus-kit scopes are requested, and Manager grants
    their intersection with the PAT ceiling and the target Robot's
    authorization. ``scopes`` is an optional explicit per-call override.
    """
    if isinstance(cred, UserPatConfig):
        try:
            org_slug, employee_no = parse_public_id(cred.robot_public_id)
        except ValueError as exc:
            raise ConfigError(f"robot_public_id is invalid: {exc}") from exc
        return PatCredential(
            pat=cred.pat.get_secret_value(),
            audience=robot_audience(org_slug, employee_no),
            scope=cred.scopes if scopes is None else scopes_to_str(scopes),
        )
    if isinstance(cred, OAuthConfig):
        raise ConfigError(
            "OAuth credentials bypass the token exchange pipeline. "
            "The OAuth path uses a static bearer token held by the MCP Client "
            "and injected by RobotClient — no AsyncCachingTokenSource is needed. "
            "Use the OAuth static bearer path instead of build_credential() / build_token_source()."
        )
    # Closed discriminated union — fail explicitly if a new kind is added without
    # wiring it here (decouples runtime validation from mypy type narrowing).
    raise ConfigError(f"unsupported credential kind: {cred!r}")  # pyright: ignore[reportUnreachable]
