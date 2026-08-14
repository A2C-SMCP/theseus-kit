"""MCP server composition root."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import Annotations
from pydantic import AnyHttpUrl, AnyUrl

from .config import OAuthConfig, TheseusSettings
from .models import (
    ConfigDetail,
    ConfigSummary,
    CreateDraftResponse,
    DraftValidateResponse,
    ListNodesResponse,
    LlmsDoc,
    PublishConfigResponse,
    SaveTemplateResponse,
    TemplateResponse,
    UpdateDraftResponse,
)

if TYPE_CHECKING:
    from .oauth import TheseusTokenVerifier

_INSTRUCTIONS = (
    "Inspect and manage TFRobot configuration. Read the exposed editing skills "
    "and version-specific llms.txt documentation before mutating configuration."
)

_WINDOW_NS = "window://com.a2c-smcp.theseus-kit"
_SKILL_NS = "skill://com.a2c-smcp.theseus-kit"


def _register_resources(mcp: FastMCP, settings: TheseusSettings) -> None:
    from .resources import build_recent, build_summary
    from .transport import RobotClient

    robot_id = settings.robot.robot_id

    @mcp.resource(
        f"{_WINDOW_NS}/config/summary",
        name="Config Summary",
        description="Compact robot identity and three-state configuration overview.",
        mime_type="application/json",
        annotations=Annotations(audience=["assistant"], priority=0.9),
    )
    async def config_summary() -> dict[str, Any]:
        async with RobotClient.from_settings(settings) as client:
            return await build_summary(client, robot_id)

    @mcp.resource(
        f"{_WINDOW_NS}/config/recent",
        name="Config Recent Detail",
        description="The most recently opened configuration detail.",
        mime_type="application/json",
        annotations=Annotations(audience=["assistant"], priority=0.8),
    )
    async def config_recent_detail() -> dict[str, Any]:
        async with RobotClient.from_settings(settings) as client:
            return await build_recent(client, robot_id)


def _make_skill_reader(skill_name: str, rel_path: str) -> Callable[[], str]:
    """Return a zero-arg callable that reads *skill_name*/*rel_path*.

    Uses a factory function so each call creates its own closure scope,
    avoiding the Python loop-variable-late-binding footgun.
    """
    from .skills import build_skill_resource

    def _reader() -> str:
        return build_skill_resource(skill_name, rel_path)

    return _reader


def _register_skill_resources(mcp: FastMCP) -> None:
    """Register all skill:// resources (root + sub) on *mcp*.

    A2C-SMCP skill.md §3 mode C ("resources") registrable shape, per the SDK's
    ``fastmcp_skill_stdio_server`` fixture: the skill ROOT resource declares
    the staging mode in ``_meta``; the Computer materializes the package by
    reading every sub-resource under the root's URI prefix.  ``SKILL.md``
    itself is therefore exposed BOTH at the root (for plain MCP clients, the
    SDK never reads root content in this mode) and as the ``SKILL.md``
    sub-resource (which is what the SDK stages as the package entry).
    """
    from . import __version__
    from .skills import SkillRegistry

    for skill in SkillRegistry.all():
        # -- Root (declaration node, also serves SKILL.md for plain clients) --
        main_rel = "SKILL.md"

        _reader = _make_skill_reader(skill.name, main_rel)

        mcp.resource(
            f"{_SKILL_NS}/{skill.name}",
            name=f"Skill: {skill.name}",
            description=skill.description,
            mime_type="text/markdown",
            annotations=Annotations(audience=["assistant"], priority=0.7),
            meta={"source": "resources", "version": __version__},
        )(_reader)

        # -- Sub-resources: SKILL.md + references/* + scripts/*, ... --
        _register_skill_sub_resource(mcp, skill.name, "SKILL.md")
        for rel_path in skill.sub_resources:
            _register_skill_sub_resource(mcp, skill.name, rel_path)

    # -- Legacy aliases (deprecated, lower priority) --
    _legacy_aliases = {
        "inspect-robot-config": "analyze-config",
        "edit-robot-draft": "tune-config",
        "publish-robot-config": "publish-config",
    }
    for legacy, new_name in _legacy_aliases.items():
        new_skill = SkillRegistry.get(new_name)
        if new_skill is None:
            continue

        _reader = _make_skill_reader(new_name, "SKILL.md")

        mcp.resource(
            f"{_SKILL_NS}/{legacy}",
            name=f"Skill: {legacy} (legacy)",
            description=new_skill.description,
            mime_type="text/markdown",
            annotations=Annotations(audience=["assistant"], priority=0.6),
            meta={
                "deprecated": True,
                "migrated_to": new_name,
            },
        )(_reader)


def _register_skill_sub_resource(mcp: FastMCP, skill_name: str, rel_path: str) -> None:
    """Register a single sub-resource for a skill.

    MIME type comes from a deterministic built-in extension mapping
    (A2C-SMCP skill.md §6.4) — never from the host OS registry.

    Sub-resources deliberately carry NO ``source`` meta: skill.md §3 declares
    the staging mode on the SKILL root only; sub-resources are discovered by
    URI prefix (``skill://<root>/**``) when the Computer stages the root.
    """
    from . import __version__
    from .skills import mime_for_rel_path

    mcp.resource(
        f"{_SKILL_NS}/{skill_name}/{rel_path}",
        name=f"Skill Ref: {skill_name}/{rel_path}",
        description=f"Reference for {skill_name}: {rel_path}",
        mime_type=mime_for_rel_path(rel_path),
        annotations=Annotations(audience=["assistant"], priority=0.6),
        meta={"version": __version__},
    )(_make_skill_reader(skill_name, rel_path))


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
        _register_resources(mcp, settings)
        _register_skill_resources(mcp)

    return mcp


# -- Resource notification --------------------------------------------------


def _notify(ctx: Context[Any, Any, Any] | None, resource: str) -> None:
    """Best-effort resource-updated notification.

    When *ctx* is available (tool was called through an MCP session), send a
    ``notifications/resources/updated`` for the given *resource* (one of
    ``"summary"`` or ``"recent"``).  Silently skips when no session context
    is available (e.g. in tests that call tool functions directly).
    """
    if ctx is None:
        return
    try:
        uri = AnyUrl(f"{_WINDOW_NS}/config/{resource}")
        ctx.request_context.session.send_resource_updated(uri)
    except Exception:
        # Never let a notification failure surface as a tool error.
        pass


# -- Tool registration -----------------------------------------------------


def _register_tools(mcp: FastMCP, settings: TheseusSettings) -> None:
    """Register the four progressive-disclosure read tools on *mcp*."""
    from .services.config_reader import ConfigReader
    from .services.draft_creator import DraftCreator
    from .services.draft_editor import DraftEditor
    from .services.draft_validator import DraftValidator
    from .services.llms_doc_reader import LlmsDocReader
    from .services.publisher import ConfigPublisher
    from .services.template_saver import TemplateSaver
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
        from .resources import set_last_locator

        reader = ConfigReader(robot_id=robot_id)
        async with RobotClient.from_settings(settings) as client:
            result = await reader.get_detail(
                client,
                locator=locator,
                select=select,
                depth=depth,
                max_bytes=max_bytes,
            )
        set_last_locator(locator)
        return result

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

    @mcp.tool(
        name="create_draft",
        description=(
            "Create a new draft configuration setting."
            " Use this FIRST when you need to add a new configuration node"
            " (a new LLM provider, a new tool, a new brain, etc.)."
            " Pass the scene (functional domain like LLM/BRAIN/TOOL),"
            " factory_name (as it appears in the LLMTEXT factory catalog),"
            " and a user-assigned setting_name. Optionally pass an initial"
            " config dictionary; otherwise the draft is created with factory"
            " defaults. Returns a setting_id and content_hash suitable for"
            " immediate use in update_draft. Requires config:write scope."
        ),
    )
    async def create_draft(
        scene: str,
        factory_name: str,
        setting_name: str,
        config: dict[str, Any] | None = None,
        ctx: Context[Any, Any, Any] | None = None,
    ) -> CreateDraftResponse:
        creator = DraftCreator(robot_id=robot_id)
        async with RobotClient.from_settings(settings) as client:
            result = await creator.create(
                client,
                scene=scene,
                factory_name=factory_name,
                setting_name=setting_name,
                config=config,
            )
        _notify(ctx, "summary")
        return result

    @mcp.tool(
        name="update_draft",
        description=(
            "Update an existing draft configuration setting."
            " Use this AFTER reading the draft with get_config_detail — pass"
            " its content_hash as expected_hash to protect against conflicting"
            " changes by another actor.  If another actor modified the draft"
            " since you read it, the call fails with a conflict error so you"
            " can re-read and retry.  Omit expected_hash to skip the conflict"
            " check (last-write-wins)."
            " Returns the updated draft with a new content_hash for the next"
            " update.  This tool NEVER publishes the draft; it only modifies"
            " the stored configuration.  Requires config:write scope."
        ),
    )
    async def update_draft(
        setting_id: int,
        setting_name: str,
        config: dict[str, Any],
        expected_hash: str | None = None,
        ctx: Context[Any, Any, Any] | None = None,
    ) -> UpdateDraftResponse:
        editor = DraftEditor(robot_id=robot_id)
        async with RobotClient.from_settings(settings) as client:
            result = await editor.update_draft(
                client,
                setting_id=setting_id,
                setting_name=setting_name,
                config=config,
                expected_hash=expected_hash,
            )
        # Resource notification: summary (draft revision changed) + recent (stale).
        _notify(ctx, "summary")
        _notify(ctx, "recent")
        return result

    @mcp.tool(
        name="publish_config",
        description=(
            "Publish ALL draft configuration to the online state."
            " This is a global, irreversible side-effect — it publishes the"
            " entire configuration tree starting from the ROBOT-scene draft."
            " Use acknowledge_publish=True to confirm you understand the"
            " consequences.  Optionally pass expected_root_hash (from a prior"
            " get_config_summary call) to prove you've read the current draft"
            " structure — the call will be refused if the draft root changed"
            " since you read it.  Requires config:publish scope (config:write"
            " alone is not sufficient).  On success returns the onlineRobotId."
        ),
    )
    async def publish_config(
        expected_root_hash: str | None = None,
        acknowledge_publish: bool = False,
        ctx: Context[Any, Any, Any] | None = None,
    ) -> PublishConfigResponse:
        publisher = ConfigPublisher(robot_id=robot_id)
        async with RobotClient.from_settings(settings) as client:
            result = await publisher.publish_config(
                client,
                expected_root_hash=expected_root_hash,
                acknowledge_publish=acknowledge_publish,
            )
        # Resource notification: both summary and recent are affected.
        _notify(ctx, "summary")
        _notify(ctx, "recent")
        return result

    @mcp.tool(
        name="save_template",
        description=(
            "Save a draft subtree as a reusable template."
            " Pass the draft's setting_id and a non-empty template_name."
            " The draft is NOT modified — this creates a new template from"
            " the current draft content.  Optionally pass expected_hash"
            " (from a prior get_config_detail call) to prove you've read"
            " the current draft — the call will be refused if the draft"
            " changed since you read it.  Requires config:write scope."
            " On success returns the new template_id and a locator suitable"
            " for get_template."
        ),
    )
    async def save_template(
        setting_id: int,
        template_name: str,
        expected_hash: str | None = None,
        ctx: Context[Any, Any, Any] | None = None,
    ) -> SaveTemplateResponse:
        saver = TemplateSaver(robot_id=robot_id)
        async with RobotClient.from_settings(settings) as client:
            result = await saver.save_template(
                client,
                setting_id=setting_id,
                template_name=template_name,
                expected_hash=expected_hash,
            )
        # Resource notification: summary (template count changed).
        _notify(ctx, "summary")
        return result

    @mcp.tool(
        name="validate_draft",
        description=(
            "Validate draft configuration before publishing."
            " Use this BEFORE publish_config to check for validation errors."
            " Call WITHOUT setting_id to run a full pre-release check on all"
            " drafts — this is the recommended pre-publish validation."
            " Call WITH setting_id to validate a specific node and its"
            " recursive dependencies.  Returns per-node validation results"
            " with pass/fail counts and detailed error messages.  A draft"
            " must pass validation before it can be published.  Requires"
            " config:write scope."
        ),
    )
    async def validate_draft(
        setting_id: int | None = None,
    ) -> DraftValidateResponse:
        validator = DraftValidator(robot_id=robot_id)
        async with RobotClient.from_settings(settings) as client:
            return await validator.validate(client, setting_id=setting_id)


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
