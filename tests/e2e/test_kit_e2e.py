"""SDK-driven E2E acceptance of the kit's A2C-SMCP surface.

Runs the full real-process chain, no mocks on either side:

    Agent (a2c-smcp AsyncSMCPAgentClient)
      → signaling server (real Socket.IO + version handshake middleware)
        → Computer (a2c-smcp, real MCP stdio subprocess running the kit)
          → theseus-kit (real create_mcp_server + stdio transport)
            → fake robot (tests._fakeserver, real TCP HTTP)

Covers, through the official SDK:

1. version handshake negotiation (a2c_version visible in the office),
2. skill staging — ``client:get_skills`` inventory + ``client:get_skill``
   for the main SKILL.md and sub-resources (mode C "resources"),
3. tool inventory via ``client:get_tools``,
4. the tool-call chain (``client:tool_call`` → real tool → real robot HTTP)
   including the X-TF-* routing contract observed on the fake robot,
5. ``client:get_resources`` passthrough with cursor pagination.

Gated behind ``THESEUS_E2E=1`` like the robot e2e suite; run with::

    THESEUS_E2E=1 uv run pytest tests/e2e -v -m e2e

Known acceptance findings (tracked for the debug phase, not fixed here):

- ``main()`` (the ``theseus-kit`` console entry) builds the server WITHOUT
  settings — zero tools and zero skill resources are served.  This suite
  therefore launches ``tests/e2e/_kit_stdio_runner.py`` (settings from env)
  instead of ``python -m theseus_kit``.
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
from pathlib import Path

import pytest
from a2c_smcp import PROTOCOL_VERSION
from a2c_smcp.agent import AsyncSMCPAgentClient, DefaultAgentAuthProvider
from a2c_smcp.computer import Computer
from a2c_smcp.computer.mcp_clients.model import StdioServerConfig, ToolMeta
from a2c_smcp.computer.socketio.client import SMCPComputerClient
from a2c_smcp.smcp import JOIN_OFFICE_EVENT, SMCP_NAMESPACE
from mcp import StdioServerParameters

from tests._fakeserver import FakeRobotServer

pytestmark = [pytest.mark.e2e]

_E2E_ENABLED = bool(os.environ.get("THESEUS_E2E"))

_SKILLS = (
    "analyze-config",
    "manage-topology",
    "persona-interview",
    "publish-config",
    "save-template",
    "tune-config",
    "write-tfonto",
)

_KIT_BUNDLE_ID = "theseus-kit"  # auto-derived from StdioServerConfig name

_skip_guard = pytest.mark.skipif(not _E2E_ENABLED, reason="set THESEUS_E2E=1 to run the SDK e2e suite")


def _stdio_cfg(script: Path) -> StdioServerConfig:
    """Real stdio MCP config; auto_apply skips the tool-call confirmation.

    The THESEUS_* settings are passed as the explicit subprocess env — the
    SDK's stdio spawn path does not reliably inherit the parent environment
    (observed: the kit subprocess silently fell back to the repo's real
    ``.env`` staging config instead of the fixture's monkeypatched vars).
    """
    return StdioServerConfig(
        name=_KIT_BUNDLE_ID,
        server_parameters=StdioServerParameters(
            command=sys.executable,
            args=[str(script)],
            env={k: v for k, v in os.environ.items()},
        ),
        default_tool_meta=ToolMeta(auto_apply=True),
    )


async def _skill_text(agent: AsyncSMCPAgentClient, computer: str, name: str, rel_path: str | None = None) -> str:
    """Fetch a skill resource's text, covering the inline and blob branches.

    Text MIME resources within the Computer's inline budget come back as
    ``body``; anything else (e.g. ``.py`` — not in the spec's §6.4 text
    table, so the SDK routes it as binary) carries a ``blob_handle`` which is
    drained here chunk-by-chunk via ``client:get_blob``.
    """
    ret = await agent.get_skill(computer, name, rel_path=rel_path)
    if "body" in ret:
        return ret["body"]
    assert "blob_handle" in ret, f"get_skill returned neither body nor blob_handle: {sorted(ret)}"
    parts: list[bytes] = []
    chunk_offset = 0
    while True:
        blob = await agent.get_blob(computer, ret["blob_handle"], chunk_offset=chunk_offset)
        parts.append(base64.b64decode(blob["blob"]))
        if blob["eof"]:
            break
        chunk_offset += len(parts[-1])
    data = b"".join(parts)
    assert len(data) == ret["total_size"], "reassembled blob size must match total_size"
    return data.decode("utf-8")


@pytest.mark.usefixtures("kit_env")
@_skip_guard
async def test_kit_full_chain(signaling_endpoint: str, fake_robot: FakeRobotServer, tmp_path: Path) -> None:
    office_id = "e2e-kit-office"
    computer = Computer(
        name="comp-e2e",
        mcp_servers={_stdio_cfg(Path(__file__).with_name("_kit_stdio_runner.py"))},
        skill_home=tmp_path / "skills",
    )
    comp_client = SMCPComputerClient(computer=computer)
    agent = AsyncSMCPAgentClient(
        auth_provider=DefaultAgentAuthProvider(agent_id="e2e-agent", office_id=office_id),
    )

    try:
        # -- 1. Connect: Agent + Computer join the same office -----------------
        await agent.connect_to_server(signaling_endpoint, namespace=SMCP_NAMESPACE)
        await agent.emit(
            JOIN_OFFICE_EVENT,
            {"role": "agent", "office_id": office_id, "name": "e2e-agent"},
            namespace=SMCP_NAMESPACE,
        )
        await computer.boot_up()
        await comp_client.connect(signaling_endpoint, namespaces=[SMCP_NAMESPACE])
        await comp_client.join_office(office_id)
        assert agent.connected is True
        assert comp_client.connected is True
        await asyncio.sleep(0.5)  # join visibility settles

        # -- 2. Version handshake negotiated and reported ----------------------
        computers = await agent.get_computers_in_office(office_id)
        assert computers, "computer session must be discoverable in the office"
        assert all(c.get("a2c_version") == PROTOCOL_VERSION for c in computers), (
            f"negotiated version must be {PROTOCOL_VERSION}, got {[c.get('a2c_version') for c in computers]}"
        )

        # -- 3. Skill staging: inventory + progressive disclosure --------------
        skills_ret = await agent.get_skills("comp-e2e")
        names = {s["name"] for s in skills_ret["skills"]}
        for skill in _SKILLS:
            assert any(n.endswith(f":{skill}") for n in names), f"skill {skill!r} not staged; inventory={sorted(names)}"
        # every staged skill is materialized to a local package dir
        assert all(s.get("path") for s in skills_ret["skills"]), "staged skills must carry a local path"

        def _skill_name(skill: str) -> str:
            return next(n for n in names if n.endswith(f":{skill}"))

        persona = await _skill_text(agent, "comp-e2e", _skill_name("persona-interview"))
        assert "能力草图" in persona, "persona-interview SKILL.md must cover the capability sketch"
        # SDK serves the staged body with YAML frontmatter stripped (a blank
        # line may remain after the closing fence).
        assert persona.lstrip().startswith("# Persona Interview"), "SKILL.md body must be frontmatter-stripped"

        cap_layer = await _skill_text(
            agent, "comp-e2e", _skill_name("write-tfonto"), rel_path="references/capability-layer.md"
        )
        assert "判断口诀" in cap_layer, "capability-layer reference must carry the decision mnemonic"

        # .py is outside the spec's §6.4 text table, so the SDK routes it via
        # the blob sideband — exercises the client:get_blob drain path.
        validator = await _skill_text(
            agent, "comp-e2e", _skill_name("write-tfonto"), rel_path="scripts/validate_tfonto.py"
        )
        assert "validate" in validator.lower(), "validator script must be staged with real content"

        # -- 4. Tool inventory ---------------------------------------------------
        tools_ret = await agent.get_tools_from_computer("comp-e2e")
        tool_names = {t["name"] for t in tools_ret["tools"]}
        # The Computer composes tool names as "<bundle_id>__<tool>".
        assert f"{_KIT_BUNDLE_ID}__get_llms_doc" in tool_names, f"get_llms_doc missing from tools: {sorted(tool_names)}"

        # -- 5. Tool-call chain: real tool → real robot HTTP ---------------------
        result = await agent.emit_tool_call(
            computer="comp-e2e",
            tool_name=f"{_KIT_BUNDLE_ID}__get_llms_doc",
            params={"max_bytes": 1024},
            timeout=30,
        )
        assert result.isError is False, f"get_llms_doc failed: {result}"
        text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
        assert "# robot llms.txt" in text, f"unexpected llms.txt body: {text!r}"

        # The X-TF-* routing contract was enforced on the wire by the fake robot.
        llms_requests = fake_robot.robot_gets_for("/llms.txt")
        assert llms_requests, "no /llms.txt request reached the fake robot"
        headers = llms_requests[-1].headers
        assert headers["x-tf-robotid"] == "e2e-robot"
        assert headers["x-tf-namespace"] == "e2e-ns"
        assert headers["x-tf-robottype"] == "tfrobot"
        # Credential exchange happened against the Manager endpoint first.
        assert fake_robot.token_posts(), "token exchange must precede robot reads"

        # -- 6. get_resources passthrough: skill:// + window://, cursor-walked ---
        cursor: str | None = None
        uris: set[str] = set()
        for _ in range(10):  # bounded walk guard
            page = await agent.get_resources(computer="comp-e2e", mcp_server=_KIT_BUNDLE_ID, cursor=cursor)
            uris.update(str(r["uri"]) for r in page["resources"])
            cursor = page.get("next_cursor")
            if not cursor:
                break
        assert any(u.startswith("skill://") for u in uris), "skill:// resources must pass through get_resources"
        assert any(u.startswith("window://") for u in uris), "window:// resources must pass through get_resources"
    finally:
        await agent.disconnect()
        await asyncio.sleep(0.2)
        # Explicit shutdown reaps the MCP subprocess; comp_client.disconnect()
        # is skipped (its 30s timeout would drag the suite, SDK does the same).
        await computer.shutdown()
