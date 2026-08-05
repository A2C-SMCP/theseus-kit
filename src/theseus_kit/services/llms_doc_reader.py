"""llms.txt documentation reader — wraps ``RobotClient`` read-only helpers.

Exposes a single ``get_doc`` entry-point that dispatches to
:meth:`RobotClient.get_llms_txt` (when *path* is empty) or
:meth:`RobotClient.get_factory_doc` (for a specific ``/v1/factory/llm-docs/**``
page), with path-traversal / cross-origin guards applied before the call.

This is a lightweight service (architecture layer 2) that lives alongside
:class:`ConfigReader`; they share the same ``RobotClient`` dependency but
address distinct concerns — docs vs configuration content.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import unquote

from theseus_kit.errors import LlmsDocError
from theseus_kit.models import LlmsDoc, ResponseMeta

if TYPE_CHECKING:
    from theseus_kit.transport import RobotClient

_MAX_BYTES = 32768


class LlmsDocReader:
    """Fetch and bound the robot's runtime llms.txt documentation."""

    async def get_doc(
        self,
        client: RobotClient,
        path: str = "",
        max_bytes: int = 8192,
    ) -> LlmsDoc:
        """Return a bounded llms.txt documentation page.

        Args:
            client: An active ``RobotClient``.
            path: Empty for ``/llms.txt`` (index), otherwise a doc path
                beneath ``/v1/factory/llm-docs/``.
            max_bytes: Content budget (clamped to ``[1, 32768]``).

        Returns:
            ``LlmsDoc`` with content bounded to *max_bytes*.
        """
        budget = max(1, min(max_bytes, _MAX_BYTES))
        path = path.strip()

        if path == "":
            text = await client.get_llms_txt()
            return self._package(path, text, budget, is_index=True)
        else:
            self._validate_path(path)
            text = await client.get_factory_doc(path)
            return self._package(path, text, budget, is_index=False)

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _validate_path(path: str) -> None:
        """Reject traversal, absolute, and cross-origin paths.

        Checks both the raw path and its URL-decoded form to defend against
        percent-encoded traversal payloads (e.g. ``%2e%2e%2f`` → ``../``).
        TFRobotServer's gateway may decode before routing, so a raw-string
        check alone is insufficient.
        """
        decoded = unquote(path)
        for candidate in (path, decoded):
            if ".." in candidate:
                raise LlmsDocError(f"llms doc path contains '..': {path!r}")
            if candidate.startswith("/") and not candidate.startswith("/v1/factory/llm-docs/"):
                raise LlmsDocError(f"llms doc path must be relative or start with '/v1/factory/llm-docs/': {path!r}")
            if "://" in candidate:
                raise LlmsDocError(f"llms doc path looks like a URL: {path!r}")

    @staticmethod
    def _package(path: str, text: str, budget: int, *, is_index: bool) -> LlmsDoc:
        encoded = text.encode()
        total = len(encoded)
        if total <= budget:
            return LlmsDoc(
                path=path,
                is_index=is_index,
                content=text,
                truncated=False,
                bytes_returned=total,
                bytes_total=total,
                meta=ResponseMeta(fetched_at=datetime.now(UTC).isoformat()),
            )

        # Truncate to budget bytes while keeping valid UTF-8.
        # Back off byte-by-byte from the cut line to avoid splitting a
        # multi-byte codepoint — ``errors="replace"`` would insert U+FFFD
        # (3 bytes) and could push ``bytes_returned`` past *budget*.
        truncated_bytes = encoded[:budget]
        truncated = ""  # fallback for empty text (should not reach here)
        while truncated_bytes:
            try:
                truncated = truncated_bytes.decode("utf-8")
                break
            except UnicodeDecodeError:
                truncated_bytes = truncated_bytes[:-1]
        return LlmsDoc(
            path=path,
            is_index=is_index,
            content=truncated,
            truncated=True,
            bytes_returned=len(truncated.encode()),
            bytes_total=total,
            meta=ResponseMeta(fetched_at=datetime.now(UTC).isoformat()),
        )
