"""stdio entry point for the SDK-driven e2e suite.

Runs the REAL composition root (``create_mcp_server`` with all tool/resource
registrations) over a REAL stdio transport, loading settings from the
``THESEUS_*`` environment that the e2e fixtures export to the subprocess.

Why not ``python -m theseus_kit`` / ``main()``?  The production entry point
currently builds ``create_mcp_server()`` **without settings** — it serves no
tools and no resources.  That wiring is the first open acceptance finding
(see tests/e2e/test_kit_e2e.py header); until it is resolved, the e2e suite
launches this script so the full MCP surface is exercised.
"""

from __future__ import annotations

from theseus_kit.config import TheseusSettings
from theseus_kit.server import create_mcp_server

if __name__ == "__main__":
    create_mcp_server(TheseusSettings()).run(transport="stdio")
