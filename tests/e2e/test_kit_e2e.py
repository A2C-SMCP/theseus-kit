"""SDK-driven E2E acceptance of the kit's A2C-SMCP surface.

Runs the full real-process chain, no mocks on either side:

    Agent (a2c-smcp AsyncSMCPAgentClient)
      → signaling server (real Socket.IO + version handshake middleware)
        → Computer (a2c-smcp, real MCP stdio subprocess running the kit)
          → theseus-kit (real production entry ``python -m theseus_kit``)
            → fake robot (tests._fakeserver, real TCP HTTP)

Covers, through the official SDK:

1. version handshake negotiation (a2c_version visible in the office),
2. skill staging — ``client:get_skills`` inventory + ``client:get_skill``
   for the main SKILL.md and sub-resources (mode C "resources"),
3. the Agent execution contract — ``A2CSkillRef.path`` is a real absolute
   staged package dir whose ``scripts/validate_tfonto.py`` is byte-identical
   to the shipped script and executes against the staged example
   (``${TFROBOT_SKILL_DIR}`` renders to this path, skill.md §9.3/§9.4),
4. tool inventory via ``client:get_tools``,
5. the tool-call chain (``client:tool_call`` → real tool → real robot HTTP)
   including the X-TF-* routing contract observed on the fake robot,
6. ``client:get_resources`` passthrough with cursor pagination.

Gated behind ``THESEUS_E2E=1`` like the robot e2e suite; run with::

    THESEUS_E2E=1 uv run pytest tests/e2e -v -m e2e

The kit subprocess is launched through the real production entry
(``python -m theseus_kit``), which loads its settings from the ``THESEUS_*``
environment exported by the ``kit_env`` fixture (Issue #30).
"""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest
from a2c_smcp import PROTOCOL_VERSION
from a2c_smcp.agent import AsyncSMCPAgentClient

from tests._fakeserver import FakeRobotServer
from tests.e2e._trio import KIT_BUNDLE_ID, boot_kit_trio

pytestmark = [pytest.mark.e2e]

_E2E_ENABLED = bool(os.environ.get("THESEUS_E2E"))

_SKILLS = (
    "analyze-config",
    "apply-config-plan",
    "enhance",
    "manage-topology",
    "persona-interview",
    "persona-optimize",
    "plan-config",
    "publish-config",
    "save-template",
    "theseus",
    "tune-config",
    "write-tfonto",
)

_WRITE_TFONTO_FILES = (
    "SKILL.md",
    "references/tfonto-format.md",
    "references/capability-layer.md",
    "references/teacher-math-example.md",
    "references/teacher-math.tfo",
    "references/engineering-memory-example.md",
    "references/engineering-memory.tfo",
    "scripts/validate_tfonto.py",
)

_skip_guard = pytest.mark.skipif(not _E2E_ENABLED, reason="set THESEUS_E2E=1 to run the SDK e2e suite")


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
    async with boot_kit_trio(
        signaling_endpoint, agent_id="e2e-agent", office_id=office_id, skill_home=tmp_path / "skills"
    ) as (computer, comp_client, agent):
        assert agent.connected is True
        assert comp_client.connected is True

        # -- 1. Version handshake negotiated and reported ----------------------
        computers = await agent.get_computers_in_office(office_id)
        assert computers, "computer session must be discoverable in the office"
        assert all(c.get("a2c_version") == PROTOCOL_VERSION for c in computers), (
            f"negotiated version must be {PROTOCOL_VERSION}, got {[c.get('a2c_version') for c in computers]}"
        )

        # -- 2. Skill staging: inventory + progressive disclosure --------------
        skills_ret = await agent.get_skills(computer.name)
        names = {s["name"] for s in skills_ret["skills"]}
        for skill in _SKILLS:
            assert any(n.endswith(f":{skill}") for n in names), f"skill {skill!r} not staged; inventory={sorted(names)}"
        # every staged skill is materialized to a local package dir
        assert all(s.get("path") for s in skills_ret["skills"]), "staged skills must carry a local path"

        def _skill_name(skill: str) -> str:
            return next(n for n in names if n.endswith(f":{skill}"))

        persona = await _skill_text(agent, computer.name, _skill_name("persona-interview"))
        assert "能力草图" in persona, "persona-interview SKILL.md must cover the capability sketch"
        # SDK serves the staged body with YAML frontmatter stripped (a blank
        # line may remain after the closing fence).
        assert persona.lstrip().startswith("# Persona Interview"), "SKILL.md body must be frontmatter-stripped"

        cap_layer = await _skill_text(
            agent, computer.name, _skill_name("write-tfonto"), rel_path="references/capability-layer.md"
        )
        assert "判断口诀" in cap_layer, "capability-layer reference must carry the decision mnemonic"

        # .py is outside the spec's §6.4 text table, so the SDK routes it via
        # the blob sideband — exercises the client:get_blob drain path.
        validator = await _skill_text(
            agent, computer.name, _skill_name("write-tfonto"), rel_path="scripts/validate_tfonto.py"
        )
        assert "validate" in validator.lower(), "validator script must be staged with real content"

        # -- 3. Tool inventory ---------------------------------------------------
        tools_ret = await agent.get_tools_from_computer(computer.name)
        tool_names = {t["name"] for t in tools_ret["tools"]}
        # The Computer composes tool names as "<bundle_id>__<tool>".
        assert f"{KIT_BUNDLE_ID}__get_llms_doc" in tool_names, f"get_llms_doc missing from tools: {sorted(tool_names)}"

        # -- 4. Tool-call chain: real tool → real robot HTTP ---------------------
        result = await agent.emit_tool_call(
            computer=computer.name,
            tool_name=f"{KIT_BUNDLE_ID}__get_llms_doc",
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

        # -- 5. get_resources passthrough: skill:// + window://, cursor-walked ---
        cursor: str | None = None
        uris: set[str] = set()
        for _ in range(10):  # bounded walk guard
            page = await agent.get_resources(computer=computer.name, mcp_server=KIT_BUNDLE_ID, cursor=cursor)
            uris.update(str(r["uri"]) for r in page["resources"])
            cursor = page.get("next_cursor")
            if not cursor:
                break
        assert any(u.startswith("skill://") for u in uris), "skill:// resources must pass through get_resources"
        assert any(u.startswith("window://") for u in uris), "window:// resources must pass through get_resources"


@pytest.mark.usefixtures("kit_env")
@_skip_guard
async def test_staged_scripts_are_real_and_runnable(signaling_endpoint: str, tmp_path: Path) -> None:
    """The staged write-tfonto package is real on disk and its script runs.

    Acceptance for the Agent execution contract (skill.md §9.3/§9.4):
    ``A2CSkillRef.path`` is the required absolute staged package dir — the
    value an Agent SDK renders into ``${TFROBOT_SKILL_DIR}`` — and
    ``path/scripts/validate_tfonto.py`` is byte-identical to the shipped
    script and executes successfully against the staged example, so an Agent
    can drive it with
    ``python ${TFROBOT_SKILL_DIR}/scripts/validate_tfonto.py <tfo>``.
    """
    async with boot_kit_trio(
        signaling_endpoint, agent_id="e2e-agent", office_id="e2e-script-office", skill_home=tmp_path / "skills"
    ) as (computer, _comp_client, agent):
        skills_ret = await agent.get_skills(computer.name)
        ref = next(s for s in skills_ret["skills"] if s["name"].endswith(":write-tfonto"))

        # -- The registry path is the real, absolute staged package dir --------
        staged_root = Path(ref["path"])
        assert staged_root.is_absolute(), "A2CSkillRef.path must be absolute (skill.md §6)"
        assert staged_root.is_dir(), f"staged dir missing: {staged_root}"

        # -- Full package shape on disk, byte-identical to the shipped files ---
        for rel in _WRITE_TFONTO_FILES:
            staged_file = staged_root / rel
            assert staged_file.is_file(), f"missing staged file: {rel}"
        for rel in _WRITE_TFONTO_FILES:
            shipped = files("theseus_kit.skills").joinpath("write-tfonto", rel).read_bytes()
            assert (staged_root / rel).read_bytes() == shipped, f"staged file differs from shipped: {rel}"

        # -- The staged SKILL.md carries the execution contract ------------------
        skill_md = (staged_root / "SKILL.md").read_text(encoding="utf-8")
        assert "${TFROBOT_SKILL_DIR}/scripts/validate_tfonto.py" in skill_md, (
            "staged SKILL.md must reference the validator via the placeholder"
        )

        # -- Execute the STAGED validator against the STAGED examples ----------
        for example_rel in ("references/teacher-math.tfo", "references/engineering-memory.tfo"):
            proc = subprocess.run(
                [
                    sys.executable,
                    str(staged_root / "scripts/validate_tfonto.py"),
                    str(staged_root / example_rel),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert proc.returncode == 0, f"staged validator failed on {example_rel}:\n{proc.stdout}\n{proc.stderr}"
            assert "校验通过" in proc.stdout, f"unexpected validator output: {proc.stdout!r}"

        # -- get_skill integrity: reported sha256 matches the staged disk file -
        ret = await agent.get_skill(computer.name, ref["name"], rel_path="scripts/validate_tfonto.py")
        assert ret["rel_path"] == "scripts/validate_tfonto.py"
        disk_digest = hashlib.sha256((staged_root / "scripts/validate_tfonto.py").read_bytes()).hexdigest()
        assert ret["sha256"] == disk_digest, "get_skill sha256 must match the staged disk file"
