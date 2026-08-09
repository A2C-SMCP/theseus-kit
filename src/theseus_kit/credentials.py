"""Build a tfrs-auth ``Credential`` from theseus-kit config.

``user_pat`` yields a ``tfrs_auth.PatCredential`` driven by
``AsyncCachingTokenSource`` (token-exchange + cache + refresh).
OAuth is handled via ``StaticTokenSource`` — see :mod:`theseus_kit.oauth`.
theseus-kit never reimplements the exchange / refresh / retry algorithm.
"""

from __future__ import annotations

from collections.abc import Sequence

from tfrs_auth import PatCredential, Scope, robot_audience, scopes_to_str

from .config import CredentialConfig, OAuthConfig, UserPatConfig
from .errors import ConfigError


def _parse_public_id(public_id: str, *, field_name: str) -> tuple[str, str]:
    """Parse a ``{orgSlug}:{employeeNo}`` public_id into its two components.

    The format is contractually defined by Manager's ``publicid`` module and
    mirrored in tfrs-auth's ``contract.public_id()``.
    """
    if ":" not in public_id:
        raise ConfigError(f"{field_name} must be in public_id format ({{orgSlug}}:{{employeeNo}}), got {public_id!r}")
    org_slug, employee_no = public_id.rsplit(":", 1)
    if not org_slug or not employee_no:
        raise ConfigError(f"{field_name} has empty org_slug or employee_no: {public_id!r}")
    return org_slug, employee_no


def build_credential(
    cred: CredentialConfig,
    *,
    scopes: Sequence[Scope | str] = (Scope.CONFIG_READ,),
) -> PatCredential:
    """Construct the tfrs-auth credential for the configured source.

    ``user_pat`` exchanges the user's PAT for a robot-scoped JWT via Manager's
    token-exchange endpoint (RFC 8693).  The token ``audience`` is
    ``robot:{public_id}`` where *public_id* identifies the target robot.
    """
    if isinstance(cred, UserPatConfig):
        org_slug, employee_no = _parse_public_id(cred.robot_public_id, field_name="robot_public_id")
        return PatCredential(
            pat=cred.pat.get_secret_value(),
            audience=robot_audience(org_slug, employee_no),
            scope=scopes_to_str(scopes),
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
