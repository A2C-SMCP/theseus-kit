"""Configuration: robot routing target + credential, loaded from env or a file.

Routing metadata is **explicit** (config-injected), never discovered from
Manager — editing a specific robot inherently requires declaring which one, and
embedded/internal deployments inject these values externally.

Credentials default to ``client_credentials`` (the robot's own machine
credential); ``user_pat`` is supported for the direct-user / future-OAuth path.
Both flow through the same ``tfrs_auth.Credential`` abstraction (see #18). All
secret fields are pydantic ``SecretStr`` so default ``repr`` / logs never expose
them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RobotTarget(BaseModel):
    """One target robot's routing identity + endpoints + TLS.

    None of these is the token ``audience``: the ``audience`` is a credential
    concern (``robot:{public_id}``) derived from the credential config. The
    ``rid`` here is the ``X-TF-RobotId`` value — a different identifier.
    """

    robot_id: str = Field(description="rid → X-TF-RobotId 与集群内路由（≠ public_id）")
    namespace: str = Field(description="租户 K8s Namespace → X-TF-Namespace")
    robot_type: str = Field(description="机器人类型（如 tfrobot / openclaw）→ X-TF-RobotType")
    api_base_url: str = Field(description="机器人 HTTP 入口 https://api.<clusterDomain>")
    manager_base_url: str = Field(description="TFRSManager 公共入口（POST /api/v1/oauth/token 的 host）")
    verify: bool = Field(default=True, description="HTTPS 证书校验")
    ca_bundle: Path | None = Field(default=None, description="自定义 CA bundle（自签 / 本地开发兜底）")
    timeout_s: float = Field(default=30.0, ge=1.0, description="HTTP 超时（秒）")

    @field_validator("api_base_url", "manager_base_url")
    @classmethod
    def _validate_url_scheme(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"URL must start with http:// or https://; got {value!r}")
        return value

    @field_validator("ca_bundle")
    @classmethod
    def _validate_ca_bundle(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_file():
            raise ValueError(f"ca_bundle file not found: {value}")
        return value


class ClientCredentialsConfig(BaseModel):
    """Robot's own machine credential (primary path, tfrs-auth 0.2.1+).

    For self-management the token ``audience`` is ``robot:{public_id}``
    — the robot exchanges a token scoped to itself — so no separate target
    identity is required.  ``machine_client_id`` is the ``public_id``
    (``{orgSlug}:{employeeNo}``), from which org_slug and employee_no are
    derived for caller == callee self-management.
    """

    kind: Literal["client_credentials"] = "client_credentials"
    machine_client_id: str = Field(
        description="机器人 machineClientId — public_id 格式（{orgSlug}:{employeeNo}）",
        pattern=r"^[a-z0-9-]+:[a-zA-Z0-9]+$",
    )
    machine_client_secret: SecretStr = Field(description="机器人 machineClientSecret")


class UserPatConfig(BaseModel):
    """User personal access token (direct-user / future-OAuth path).

    A user PAT can target any of the user's robots, so the target robot's
    ``public_id`` (``{orgSlug}:{employeeNo}``) must be named explicitly as the
    token ``audience``.
    """

    kind: Literal["user_pat"] = "user_pat"
    pat: SecretStr = Field(description="用户个人访问令牌（tfp_…）")
    robot_public_id: str = Field(
        description="目标机器人 public_id（{orgSlug}:{employeeNo}）→ audience robot:{public_id}",
        pattern=r"^[a-z0-9-]+:[a-zA-Z0-9]+$",
    )


CredentialConfig = Annotated[ClientCredentialsConfig | UserPatConfig, Field(discriminator="kind")]


class TheseusSettings(BaseSettings):
    """theseus-kit settings, loadable from env vars or a ``.env`` file.

    Env nesting uses ``__``. Examples::

        THESEUS_ROBOT__ROBOT_ID=robot-1
        THESEUS_ROBOT__NAMESPACE=default
        THESEUS_ROBOT__API_BASE_URL=https://api.example.com
        THESEUS_CREDENTIAL__KIND=client_credentials
        THESEUS_CREDENTIAL__MACHINE_CLIENT_ID=42
        THESEUS_CREDENTIAL__MACHINE_CLIENT_SECRET=tfp_xxx
    """

    model_config = SettingsConfigDict(
        env_prefix="theseus_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    robot: RobotTarget
    credential: CredentialConfig
