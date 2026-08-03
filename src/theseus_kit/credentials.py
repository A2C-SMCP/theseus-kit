"""Build a tfrs-auth ``Credential`` from theseus-kit config.

Credential-source agnostic: ``client_credentials`` (robot's own machine
credential, primary) and ``user_pat`` both yield a ``tfrs_auth.Credential`` that
the same ``AsyncCachingTokenSource`` drives — the reuse point for #18 OAuth.
theseus-kit never reimplements the exchange / refresh / retry algorithm.
"""

from __future__ import annotations

from collections.abc import Sequence

from tfrs_auth import ClientCredentials, PatCredential, Scope, robot_audience, scopes_to_str

from .config import ClientCredentialsConfig, CredentialConfig, UserPatConfig
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
) -> ClientCredentials | PatCredential:
    """Construct the tfrs-auth credential for the configured source.

    ``client_credentials`` is self-management: the token ``audience`` is the
    robot's own ``public_id`` (callee == caller).  ``user_pat`` must name its
    target robot's ``public_id`` as the audience.
    """
    if isinstance(cred, ClientCredentialsConfig):
        # Self-management: callee (audience target) == the robot's own identity.
        org_slug, employee_no = _parse_public_id(cred.machine_client_id, field_name="machine_client_id")
        return ClientCredentials.for_robot(
            client_id=cred.machine_client_id,
            client_secret=cred.machine_client_secret.get_secret_value(),
            callee_org_slug=org_slug,
            callee_employee_no=employee_no,
            scope=list(scopes),
        )
    if isinstance(cred, UserPatConfig):
        org_slug, employee_no = _parse_public_id(cred.robot_public_id, field_name="robot_public_id")
        return PatCredential(
            pat=cred.pat.get_secret_value(),
            audience=robot_audience(org_slug, employee_no),
            scope=scopes_to_str(scopes),
        )
    # Closed discriminated union — fail explicitly if a new kind is added without
    # wiring it here (decouples runtime validation from mypy type narrowing).
    raise ConfigError(f"unsupported credential kind: {cred!r}")
