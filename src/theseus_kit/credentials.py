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


def build_credential(
    cred: CredentialConfig,
    *,
    scopes: Sequence[Scope | str] = (Scope.CONFIG_READ,),
) -> ClientCredentials | PatCredential:
    """Construct the tfrs-auth credential for the configured source.

    ``client_credentials`` is self-management: the token ``audience`` is the
    robot's own ``machine_client_id`` (callee == caller). ``user_pat`` must name
    its target robot's Account.ID as the audience.
    """
    if isinstance(cred, ClientCredentialsConfig):
        # Self-management: callee (audience target) == the robot's own machine id.
        return ClientCredentials.for_robot(
            client_id=cred.machine_client_id,
            client_secret=cred.machine_client_secret.get_secret_value(),
            callee_robot_id=cred.machine_client_id,
            scope=list(scopes),
        )
    if isinstance(cred, UserPatConfig):
        return PatCredential(
            pat=cred.pat.get_secret_value(),
            audience=robot_audience(cred.robot_account_id),
            scope=scopes_to_str(scopes),
        )
    # Closed discriminated union — fail explicitly if a new kind is added without
    # wiring it here (decouples runtime validation from mypy type narrowing).
    raise ConfigError(f"unsupported credential kind: {cred!r}")
