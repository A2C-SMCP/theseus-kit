"""Focused tests for settings-layer validation error guidance."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from theseus_kit.config import _env_hint_for, _match_union_variant


def test_missing_error_type_expands_required_nested_fields() -> None:
    """Hint expansion keys off pydantic's stable error type, not message text."""
    hint = _env_hint_for(("robot",), "missing")

    assert "THESEUS_ROBOT__ROBOT_ID" in hint
    assert "THESEUS_ROBOT__NAMESPACE" in hint


def test_non_missing_error_does_not_expand_nested_fields() -> None:
    """Non-missing validation errors point at the exact environment prefix."""
    assert _env_hint_for(("robot",), "value_error") == "THESEUS_ROBOT"


def test_union_variant_match_does_not_require_discriminator_default() -> None:
    """Literal discriminator annotations work even when variants omit defaults."""

    class Alpha(BaseModel):
        kind: Literal["alpha"]

    class Beta(BaseModel):
        kind: Literal["beta"]

    assert _match_union_variant(Alpha | Beta, "kind", "beta") is Beta
