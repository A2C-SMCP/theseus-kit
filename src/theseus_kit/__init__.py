"""theseus-kit MCP server."""

from theseus_kit.config import (
    ClientCredentialsConfig,
    CredentialConfig,
    OAuthConfig,
    RobotTarget,
    TheseusSettings,
    UserPatConfig,
)
from theseus_kit.errors import (
    AuthRejectedError,
    ConfigError,
    ConfigLocatorError,
    CredentialError,
    ExchangeUnavailableError,
    RobotApiError,
    RobotValidationError,
    RoutingConfigError,
    ScopeOrAudienceError,
    SubscriptionFrozenError,
    TheseusError,
)
from theseus_kit.models import (
    ConfigDetail,
    ConfigSummary,
    CursorData,
    ListNode,
    ListNodesResponse,
    NodeKind,
    PaginatedList,
    StateSummary,
    TemplateResponse,
    TFSResponse,
)
from theseus_kit.oauth import TheseusTokenVerifier, build_token_verifier
from theseus_kit.routing import RequestContext
from theseus_kit.server import create_mcp_server
from theseus_kit.services import ConfigReader
from theseus_kit.transport import RobotClient, StaticTokenSource

__version__ = "0.1.0.dev0"

__all__ = [
    "__version__",
    "TheseusSettings",
    "RobotTarget",
    "CredentialConfig",
    "ClientCredentialsConfig",
    "OAuthConfig",
    "UserPatConfig",
    "RequestContext",
    "RobotClient",
    "StaticTokenSource",
    "TheseusError",
    "ConfigError",
    "ConfigLocatorError",
    "RoutingConfigError",
    "CredentialError",
    "ScopeOrAudienceError",
    "SubscriptionFrozenError",
    "ExchangeUnavailableError",
    "AuthRejectedError",
    "RobotApiError",
    "RobotValidationError",
    "TheseusTokenVerifier",
    "build_token_verifier",
    "create_mcp_server",
    "ConfigReader",
    "ConfigSummary",
    "StateSummary",
    "ListNodesResponse",
    "ListNode",
    "NodeKind",
    "ConfigDetail",
    "TemplateResponse",
    "CursorData",
    "TFSResponse",
    "PaginatedList",
]
