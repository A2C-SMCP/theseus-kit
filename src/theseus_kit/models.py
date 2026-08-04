"""Typed response models for the TFRobotServer API.

Mirrors the wire contract of TFRobotServer's ``dtos/global_dto.py`` —
``TFSResponse[T]`` wrapping ``{code, message, data}`` and
``PaginatedList[T]`` for endpoints that return ``{items, total}``.

These models insulate upper layers from ``httpx.Response`` (Issue #3
acceptance criterion); Application Services work with typed pydantic
models instead of raw JSON / HTTP responses.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")

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
    model_type: type[BaseModel],
) -> TFSResponse:  # type: ignore[type-arg]
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
