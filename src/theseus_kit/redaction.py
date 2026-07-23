"""Credential redaction utilities.

theseus-kit must never leak the PAT, machine secret, or the exchanged short JWT
into logs, exception text, ``repr``, or any tool output — an architecture safety
invariant (see ``docs/architecture.md``: "Credentials stay in the MCP server
process and never enter tool output, resources, logs, or SKILL content").

Config holds secrets as pydantic ``SecretStr`` so default ``repr`` is already
safe. This module is the belt-and-braces scrubber for any string theseus-kit
constructs that could otherwise echo a credential- or token-shaped value.
"""

from __future__ import annotations

import re

#: Placeholder substituted for any detected secret material.
REDACTED = "<<redacted>>"

# Credential-shaped values theseus-kit might accidentally interpolate.
# PAT: opaque ``tfp_`` token (see ``tfrs_auth.contract.PAT_PREFIX``).
# JWT: three base64url segments separated by dots (the RS256 short JWT).
_PAT_RE = re.compile(r"tfp_[A-Za-z0-9_-]+")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_SECRET_RES: tuple[re.Pattern[str], ...] = (_PAT_RE, _JWT_RE)


def redact_secrets(text: str) -> str:
    """Replace any PAT- or JWT-shaped substring in *text* with :data:`REDACTED`.

    Used defensively when theseus-kit builds strings (error messages, debug
    context) that could otherwise echo a credential or exchanged token. The
    tfrs-auth error templates already avoid secret detail; this is a backstop.
    """
    redacted = text
    for pattern in _SECRET_RES:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted
