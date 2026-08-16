"""Shared real-process trio assembly for the SDK e2e suite.

Brings up the full chain once per test: the kit as a real MCP stdio
subprocess under an a2c-smcp Computer, connected (with the Agent) to the
signaling server, joined into one office, and torn down in the reverse
order.  Kept in a plain module (not conftest) so importing it from tests
never double-imports the conftest plugin module.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

from a2c_smcp.agent import AsyncSMCPAgentClient, DefaultAgentAuthProvider
from a2c_smcp.computer import Computer
from a2c_smcp.computer.mcp_clients.model import StdioServerConfig, ToolMeta
from a2c_smcp.computer.socketio.client import SMCPComputerClient
from a2c_smcp.smcp import JOIN_OFFICE_EVENT, SMCP_NAMESPACE
from mcp import StdioServerParameters

KIT_BUNDLE_ID = "theseus-kit"  # auto-derived from StdioServerConfig name

COMPUTER_NAME = "comp-e2e"

_OFFICE_SETTLE_S = 0.5  # join visibility settles after this (SDK e2e convention)


def stdio_kit_cfg() -> StdioServerConfig:
    """Real stdio MCP config for the kit; auto_apply skips tool confirmation.

    The THESEUS_* settings are passed as the explicit subprocess env — mcp's
    stdio_client only inherits a whitelisted "safe" subset of the parent
    environment by default (``get_default_environment()``), so relying on
    inheritance would silently drop them (observed: the kit subprocess fell
    back to the repo's real ``.env`` staging config instead of the fixture's
    monkeypatched vars).
    """
    return StdioServerConfig(
        name=KIT_BUNDLE_ID,
        server_parameters=StdioServerParameters(
            command=sys.executable,
            args=["-m", "theseus_kit"],  # the REAL production entry (Issue #30)
            env={k: v for k, v in os.environ.items()},
        ),
        default_tool_meta=ToolMeta(auto_apply=True),
    )


@contextlib.asynccontextmanager
async def boot_kit_trio(
    base: str,
    *,
    agent_id: str,
    office_id: str,
    skill_home: Path,
) -> AsyncIterator[tuple[Computer, SMCPComputerClient, AsyncSMCPAgentClient]]:
    """Boot the real trio — kit stdio subprocess + Computer + Agent in one office.

    Yields ``(computer, comp_client, agent)`` after both sides joined the
    office; on exit disconnects the Agent and reaps the MCP subprocess via
    ``Computer.shutdown()`` (``comp_client.disconnect()`` is skipped — its
    30s timeout would drag the suite, the SDK's own e2e does the same).
    """
    computer = Computer(
        name=COMPUTER_NAME,
        mcp_servers={stdio_kit_cfg()},
        skill_home=skill_home,
    )
    comp_client = SMCPComputerClient(computer=computer)
    agent = AsyncSMCPAgentClient(
        auth_provider=DefaultAgentAuthProvider(agent_id=agent_id, office_id=office_id),
    )

    booted = False
    try:
        await agent.connect_to_server(base, namespace=SMCP_NAMESPACE)
        await agent.emit(
            JOIN_OFFICE_EVENT,
            {"role": "agent", "office_id": office_id, "name": agent_id},
            namespace=SMCP_NAMESPACE,
        )
        await computer.boot_up()
        booted = True
        await comp_client.connect(base, namespaces=[SMCP_NAMESPACE])
        await comp_client.join_office(office_id)
        await asyncio.sleep(_OFFICE_SETTLE_S)
        yield computer, comp_client, agent
    finally:
        with contextlib.suppress(Exception):
            await agent.disconnect()
            await asyncio.sleep(0.2)
        if booted:
            with contextlib.suppress(Exception):
                await computer.shutdown()
