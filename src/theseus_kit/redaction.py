"""Credential redaction utilities.

theseus-kit must never leak the PAT, machine secret, exchanged short JWT,
OAuth access/refresh token, authorization code, or state parameter into logs,
exception text, ``repr``, or any tool output — an architecture safety invariant
(see ``docs/architecture.md``: "Credentials stay in the MCP server process and
never enter tool output, resources, logs, or SKILL content").

Config holds secrets as pydantic ``SecretStr`` so default ``repr`` is already
safe. This module is the belt-and-braces scrubber for any string theseus-kit
constructs that could otherwise echo a credential- or token-shaped value.

.. seealso:: ``docs/auth-oauth-design.md`` §9 for the OAuth token shapes this
   module must cover.
"""

from __future__ import annotations

import re

#: Placeholder substituted for any detected secret material.
REDACTED = "<<redacted>>"

# Credential-shaped values theseus-kit might accidentally interpolate.
# PAT: opaque ``tfp_`` token (see ``tfrs_auth.contract.PAT_PREFIX``).
_PAT_RE = re.compile(r"tfp_[A-Za-z0-9_-]+")

# JWT: three base64url segments separated by dots (covers exchanged RS256 short
# JWT, OAuth AS access tokens — including RFC 9068 ``typ=at+jwt`` — and any
# JWT-format refresh token TFRSManager may issue).
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

# Opaque token catch-all: 40+ character pure-base64url strings. Natural
# language never contains runs this long without spaces or punctuation;
# a match is near-certainly an opaque refresh token, state value, PKCE
# verifier, or similarly sensitive random bearer material.  40 chars ≡
# ≥ 30 raw bytes base64url-encoded — far above the 128-bit entropy floor
# for opaque OAuth tokens.
_OPAQUE_RE = re.compile(r"[A-Za-z0-9_-]{40,}")

# OAuth parameter contexts: catches shorter tokens (authorization codes,
# state values under 40 chars) that only become identifiable when they
# appear in their expected OAuth key=value or JSON "key":"value" syntax.
# The 10-char floor on the value excludes empty strings and trivially
# short tokens. Real authorization codes are typically ≥ 20 chars of
# entropy (RFC 6749 §4.1.2), well above this floor.
#
# Key=value matching is deliberately aggressive — a "code":"..." key in
# non-OAuth JSON (e.g. status codes) may be redacted. This is an accepted
# trade-off: missing a real authorization code is worse than redacting a
# benign value. A negative lookbehind on the key prevents false positives
# from compound key names like "error_code=…" or "status_code=…".
_OAUTH_PARAM_RE = re.compile(
    r'''(?:"(?:refresh_token|code|state)"\s*:\s*"[A-Za-z0-9_-]{10,}"'''
    r"""|(?<![A-Za-z0-9_])(?:refresh_token|code|state)=['"]?[A-Za-z0-9_-]{10,}['"]?)"""
)

# Order matters: JWT must precede opaque — JWT segments are long base64url
# strings and would be double-redacted otherwise.  PAT comes first because
# it is the most specific pattern and the most likely to appear in config-
# related error messages.
_SECRET_RES: tuple[re.Pattern[str], ...] = (
    _PAT_RE,
    _JWT_RE,
    _OPAQUE_RE,
    _OAUTH_PARAM_RE,
)


def redact_secrets(text: str) -> str:
    """Replace any credential- or token-shaped substring in *text* with
    :data:`REDACTED`.

    Covers PAT, JWT (exchanged + OAuth AS access + JWT refresh tokens),
    opaque refresh tokens, OAuth authorization codes, and state parameters
    (both standalone long-base64url forms and contextual key=value / JSON
    forms).

    Used defensively when theseus-kit builds strings (error messages, debug
    context) that could otherwise echo a credential or token. The tfrs-auth
    error templates already avoid secret detail; this is a backstop.
    """
    redacted = text
    for pattern in _SECRET_RES:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


# ---------------------------------------------------------------------------
# Sensitive-field redaction for progressive-disclosure read surfaces
# ---------------------------------------------------------------------------

# Keys matched case-insensitively by substring (see progressive-disclosure.md
# § "Sensitive-field redaction").  Applied recursively to nested dicts/lists.
_SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "client_secret",
    "refresh_token",
    "pat",
    "bearer",
    "authorization",
)


def _is_sensitive_key(key: str) -> bool:
    """Return ``True`` when *key* matches a known sensitive-field pattern."""
    lower = key.lower()
    return any(pat in lower for pat in _SENSITIVE_KEY_SUBSTRINGS)


def redact_sensitive_fields(obj: object) -> tuple[object, list[str]]:
    """Recursively redact sensitive fields in *obj*.

    Returns ``(redacted_obj, redacted_paths)`` where *redacted_paths* is a
    list of RFC 6901 JSON Pointer strings identifying every redacted location.
    Only the **paths** are recorded — values are never accumulated.
    """

    redacted_paths: list[str] = []

    def _walk(current: object, pointer: str) -> object:
        if isinstance(current, dict):
            result: dict[str, object] = {}
            for k, v in current.items():
                child_ptr = f"{pointer}/{_escape_json_pointer(k)}"
                if _is_sensitive_key(k):
                    redacted_paths.append(child_ptr)
                    result[k] = REDACTED
                else:
                    result[k] = _walk(v, child_ptr)
            return result
        if isinstance(current, list):
            return [_walk(item, f"{pointer}/{i}") for i, item in enumerate(current)]
        return current

    redacted_obj = _walk(obj, "")
    return redacted_obj, redacted_paths


def _escape_json_pointer(segment: str) -> str:
    """Escape *segment* for use as an RFC 6901 JSON Pointer token."""
    return segment.replace("~", "~0").replace("/", "~1")
