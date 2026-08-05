"""MCP server composition root."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl

from .config import OAuthConfig, TheseusSettings
from .models import (
    ConfigDetail,
    ConfigSummary,
    ListNodesResponse,
    LlmsDoc,
    TemplateResponse,
)

if TYPE_CHECKING:
    from .oauth import TheseusTokenVerifier

_INSTRUCTIONS = (
    "Inspect and manage TFRobot configuration. Read the exposed editing skills "
    "and version-specific llms.txt documentation before mutating configuration."
)


def create_mcp_server(settings: TheseusSettings | None = None) -> FastMCP:
    """Build the FastMCP instance, conditionally configured for OAuth.

    When *settings* carries an ``oauth`` credential, the server is wired with
    a :class:`TheseusTokenVerifier` (bearer-auth middleware + Protected Resource
    Metadata routes) so it can act as an OAuth RS (Topology A).  Otherwise the
    plain stdio server is returned unchanged.

    When *settings* is not ``None``, the four progressive-disclosure read tools
    (``get_config_summary``, ``list_config_nodes``, ``get_config_detail``,
    ``get_template``) are registered on the MCP instance.
    """
    if settings is not None and isinstance(settings.credential, OAuthConfig):
        cred: OAuthConfig = settings.credential

        resource_url = cred.resource_server_url
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(cred.authorization_server),
            resource_server_url=(AnyHttpUrl(resource_url) if resource_url is not None else None),
            required_scopes=sorted(_parse_space_separated(cred.scopes)),
        )

        verifier = _LazyOAuthTokenVerifier(
            authorization_server=cred.authorization_server,
            required_scope=cred.scopes,
            audience=resource_url,
        )

        mcp = FastMCP(
            name="theseus-kit",
            instructions=_INSTRUCTIONS,
            token_verifier=verifier,
            auth=auth,
        )
    else:
        mcp = FastMCP(name="theseus-kit", instructions=_INSTRUCTIONS)

    if settings is not None:
        _register_tools(mcp, settings)

    return mcp


# -- Tool registration -----------------------------------------------------


def _register_tools(mcp: FastMCP, settings: TheseusSettings) -> None:
    """Register the four progressive-disclosure read tools on *mcp*."""
    from .services.config_reader import ConfigReader
    from .services.llms_doc_reader import LlmsDocReader
    from .transport import RobotClient

    robot_id = settings.robot.robot_id

    @mcp.tool(
        name="get_config_summary",
        description=(
            "Get a compact overview of available TFRobot configuration states."
            " Use this FIRST when you need to discover what configurations exist."
            " Returns robot identity and a summary of each lifecycle state"
            " (draft, template, online) — whether each is present, node counts,"
            " and root locators.  Optionally pass 'state' to query a single state."
        ),
    )
    async def get_config_summary(
        state: str | None = None,
    ) -> ConfigSummary:
        reader = ConfigReader(robot_id=robot_id)
        async with RobotClient.from_settings(settings) as client:
            return await reader.get_summary(client, state=state)

    @mcp.tool(
        name="list_config_nodes",
        description=(
            "List configuration nodes at a given level of the configuration forest."
            " Use this AFTER get_config_summary to explore what's inside a state,"
            " scene, or factory.  Returns a paginated list of child nodes with"
            " locators you can pass to get_config_detail.  Supports cursor-based"
            " pagination and optional filters (scene, factory, name)."
        ),
    )
    async def list_config_nodes(
        parent: str | None = None,
        state: str | None = None,
        scene: str | None = None,
        factory: str | None = None,
        name: str | None = None,
        cursor: str | None = None,
        page_size: int = 50,
    ) -> ListNodesResponse:
        reader = ConfigReader(robot_id=robot_id)
        async with RobotClient.from_settings(settings) as client:
            return await reader.list_nodes(
                client,
                parent=parent,
                state=state,
                scene=scene,
                factory=factory,
                name=name,
                cursor=cursor,
                page_size=page_size,
            )

    @mcp.tool(
        name="get_config_detail",
        description=(
            "Get a bounded, redacted detail view of a single configuration node."
            " Use this AFTER list_config_nodes to read the actual configuration"
            " content.  Pass a 'locator' (from list_nodes or the summary) to"
            " identify the node.  The response is bounded by 'depth' (default 3)"
            " and 'max_bytes' (default 8192, max 32768).  When truncated,"
            " 'next_actions' tells you how to drill further.  Sensitive fields"
            " (passwords, tokens, keys) are redacted automatically."
        ),
    )
    async def get_config_detail(
        locator: str,
        select: str = "",
        depth: int = 3,
        max_bytes: int = 8192,
    ) -> ConfigDetail:
        reader = ConfigReader(robot_id=robot_id)
        async with RobotClient.from_settings(settings) as client:
            return await reader.get_detail(
                client,
                locator=locator,
                select=select,
                depth=depth,
                max_bytes=max_bytes,
            )

    @mcp.tool(
        name="get_template",
        description=(
            "Get a template configuration by ID.  With 'metadata_only=true'"
            " returns a compact summary (name, size, lifecycle).  With"
            " 'metadata_only=false' (default) returns the full configuration"
            " detail — same shape as get_config_detail.  Use this when you know"
            " the template ID (from list_config_nodes or the summary)."
        ),
    )
    async def get_template(
        template_id: str,
        metadata_only: bool = False,
        select: str = "",
        depth: int = 3,
        max_bytes: int = 8192,
    ) -> TemplateResponse:
        reader = ConfigReader(robot_id=robot_id)
        async with RobotClient.from_settings(settings) as client:
            return await reader.get_template(
                client,
                template_id=template_id,
                metadata_only=metadata_only,
                select=select,
                depth=depth,
                max_bytes=max_bytes,
            )

    @mcp.tool(
        name="get_llms_doc",
        description=(
            "Read the robot's runtime llms.txt configuration documentation."
            " Call WITHOUT a path FIRST to get the index (/llms.txt) — it lists"
            " available documentation pages.  Then call WITH a specific path"
            " (e.g. 'schema/brain') to read a particular page.  The content is"
            " bounded by max_bytes (default 8192, max 32768); use the index to"
            " decide which pages to load.  Use this when you need to understand"
            " what endpoints, scenes, factories, fields, and scopes are available"
            " for THIS specific robot version."
        ),
    )
    async def get_llms_doc(
        path: str = "",
        max_bytes: int = 8192,
    ) -> LlmsDoc:
        reader = LlmsDocReader()
        async with RobotClient.from_settings(settings) as client:
            return await reader.get_doc(client, path=path, max_bytes=max_bytes)


class _LazyOAuthTokenVerifier:
    """TokenVerifier that lazily discovers the AS and builds a TheseusTokenVerifier.

    FastMCP requires ``token_verifier`` at construction time (sync), but AS
    metadata discovery is async.  This shim defers the async discovery to the
    first ``verify_token`` call and caches the result under an async lock
    (single-flight).
    """

    def __init__(
        self,
        authorization_server: str,
        required_scope: str,
        audience: str | None = None,
    ) -> None:
        self._authorization_server = authorization_server
        self._required_scope = required_scope
        self._audience = audience
        self._verifier: TheseusTokenVerifier | None = None
        self._lock = asyncio.Lock()

    async def verify_token(self, token: str) -> AccessToken | None:
        if self._verifier is None:
            async with self._lock:
                if self._verifier is None:
                    from .oauth import build_token_verifier

                    self._verifier = await build_token_verifier(
                        self._authorization_server,
                        required_scope=self._required_scope,
                        audience=self._audience,
                    )
        result: AccessToken | None = await self._verifier.verify_token(token)
        return result


def _parse_space_separated(scopes: str) -> frozenset[str]:
    """Parse a space-separated scope string into an immutable set."""
    return frozenset(s for s in scopes.split() if s)


def main() -> None:
    """Run the MCP server over the portable stdio transport."""
    create_mcp_server().run(transport="stdio")
