"""Progressive-disclosure configuration reader (Application Service layer).

Implements the four-tool read surface freeze-dried in
``docs/progressive-disclosure.md`` (#2 / #4):

* ``get_config_summary`` — compact robot-identity + lifecycle overview
* ``list_config_nodes`` — paginated, filterable forest listing
* ``get_config_detail`` — bounded, redacted subtree view
* ``get_template`` — template metadata or full detail

Every call is **live** (no snapshot cache).  The ``_meta`` block on every
response carries at least ``revision`` and ``fetched_at``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import AliasGenerator, BaseModel, ConfigDict
from pydantic import Field as PydField
from pydantic.alias_generators import to_camel

from theseus_kit.errors import ConfigLocatorError
from theseus_kit.models import (
    ConfigDetail,
    ConfigSummary,
    CursorData,
    DraftTopology,
    ListMeta,
    ListNode,
    ListNodesResponse,
    NodeKind,
    ResponseMeta,
    RobotIdentity,
    StateSummary,
    TemplateResponse,
    compute_config_hash,
    decode_cursor,
    encode_cursor,
)
from theseus_kit.redaction import redact_sensitive_fields
from theseus_kit.transport import RobotClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_STATES = frozenset({"draft", "online", "template"})
_LOCATOR_PREFIX = "tcfg:"
_FACTORY_BASE = "/v1/factory"

# Mapping from state to its factory prefix under /v1/factory.
_STATE_ENDPOINT: dict[str, str] = {
    "draft": f"{_FACTORY_BASE}/drafts",
    "online": f"{_FACTORY_BASE}/online",
    "template": f"{_FACTORY_BASE}/templates",
}

# Locator-part index for each node kind.
_KIND_INDEX: dict[int, NodeKind] = {
    0: NodeKind.STATE,
    1: NodeKind.SCENE,
    2: NodeKind.FACTORY,
    3: NodeKind.SETTING,
}

# -- JSON Pointer helpers --------------------------------------------------


def _json_pointer_get(doc: dict[str, Any], pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer *pointer* within *doc*.

    Returns the located value or ``None`` when the pointer targets a
    non-existent path.
    """
    if not pointer or pointer == "":
        return doc
    tokens = pointer.lstrip("/").split("/")
    current: Any = doc
    for raw in tokens:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(token)
        elif isinstance(current, list):
            try:
                idx = int(token)
            except (ValueError, TypeError):
                return None
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            return None
        if current is None:
            return None
    return current


# -- Subtree bounding ------------------------------------------------------


def _bound_subtree(
    value: Any,
    *,
    depth: int,
    max_bytes: int,
    json_pointer: str = "",
) -> dict[str, Any]:
    """Return a bounded, truncated view of *value*.

    Returns a dict with keys ``subtree``, ``truncated``, ``truncated_at``,
    ``bytes_returned``, ``bytes_estimated_total``, and ``next_actions``.
    """
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    total_bytes = len(serialized.encode("utf-8"))

    # Apply depth bounding first — this is always active.
    depth_bounded = _apply_depth(value, depth)

    if total_bytes <= max_bytes:
        # Size fits, but depth may have changed the shape.
        serialized_result = json.dumps(depth_bounded, ensure_ascii=False, default=str)
        return {
            "subtree": depth_bounded,
            "truncated": False,
            "truncated_at": None,
            "bytes_returned": len(serialized_result.encode("utf-8")),
            "bytes_estimated_total": total_bytes,
            "next_actions": None,
        }

    # Value exceeds budget — walk the tree and drop subtrees that push us
    # over max_bytes.
    result: Any = _truncate_at_budget(depth_bounded, depth, max_bytes, json_pointer)

    serialized_result = json.dumps(result, ensure_ascii=False, default=str)
    returned = len(serialized_result.encode("utf-8"))

    # Determine where the cut happened (first elided key / index).
    truncated_at = _find_truncation_point(depth_bounded, result, json_pointer)

    next_actions: list[dict[str, Any]] = []
    if truncated_at:
        next_actions.append({"action": "get_config_detail", "select": truncated_at})
        next_actions.append({"action": "list_config_nodes", "at": truncated_at})

    return {
        "subtree": result,
        "truncated": True,
        "truncated_at": truncated_at,
        "bytes_returned": returned,
        "bytes_estimated_total": total_bytes,
        "next_actions": next_actions,
    }


def _apply_depth(value: Any, depth: int) -> Any:
    """Apply depth limit: return scalar/placeholder at depth 0, recurse otherwise."""
    if depth <= 0:
        if isinstance(value, (dict, list)):
            return _placeholder(value)
        return value
    if isinstance(value, dict):
        return {k: _apply_depth(v, depth - 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_apply_depth(item, depth - 1) for item in value]
    return value


def _truncate_at_budget(
    value: Any,
    depth: int,
    max_bytes: int,
    pointer: str,
) -> Any:
    """Recursively walk *value*; replace subtrees that overflow the budget.

    Depth limit is applied first (0 = scalar only, no further descent).
    """
    if depth <= 0:
        if isinstance(value, (dict, list)):
            return _placeholder(value)
        return value

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in value.items():
            child_ptr = f"{pointer}/{_escape_ptr(k)}"
            candidate = _truncate_at_budget(v, depth - 1, max_bytes, child_ptr)
            # Test: would adding this key overflow?
            trial = dict(result)
            trial[k] = candidate
            if len(json.dumps(trial, ensure_ascii=False, default=str).encode("utf-8")) > max_bytes:
                result[k] = _placeholder(v)
                break
            result[k] = candidate
        return result

    if isinstance(value, list):
        result_list: list[Any] = []
        for i, item in enumerate(value):
            child_ptr = f"{pointer}/{i}"
            candidate = _truncate_at_budget(item, depth - 1, max_bytes, child_ptr)
            trial_list = list(result_list)
            trial_list.append(candidate)
            if len(json.dumps(trial_list, ensure_ascii=False, default=str).encode("utf-8")) > max_bytes:
                break
            result_list.append(candidate)
        return result_list

    return value


def _placeholder(value: Any) -> str:
    """Human-readable placeholder for a truncated subtree."""
    if isinstance(value, dict):
        return "{…} <<truncated>>"
    if isinstance(value, list):
        return "[…] <<truncated>>"
    return "<<truncated>>"


def _find_truncation_point(original: Any, result: Any, pointer: str) -> str | None:
    """Find the JSON Pointer where *result* first diverges from *original*."""
    if type(original) is not type(result):
        return pointer or ""
    if isinstance(original, dict) and isinstance(result, dict):
        for k in original:
            if k not in result:
                return f"{pointer}/{_escape_ptr(k)}"
            child_ptr = f"{pointer}/{_escape_ptr(k)}"
            child_result = _find_truncation_point(original[k], result[k], child_ptr)
            if child_result is not None:
                return child_result
        return None
    if isinstance(original, list) and isinstance(result, list):
        for i in range(len(original)):
            if i >= len(result):
                return f"{pointer}/{i}"
            child_result = _find_truncation_point(original[i], result[i], f"{pointer}/{i}")
            if child_result is not None:
                return child_result
        return None
    return None


def _escape_ptr(segment: str) -> str:
    """Escape *segment* for use in an RFC 6901 JSON Pointer token."""
    return segment.replace("~", "~0").replace("/", "~1")


# -- Locator parsing -------------------------------------------------------


def parse_locator(locator: str) -> tuple[str, list[str]]:
    """Parse a ``tcfg:`` locator into ``(state, parts)``.

    Raises :class:`ConfigLocatorError` when the locator is malformed or
    references an unknown state.

    >>> parse_locator("tcfg:draft")
    ('draft', [])
    >>> parse_locator("tcfg:draft/brain/brain/42")
    ('draft', ['brain', 'brain', '42'])
    """
    if not locator.startswith(_LOCATOR_PREFIX):
        raise ConfigLocatorError(f"locator must start with '{_LOCATOR_PREFIX}'; got {locator!r}")

    rest = locator[len(_LOCATOR_PREFIX) :]
    if not rest:
        raise ConfigLocatorError(f"missing state in locator {locator!r}")

    # Split on "/" — the first segment is the state.
    segments = rest.split("/")
    state = segments[0]
    if state not in _VALID_STATES:
        raise ConfigLocatorError(
            f"unknown state {state!r} in locator {locator!r}; expected one of {sorted(_VALID_STATES)}"
        )

    parts = segments[1:] if len(segments) > 1 else []
    return state, parts


def locator_kind(parts: list[str]) -> NodeKind:
    """Determine the :class:`NodeKind` from the number of locator parts."""
    return _KIND_INDEX.get(len(parts), NodeKind.SETTING)


# ---------------------------------------------------------------------------
# ConfigReader
# ---------------------------------------------------------------------------


class ConfigReader:
    """Read TFRobotServer configuration via the progressive-disclosure contract.

    Wraps a :class:`RobotClient` and maps its typed HTTP methods to the
    four-tool read surface.  Every method accepts a *client* so callers
    control lifecycle (``async with RobotClient(...) as client:``).
    """

    def __init__(self, robot_id: str) -> None:
        self._robot_id = robot_id

    # -- get_config_summary -------------------------------------------------

    async def get_summary(
        self,
        client: RobotClient,
        state: str | None = None,
    ) -> ConfigSummary:
        """Build the state-of-the-world overview."""
        states_to_query = [state] if state else ["draft", "online", "template"]
        states: dict[str, StateSummary] = {}
        now = datetime.now(UTC).isoformat()

        for st in states_to_query:
            states[st] = await self._summarize_state(client, st)

        return ConfigSummary(
            robot_identity=RobotIdentity(robot_id=self._robot_id),
            states=states,
            **{"_meta": ResponseMeta(fetched_at=now)},
        )

    # -- get_topology ---------------------------------------------------------

    async def get_topology(self, client: RobotClient) -> DraftTopology:
        """Retrieve the draft configuration reference graph.

        Calls ``GET /v1/factory/drafts/topology`` (TFRobotServer ≥ 0.3.0-rc2).
        Returns a :class:`DraftTopology` with roots, orphans, and a compact
        adjacency-list node map.
        """
        resp = await client.get_model(
            f"{_FACTORY_BASE}/drafts/topology",
            DraftTopology,
        )
        return resp.data

    async def _summarize_state(self, client: RobotClient, state: str) -> StateSummary:
        """Build a :class:`StateSummary` for one lifecycle state."""
        root_locator = f"{_LOCATOR_PREFIX}{state}"
        try:
            scenes = await self._fetch_scene_list(client, state)
        except Exception:
            return StateSummary(present=False, root_locator=root_locator, status="unknown")

        present = len(scenes) > 0
        if not present:
            return StateSummary(present=False, root_locator=root_locator)

        # Templates get a count.
        count: int | None = None
        if state == "template" and present:
            try:
                list_resp = await client.get_paginated(
                    f"{_STATE_ENDPOINT['template']}/query",
                    _AnyDict,
                    page=1,
                    page_size=1,
                )
                count = list_resp.data.total if list_resp.data else 0
            except Exception:
                count = None

        # Draft: use topology endpoint for accurate dirty detection.
        status: str | None = None
        if state == "draft":
            try:
                topo = await self.get_topology(client)
                if len(topo.roots) > 1:
                    status = "dirty"
            except Exception:
                # Fall back to scene-count heuristic when topology is unavailable.
                if len(scenes) > 1:
                    status = "dirty"

        return StateSummary(
            present=True,
            root_locator=root_locator,
            status=status,
            count=count,
        )

    # -- list_config_nodes --------------------------------------------------

    async def list_nodes(  # noqa: C901, PLR0913
        self,
        client: RobotClient,
        *,
        parent: str | None = None,
        state: str | None = None,
        scene: str | None = None,
        factory: str | None = None,
        name: str | None = None,
        cursor: str | None = None,
        page_size: int = 50,
    ) -> ListNodesResponse:
        """Paginate children of a forest node."""

        page_size = max(1, min(page_size, 200))
        now = datetime.now(UTC).isoformat()  # noqa: DTZ005
        filters: dict[str, str] = {}

        # Decode cursor if present.
        offset = 0
        effective_parent = parent
        effective_state = state
        if cursor:
            cd = decode_cursor(cursor)
            effective_parent = cd.parent
            effective_state = cd.state
            filters = cd.filters
            offset = cd.offset
            revision_at_issue = cd.revision_at_issue
        else:
            revision_at_issue = ""

        # Determine state.
        resolved_state: str | None = effective_state
        if effective_parent:
            parsed_state, parent_parts = parse_locator(effective_parent)
            resolved_state = resolved_state or parsed_state
        if resolved_state is None or resolved_state not in _VALID_STATES:
            # No parent, no state → return the three forest roots.
            return self._forest_roots(now)

        # Build filters from explicit params (cursor takes precedence).
        if not cursor:
            if scene:
                filters["scene"] = scene
            if factory:
                filters["factory"] = factory
            if name:
                filters["name"] = name

        # Build effective parent parts.
        if effective_parent:
            _, parent_parts = parse_locator(effective_parent)
        else:
            parent_parts = []

        kind = locator_kind(parent_parts)

        if kind == NodeKind.STATE:
            return await self._list_scenes(client, resolved_state, parent_parts, offset, page_size, now, filters)
        if kind == NodeKind.SCENE:
            return await self._list_factories(client, resolved_state, parent_parts, offset, page_size, now, filters)
        if kind == NodeKind.FACTORY:
            return await self._list_settings(
                client, resolved_state, parent_parts, offset, page_size, now, filters, revision_at_issue
            )
        # SETTING — no children.
        return ListNodesResponse(meta=ListMeta(fetched_at=now, filters=filters), **{"_meta": None})

    def _forest_roots(self, now: str) -> ListNodesResponse:
        """Return the three forest root nodes (no parent, no state)."""
        nodes: list[ListNode] = []
        for st in sorted(_VALID_STATES):
            nodes.append(
                ListNode(
                    locator=f"{_LOCATOR_PREFIX}{st}",
                    state=st,
                    kind=NodeKind.STATE,
                    name=st,
                )
            )
        return ListNodesResponse(nodes=nodes, meta=ListMeta(fetched_at=now))

    async def _list_scenes(
        self,
        client: RobotClient,
        state: str,
        _parent_parts: list[str],
        offset: int,
        page_size: int,
        now: str,
        filters: dict[str, str],
    ) -> ListNodesResponse:
        """List scenes for *state*."""
        scenes = await self._fetch_scene_list(client, state)

        # Apply scene filter.
        scene_filter = filters.get("scene")
        if scene_filter:
            scenes = [s for s in scenes if scene_filter.lower() in s.lower()]

        # Paginate.
        paged, next_offset = _paginate_list(scenes, offset, page_size)
        nodes: list[ListNode] = []
        for s in paged:
            nodes.append(
                ListNode(
                    locator=f"{_LOCATOR_PREFIX}{state}/{s}",
                    state=state,
                    kind=NodeKind.SCENE,
                    name=s,
                )
            )

        next_cursor = None
        if next_offset is not None:
            next_cursor = encode_cursor(
                CursorData(
                    parent=f"{_LOCATOR_PREFIX}{state}",
                    state=state,
                    filters=filters,
                    offset=next_offset,
                    revision_at_issue="",
                )
            )

        return ListNodesResponse(nodes=nodes, next_cursor=next_cursor, meta=ListMeta(fetched_at=now, filters=filters))

    async def _list_factories(
        self,
        client: RobotClient,
        state: str,
        parent_parts: list[str],
        offset: int,
        page_size: int,
        now: str,
        filters: dict[str, str],
    ) -> ListNodesResponse:
        """List factories within a scene."""
        scene_name = parent_parts[0]
        base = _STATE_ENDPOINT[state]
        parent_locator = f"{_LOCATOR_PREFIX}{state}/{scene_name}"

        resp = await client.get_model(f"{base}/{scene_name}/factories", _FactoryList)
        factory_names: list[str] = resp.data.factory_names if resp.data else []

        # Apply factory filter.
        factory_filter = filters.get("factory")
        if factory_filter:
            factory_names = [f for f in factory_names if factory_filter.lower() in f.lower()]

        paged, next_offset = _paginate_list(factory_names, offset, page_size)
        nodes: list[ListNode] = []
        for f in paged:
            nodes.append(
                ListNode(
                    locator=f"{parent_locator}/{f}",
                    state=state,
                    kind=NodeKind.FACTORY,
                    name=f,
                )
            )

        next_cursor = None
        if next_offset is not None:
            next_cursor = encode_cursor(
                CursorData(
                    parent=parent_locator,
                    state=state,
                    filters=filters,
                    offset=next_offset,
                    revision_at_issue="",
                )
            )

        return ListNodesResponse(nodes=nodes, next_cursor=next_cursor, meta=ListMeta(fetched_at=now, filters=filters))

    async def _list_settings(  # noqa: C901, PLR0913
        self,
        client: RobotClient,
        state: str,
        parent_parts: list[str],
        offset: int,
        page_size: int,
        now: str,
        filters: dict[str, str],
        revision_at_issue: str,
    ) -> ListNodesResponse:
        """List settings within a scene+factory via the query endpoint."""
        scene_name = parent_parts[0]
        factory_name = parent_parts[1]
        base = _STATE_ENDPOINT[state]
        parent_locator = f"{_LOCATOR_PREFIX}{state}/{scene_name}/{factory_name}"

        if state == "template":
            items, _total = await self._fetch_template_settings(
                client, base, scene_name, factory_name, filters, offset, page_size
            )
        else:
            items = await self._fetch_draft_online_settings(client, base, scene_name, factory_name, filters)

        paged, next_offset = _paginate_list(items, offset, page_size)

        # Capture the current revision from the first item for drift detection.
        current_revision: str = items[0].get("factory_version", "") if items else ""

        nodes: list[ListNode] = []
        for item in paged:
            sid = item.get("setting_id", "")
            name_val = item.get("setting_name") or item.get("name", "")
            config = item.get("config", {})
            size_bytes = len(json.dumps(config, ensure_ascii=False, default=str).encode("utf-8"))
            nodes.append(
                ListNode(
                    locator=f"{parent_locator}/{sid}",
                    state=state,
                    kind=NodeKind.SETTING,
                    name=name_val,
                    size_bytes=size_bytes,
                    revision=item.get("factory_version"),
                )
            )

        next_cursor: str | None = None
        if next_offset is not None:
            next_cursor = encode_cursor(
                CursorData(
                    parent=parent_locator,
                    state=state,
                    filters=filters,
                    offset=next_offset,
                    revision_at_issue=current_revision,
                )
            )

        # Drift: the revision changed between the cursor's snapshot and now.
        drift = bool(revision_at_issue and current_revision and revision_at_issue != current_revision)

        return ListNodesResponse(
            nodes=nodes,
            next_cursor=next_cursor,
            drift=drift,
            meta=ListMeta(fetched_at=now, filters=filters),
        )

    @staticmethod
    async def _fetch_scene_list(client: RobotClient, state: str) -> list[str]:
        """Fetch the list of scenes for *state*.

        TFRobotServer returns ``TFSResponse[list[str]]`` — ``data`` is a raw
        JSON array, not an object.  We parse the envelope by hand.
        """
        base = _STATE_ENDPOINT[state]
        resp = await client.get(f"{base}/scenes")
        body: dict[str, Any] = resp.json()
        code: int = body.get("code", 0)
        if code != 200:
            from theseus_kit.errors import RobotApiError

            raise RobotApiError(
                f"scenes endpoint returned code {code}: {body.get('message', '')}",
                status_code=code,
            )
        data = body.get("data", [])
        if isinstance(data, list):
            return [str(item) for item in data]
        return []

    @staticmethod
    async def _fetch_template_settings(
        client: RobotClient,
        base: str,
        scene_name: str,
        factory_name: str,
        filters: dict[str, str],
        offset: int,  # noqa: ARG004
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch template settings via the paginated query endpoint."""
        from urllib.parse import urlencode

        name_filter = filters.get("name")
        query_params: list[tuple[str, str]] = [
            ("scene", scene_name),
            ("factoryName", factory_name),
        ]
        if name_filter:
            query_params.append(("settingName", name_filter))

        # Build path with filter params; get_paginated adds page & pageSize.
        path = f"{base}/query?{urlencode(query_params)}"
        page = offset + 1

        resp = await client.get_paginated(path, _SettingDto, page=page, page_size=page_size)
        items: list[dict[str, Any]] = []
        if resp.data and hasattr(resp.data, "items"):
            for item in resp.data.items:
                if hasattr(item, "model_dump"):
                    items.append(item.model_dump())
                elif isinstance(item, dict):
                    items.append(item)
        total: int = resp.data.total if resp.data else 0
        return items, total

    @staticmethod
    async def _fetch_draft_online_settings(
        client: RobotClient,
        base: str,
        scene_name: str,
        factory_name: str,
        filters: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Fetch draft/online settings via the query endpoint (returns a list).

        The draft/online query endpoints return ``TFSResponse[list[Dto]]``,
        not wrapped in a paginated object.  We use a raw GET and parse the
        envelope by hand.
        """
        from urllib.parse import urlencode

        name_filter = filters.get("name")
        query_params: list[tuple[str, str]] = [
            ("scene", scene_name),
            ("factoryName", factory_name),
        ]
        if name_filter:
            query_params.append(("settingName", name_filter))

        path = f"{base}/query?{urlencode(query_params)}"
        resp = await client.get(path)
        body: dict[str, Any] = resp.json()
        code: int = body.get("code", 0)
        if code != 200:
            from theseus_kit.errors import RobotApiError

            raise RobotApiError(
                f"robot returned code {code}: {body.get('message', '')}",
                status_code=code,
            )
        data = body.get("data", [])
        if not isinstance(data, list):
            return []
        # Normalise camelCase API keys → snake_case via _SettingDto.
        return [_SettingDto.model_validate(item).model_dump() if isinstance(item, dict) else {} for item in data]

    # -- get_config_detail --------------------------------------------------

    async def get_detail(  # noqa: C901, PLR0913
        self,
        client: RobotClient,
        *,
        locator: str,
        select: str = "",
        depth: int = 3,
        max_bytes: int = 8192,
    ) -> ConfigDetail:
        """Return a bounded, redacted subtree for *locator*."""
        state, parts = parse_locator(locator)
        kind = locator_kind(parts)
        max_bytes = max(1024, min(max_bytes, 32768))
        depth = max(0, depth)
        now = datetime.now(UTC).isoformat()  # noqa: DTZ005

        if kind != NodeKind.SETTING:
            raise ConfigLocatorError(
                f"get_config_detail requires a setting locator (3 parts); "
                f"got {kind.value} with {len(parts)} part(s): {locator!r}"
            )

        _scene_name = parts[0]  # validated by kind check above
        _factory_name = parts[1]
        setting_id = parts[2]
        base = _STATE_ENDPOINT[state]

        # Fetch the full setting DTO.
        resp = await client.get_model(f"{base}/{setting_id}", _SettingDto)
        dto: Any = resp.data
        raw: dict[str, Any] = dto.model_dump() if hasattr(dto, "model_dump") else dto
        config: dict[str, Any] = raw.get("config", {})

        # Apply 'select' — drill into config via JSON Pointer.
        if select:
            selected = _json_pointer_get(config, select)
            target: Any = selected if selected is not None else {}
        else:
            target = config

        # Redaction FIRST (spec § "Sensitive-field redaction": redaction is
        # applied before size measurement and truncation, so a field full of
        # secrets collapses to "<<redacted>>" and never overflows the budget).
        redacted_obj, redacted_paths = redact_sensitive_fields(target)

        # Compute content hash of the original config (before redaction/bounding)
        # so the caller can use it for optimistic concurrency in update_draft.
        config_hash = compute_config_hash(config)

        # Apply depth + max_bytes bounding on the redacted object.
        bounds = _bound_subtree(redacted_obj, depth=depth, max_bytes=max_bytes)

        bytes_returned: int = bounds["bytes_returned"]
        bytes_estimated_total: int | None = bounds["bytes_estimated_total"] if bounds["truncated"] else None

        return ConfigDetail(
            locator=locator,
            state=state,
            revision=raw.get("factory_version"),
            subtree=bounds["subtree"],
            truncated=bounds["truncated"],
            truncated_at=bounds.get("truncated_at"),
            bytes_returned=bytes_returned,
            bytes_estimated_total=bytes_estimated_total,
            redacted=redacted_paths,
            next_actions=bounds.get("next_actions"),
            content_hash=config_hash,
            **{"_meta": ResponseMeta(fetched_at=now)},
        )

    # -- get_template -------------------------------------------------------

    async def get_template(  # noqa: C901, PLR0913
        self,
        client: RobotClient,
        *,
        template_id: str,
        metadata_only: bool = False,
        select: str = "",
        depth: int = 3,
        max_bytes: int = 8192,
    ) -> TemplateResponse:
        """Return template metadata or full detail."""
        max_bytes = max(1024, min(max_bytes, 32768))
        depth = max(0, depth)
        now = datetime.now(UTC).isoformat()  # noqa: DTZ005
        base = _STATE_ENDPOINT["template"]
        root_locator = f"{_LOCATOR_PREFIX}template/{template_id}"

        try:
            resp = await client.get_model(f"{base}/{template_id}", _SettingDto)
        except Exception:
            raise ConfigLocatorError(f"template {template_id!r} not found") from None

        dto: Any = resp.data
        raw: dict[str, Any] = dto.model_dump() if hasattr(dto, "model_dump") else dto
        config: dict[str, Any] = raw.get("config", {})

        size_bytes = len(json.dumps(config, ensure_ascii=False, default=str).encode("utf-8"))

        if metadata_only:
            return TemplateResponse(
                template_id=template_id,
                name=raw.get("template_name") or raw.get("setting_name"),
                lifecycle="template",
                size_bytes=size_bytes,
                root_locator=root_locator,
                state="template",
                revision=raw.get("factory_version"),
                meta=ResponseMeta(fetched_at=now),
            )

        # Full detail — same logic as get_detail (redaction first, then bounding).
        if select:
            selected = _json_pointer_get(config, select)
            target = selected if selected is not None else {}
        else:
            target = config

        redacted_obj, redacted_paths = redact_sensitive_fields(target)
        bounds = _bound_subtree(redacted_obj, depth=depth, max_bytes=max_bytes)

        return TemplateResponse(
            template_id=template_id,
            name=raw.get("template_name") or raw.get("setting_name"),
            lifecycle="template",
            size_bytes=size_bytes,
            root_locator=root_locator,
            state="template",
            revision=raw.get("factory_version"),
            subtree=redacted_obj,
            truncated=bounds["truncated"],
            truncated_at=bounds.get("truncated_at"),
            bytes_returned=bounds["bytes_returned"],
            bytes_estimated_total=bounds["bytes_estimated_total"] if bounds["truncated"] else None,
            redacted=redacted_paths,
            next_actions=bounds.get("next_actions"),
            content_hash=compute_config_hash(config),
            meta=ResponseMeta(fetched_at=now),
        )


# ---------------------------------------------------------------------------
# Internal DTO helpers (lightweight pydantic models for API parsing)
# ---------------------------------------------------------------------------


class _FactoryList(BaseModel):
    """Adapter for ``GET .../{scene}/factories`` → ``TFSResponse[{factory_names}]``."""

    factory_names: list[str] = PydField(default_factory=list)
    model_config = ConfigDict(
        extra="allow",
        alias_generator=AliasGenerator(to_camel),
        populate_by_name=True,
    )


class _SettingDto(BaseModel):
    """Minimal DTO for parsing setting responses from TFRobotServer.

    Uses ``extra="allow"`` so we can access fields without listing every
    TFRobotServer DTO attribute here.
    """

    setting_id: int = 0
    name: str = ""
    setting_name: str = ""
    scene: str = ""
    config: dict[str, Any] = PydField(default_factory=dict)
    factory_version: str = ""
    template_name: str = ""

    model_config = ConfigDict(
        extra="allow",
        alias_generator=AliasGenerator(to_camel),
        populate_by_name=True,
    )


class _AnyDict(BaseModel):
    """Catch-all DTO for endpoints where we only need count / minimal info."""

    model_config = ConfigDict(extra="allow")


# -- Internal pagination helper --------------------------------------------


def _paginate_list(
    items: list[Any],
    offset: int,
    page_size: int,
) -> tuple[list[Any], int | None]:
    """Slice *items* at *offset*, returning ``(page, next_offset | None)``."""
    total = len(items)
    start = offset
    end = min(start + page_size, total)
    page = items[start:end]
    next_offset = end if end < total else None
    return page, next_offset
