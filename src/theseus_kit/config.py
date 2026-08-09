"""Configuration: robot routing target + credential, loaded from env or a file.

Routing metadata is **explicit** (config-injected), never discovered from
Manager — editing a specific robot inherently requires declaring which one, and
embedded/internal deployments inject these values externally.

Credentials default to ``user_pat`` — the user's personal access token is
exchanged for a robot-scoped JWT via Manager's token-exchange endpoint.
All secret fields are pydantic ``SecretStr`` so default ``repr`` / logs never expose
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


class OAuthConfig(BaseModel):
    """OAuth 2.0 / 2.1 authorization (MCP-standard, no PAT).

    When no explicit PAT is configured, theseus-kit
    acts as an OAuth Protected Resource (RS): the MCP Client drives the
    authorization-code + PKCE flow against the TFRSManager AS, and theseus-kit
    validates the resulting Bearer token then forwards it directly to the target
    TFRobotServer (no token exchange — TFRobotServer natively accepts OAuth AS
    tokens per §10.1-new of the OAuth design).

    .. seealso:: :ref:`docs/auth-oauth-design.md` §4, §5.
    """

    kind: Literal["oauth"] = "oauth"
    authorization_server: str = Field(
        description="TFRSManager OAuth AS base URL（用于 PRM 发现 + Bearer 校验的 JWKS 获取）",
    )
    scopes: str = Field(
        default="config:read",
        description="Space-separated scope string（与 PAT 路径默认值一致）",
    )
    client_id: str | None = Field(
        default=None,
        description="预注册 client_id；None 时走 DCR / CIMD（MCP SDK 处理）",
    )
    redirect_uri: str | None = Field(
        default=None,
        description="STDIO 外部回调 URI（Topology B）；Topology A（HTTP/MCP Client）不需要",
    )
    resource_server_url: str | None = Field(
        default=None,
        description=(
            "theseus-kit PRM resource URL（Topology A HTTP 入口），"
            "用作 token audience 校验 + AuthSettings.resource_server_url；"
            "None 跳过 audience 校验（宽松模式，仅开发/调试）"
        ),
    )

    @field_validator("authorization_server")
    @classmethod
    def _validate_authorization_server_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"authorization_server must start with http:// or https://; got {value!r}")
        return value

    @field_validator("redirect_uri")
    @classmethod
    def _validate_redirect_uri_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError(f"redirect_uri must start with http:// or https://; got {value!r}")
        return value

    @field_validator("resource_server_url")
    @classmethod
    def _validate_resource_server_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError(f"resource_server_url must start with http:// or https://; got {value!r}")
        return value


CredentialConfig = Annotated[UserPatConfig | OAuthConfig, Field(discriminator="kind")]


class TheseusSettings(BaseSettings):
    """theseus-kit settings, loadable from env vars or a ``.env`` file.

    Env nesting uses ``__``. Examples::

        THESEUS_ROBOT__ROBOT_ID=robot-1
        THESEUS_ROBOT__NAMESPACE=default
        THESEUS_ROBOT__API_BASE_URL=https://api.example.com
        THESEUS_CREDENTIAL__KIND=user_pat
        THESEUS_CREDENTIAL__PAT=tfp_xxx
        THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID=myorg:12345
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
