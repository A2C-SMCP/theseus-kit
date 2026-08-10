"""Typed response models for the TFRobotServer API and progressive-disclosure tools.

Mirrors the wire contract of TFRobotServer's ``dtos/global_dto.py`` —
``TFSResponse[T]`` wrapping ``{code, message, data}`` and
``PaginatedList[T]`` for endpoints that return ``{items, total}``.

The progressive-disclosure models (Issue #4) define the four-tool read
surface freeze-dried in ``docs/progressive-disclosure.md``: ``ConfigSummary``,
``ListNodesResponse``, ``ConfigDetail``, and ``TemplateResponse``, plus the
``CursorData`` helper for stateless pagination.
"""

from __future__ import annotations

import base64
import hashlib
import json as _json
from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)  # bounded TypeVar for model-type-parameterised functions

# ---------------------------------------------------------------------------
# Response wrapper
# ---------------------------------------------------------------------------


class TFSResponse(BaseModel, Generic[T]):
    """TFRobotServer generic response envelope.

    Wire format (from ``TFRobotServer/dtos/global_dto.py``)::

        {"code": 200, "message": "Success", "data": <T>}

    *code* mirrors the HTTP status code.  *data* defaults to an empty
    dict (``{}``) for endpoints that carry no payload.
    """

    code: int = 200
    message: str = "Success"
    data: T = {}  # type: ignore[assignment]

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginatedList(BaseModel, Generic[T]):
    """Paginated list returned by TFRobotServer query endpoints.

    Wire format::

        {"items": [...], "total": 382}

    TFRobotServer does **not** echo ``page`` / ``pageSize`` in the
    response — only the item list and total count.  The request-side
    pagination parameters are handled by
    :meth:`RobotClient.get_paginated`.
    """

    items: list[T]
    total: int


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def parse_tfs_response(
    response: Any,  # httpx.Response — kept as Any to avoid import burden
    model_type: type[M],
) -> TFSResponse[M]:
    """Parse an ``httpx.Response`` body as a :class:`TFSResponse` wrapping *model_type*.

    Raises :class:`theseus_kit.errors.RobotApiError` when ``code != 200``.
    Returns the fully-typed :class:`TFSResponse` on success.
    """
    from .errors import RobotApiError

    body = response.json()
    code = body.get("code", 0)
    message = body.get("message", "")

    if code != 200:
        raise RobotApiError(
            f"robot returned code {code}: {message}",
            status_code=code,
        )

    # Deserialise the ``data`` payload into *model_type*.  For a
    # ``PaginatedList[FooDto]`` this means ``items`` is ``list[FooDto]``
    # and ``total`` is an int.
    data_payload = body.get("data", {})
    parsed_data = model_type.model_validate(data_payload)

    return TFSResponse(
        code=code,
        message=message,
        data=parsed_data,
    )


# ---------------------------------------------------------------------------
# Progressive-disclosure tool response models (Issue #4)
# ---------------------------------------------------------------------------


class NodeKind(StrEnum):
    """Kinds of node in the three-state configuration forest."""

    STATE = "state"
    SCENE = "scene"
    FACTORY = "factory"
    SETTING = "setting"


class ResponseMeta(BaseModel):
    """Carried on every progressive-disclosure response (``_meta``)."""

    revision: str | None = None
    fetched_at: str


class ListMeta(ResponseMeta):
    """``_meta`` extension for ``list_config_nodes`` (adds ``filters``)."""

    filters: dict[str, str] = Field(default_factory=dict)


# -- get_config_summary ----------------------------------------------------


class StateSummary(BaseModel):
    """One lifecycle state's availability overview."""

    present: bool
    root_locator: str
    status: Literal["clean", "dirty", "publishing", "unknown"] | None = None
    count: int | None = None
    revision: str | None = None
    last_modified: str | None = None


class RobotIdentity(BaseModel):
    """Minimal robot identity for the summary response."""

    robot_id: str
    display_name: str | None = None
    server_version: str | None = None


class ConfigSummary(BaseModel):
    """``get_config_summary`` response — entry point for an LLM that does
    not yet know what exists.
    """

    robot_identity: RobotIdentity
    states: dict[str, StateSummary] = Field(default_factory=dict)
    meta: ResponseMeta = Field(alias="_meta")

    model_config = ConfigDict(populate_by_name=True)


# -- list_config_nodes -----------------------------------------------------


class ListNode(BaseModel):
    """One node in a ``list_config_nodes`` response page."""

    locator: str
    state: str
    kind: NodeKind
    name: str | None = None
    size_bytes: int | None = None
    revision: str | None = None


class ListNodesResponse(BaseModel):
    """``list_config_nodes`` response — paginated children of a forest node."""

    nodes: list[ListNode] = Field(default_factory=list)
    next_cursor: str | None = None
    drift: bool = False
    meta: ListMeta = Field(alias="_meta")

    model_config = ConfigDict(populate_by_name=True)


# -- get_config_detail -----------------------------------------------------


class ConfigDetail(BaseModel):
    """``get_config_detail`` response — bounded, redacted subtree of a node."""

    locator: str
    state: str
    revision: str | None = None
    subtree: Any = {}
    truncated: bool = False
    truncated_at: str | None = None
    bytes_returned: int = 0
    bytes_estimated_total: int | None = None
    redacted: list[str] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] | None = None
    content_hash: str | None = None
    meta: ResponseMeta = Field(alias="_meta")

    model_config = ConfigDict(populate_by_name=True)


# -- get_template ----------------------------------------------------------


class TemplateResponse(BaseModel):
    """``get_template`` response.

    When ``metadata_only=True`` the detail fields (``subtree``, ``truncated``,
    …) are absent; when ``False`` this carries the same shape as
    :class:`ConfigDetail` with ``locator := tcfg:template/<template_id>``.
    """

    template_id: str
    name: str | None = None
    lifecycle: str = "template"
    size_bytes: int | None = None
    root_locator: str = ""
    # detail fields (metadata_only=False only)
    state: str = "template"
    revision: str | None = None
    subtree: Any = {}
    truncated: bool = False
    truncated_at: str | None = None
    bytes_returned: int = 0
    bytes_estimated_total: int | None = None
    redacted: list[str] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] | None = None
    content_hash: str | None = None
    meta: ResponseMeta = Field(alias="_meta")

    model_config = ConfigDict(populate_by_name=True)


# -- Cursor (stateless pagination for list_config_nodes) -------------------


@dataclass
class CursorData:
    """Decoded content of an opaque ``next_cursor`` token.

    Encoded as base64url JSON with abbreviated keys to keep the token compact.
    """

    parent: str
    state: str
    filters: dict[str, str] = dc_field(default_factory=dict)
    offset: int = 0
    revision_at_issue: str = ""

    _KEYS = ("p", "s", "f", "o", "r")


def encode_cursor(data: CursorData) -> str:
    """Encode *data* as an opaque base64url cursor token."""
    payload = {
        "p": data.parent,
        "s": data.state,
        "f": data.filters,
        "o": data.offset,
        "r": data.revision_at_issue,
    }
    raw = _json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_cursor(token: str) -> CursorData:
    """Decode a base64url cursor token back to :class:`CursorData`."""
    # Restore padding stripped by urlsafe_b64encode.
    missing = 4 - len(token) % 4
    if missing != 4:
        token += "=" * missing
    raw = base64.urlsafe_b64decode(token).decode()
    d: dict[str, Any] = _json.loads(raw)
    return CursorData(
        parent=d.get("p", ""),
        state=d.get("s", ""),
        filters=d.get("f", {}),
        offset=d.get("o", 0),
        revision_at_issue=d.get("r", ""),
    )


# -- get_llms_doc -----------------------------------------------------------


class LlmsDoc(BaseModel):
    """``get_llms_doc`` response — a bounded llms.txt documentation page.

    When ``path`` is empty the tool fetches ``/llms.txt`` (the index);
    otherwise it fetches ``/v1/factory/llm-docs/{path}``.
    """

    path: str = ""
    is_index: bool = False
    content: str = ""
    content_type: str = "text/markdown"
    truncated: bool = False
    bytes_returned: int = 0
    bytes_total: int | None = None
    meta: ResponseMeta = Field(alias="_meta")

    model_config = ConfigDict(populate_by_name=True)


# -- update_draft ------------------------------------------------------------


class UpdateDraftResponse(BaseModel):
    """``update_draft`` response — updated draft with content hash for optimistic concurrency.

    The *content_hash* is a SHA-256 digest of the deterministic JSON
    serialization of *config* (sorted keys, compact separators).  Pass it
    as ``expected_hash`` on the next ``update_draft`` call to detect
    intervening modifications by another actor.
    """

    locator: str = ""
    setting_id: int = 0
    setting_name: str = ""
    scene: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = ""
    revision: str | None = None
    meta: ResponseMeta = Field(alias="_meta")

    model_config = ConfigDict(populate_by_name=True)


# -- publish_config -----------------------------------------------------------


class PublishConfigResponse(BaseModel):
    """``publish_config`` response — result of a global draft release.

    The *online_robot_id* is the ID of the ROBOT-scene setting that was
    published to the online state. *locator* is the root of the online
    configuration tree (``tcfg:online``).
    """

    online_robot_id: int
    locator: str = "tcfg:online"
    meta: ResponseMeta = Field(alias="_meta")

    model_config = ConfigDict(populate_by_name=True)


# -- save_template -----------------------------------------------------------


class SaveTemplateResponse(BaseModel):
    """``save_template`` response — result of saving a draft subtree as a template.

    The *template_id* is the new template's ID suitable for use with
    ``get_template``. *locator* is the root of the template configuration
    tree (``tcfg:template/{template_id}``).
    """

    template_id: int
    locator: str = ""
    meta: ResponseMeta = Field(alias="_meta")

    model_config = ConfigDict(populate_by_name=True)


# -- Topology ----------------------------------------------------------------


class TopologyNode(BaseModel):
    """A single node in the draft configuration topology graph.

    Field names are abbreviated for minimal token cost when consumed by LLM
    agents.  The *c* (children) list is the adjacency-list out-edge set:
    one-hop references, not transitively expanded.
    """

    s: str = ""  # scene (functional domain, e.g. ROBOT/BRAIN/LLM)
    f: str = ""  # factory class name (e.g. EmployeeDraftSetting)
    n: str = ""  # setting_name (user-assigned configuration instance name)
    c: list[int] = Field(default_factory=list)  # children: direct setting_id references


class DraftTopology(BaseModel):
    """Draft configuration topology returned by ``GET /v1/factory/drafts/topology``.

    Three top-level fields provide a complete reference graph of the draft
    state in a compact, LLM-friendly format:

    - *roots*: setting IDs not referenced by any other node.
    - *orphans*: subset of *roots* that also have no outgoing references.
    - *nodes*: all nodes keyed by setting_id (string, per JSON key constraint).
    """

    roots: list[int] = Field(default_factory=list)
    orphans: list[int] = Field(default_factory=list)
    nodes: dict[str, TopologyNode] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


# -- Hash utility -----------------------------------------------------------


def compute_config_hash(config: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash of a config dictionary.

    The hash is stable regardless of key ordering — the dict is serialized
    with ``sort_keys=True`` and compact separators before hashing.

    Returns a 64-character lowercase hex string.
    """
    canonical = _json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
