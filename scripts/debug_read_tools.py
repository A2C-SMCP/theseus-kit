"""Step-by-step debugging of the four progressive-disclosure read tools.

Usage:
    cd /Users/jqq/PycharmProjects/theseus-kit
    uv run python scripts/debug_read_tools.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, NoReturn


def banner(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def fail(step: str, detail: str) -> NoReturn:
    print(f"\n  ✗ [{step}] FAILED: {detail}")
    print("\n  Fix the issue above before retrying.\n")
    sys.exit(1)


def ok(msg: str = "") -> None:
    suffix = f" — {msg}" if msg else ""
    print(f"  ✓ OK{suffix}")


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


async def main() -> None:
    # ---- Setup: load settings + build client --------------------------------

    banner("Setup: Load TheseusSettings + Build RobotClient")

    from theseus_kit import TheseusSettings
    from theseus_kit.transport import RobotClient

    try:
        settings = TheseusSettings()
    except Exception as exc:
        fail("Setup", f"Cannot load settings: {exc}")

    print(f"  robot_id:     {settings.robot.robot_id}")
    print(f"  namespace:    {settings.robot.namespace}")
    print(f"  robot_type:   {settings.robot.robot_type}")
    print(f"  api_base_url: {settings.robot.api_base_url}")
    print(f"  credential:   kind={settings.credential.kind}")
    ok()

    # ---- Tool 1: get_config_summary ----------------------------------------

    banner("Tool 1: get_config_summary")
    print("  → Discover what configurations exist (draft/template/online)")

    from theseus_kit.services.config_reader import ConfigReader

    reader = ConfigReader(robot_id=settings.robot.robot_id)

    try:
        async with RobotClient.from_settings(settings) as client:
            summary = await reader.get_summary(client)
    except Exception as exc:
        fail("get_config_summary", str(exc))

    print(f"\n  Robot: {summary.robot_identity.robot_id}")
    for state_name, state_info in summary.states.items():
        status_icon = "✓" if state_info.present else "✗"
        extra = ""
        if state_info.count is not None:
            extra = f", count={state_info.count}"
        if state_info.status:
            extra += f", status={state_info.status}"
        print(f"  {status_icon} {state_name}: present={state_info.present}{extra}")
    print(f"  _meta.fetched_at: {summary.meta.fetched_at}")

    ok("Summary retrieved")

    # ---- Tool 2: list_config_nodes -----------------------------------------

    banner("Tool 2: list_config_nodes")
    print("  → First list forest roots (no parent), then drill into draft scenes")

    # 2a: Forest roots
    print("\n  --- Forest roots ---")
    try:
        async with RobotClient.from_settings(settings) as client:
            roots = await reader.list_nodes(client)
    except Exception as exc:
        fail("list_config_nodes (roots)", str(exc))

    for node in roots.nodes:
        print(f"  [{node.kind.value}] {node.locator}  name={node.name!r}")

    # Pick the first non-empty state to drill into.
    target_state = None
    for st in ("draft", "online", "template"):
        if summary.states.get(st) and summary.states[st].present:
            target_state = st
            break

    if target_state is None:
        fail("list_config_nodes", "No state has any configuration — nothing to drill into")

    print(f"\n  --- Scenes under tcfg:{target_state} ---")
    try:
        async with RobotClient.from_settings(settings) as client:
            scenes = await reader.list_nodes(client, parent=f"tcfg:{target_state}")
    except Exception as exc:
        fail(f"list_config_nodes (tcfg:{target_state})", str(exc))

    # Track the first scene+factory+setting we find, to drill further.
    first_scene: str | None = None
    first_factory: str | None = None
    first_setting_id: str | None = None

    for node in scenes.nodes:
        print(f"  [{node.kind.value}] {node.locator}  name={node.name!r}")
    if scenes.next_cursor:
        print(f"  (more scenes available, cursor={scenes.next_cursor[:40]}...)")
    if not scenes.nodes:
        print("  (no scenes found)")
        fail("list_config_nodes", f"No scenes in {target_state} state")

    first_scene = scenes.nodes[0].name
    ok(f"{len(scenes.nodes)} scenes found")

    # 2c: Factories under first scene
    print(f"\n  --- Factories under tcfg:{target_state}/{first_scene} ---")
    try:
        async with RobotClient.from_settings(settings) as client:
            factories = await reader.list_nodes(client, parent=f"tcfg:{target_state}/{first_scene}")
    except Exception as exc:
        fail(f"list_config_nodes (tcfg:{target_state}/{first_scene})", str(exc))

    for node in factories.nodes:
        print(f"  [{node.kind.value}] {node.locator}  name={node.name!r}")
    if not factories.nodes:
        print("  (no factories found)")
        # No factories → skip to llms_doc
        first_factory = None
    else:
        first_factory = factories.nodes[0].name
        ok(f"{len(factories.nodes)} factories found")

    # 2d: Settings under first factory
    if first_factory:
        print(f"\n  --- Settings under tcfg:{target_state}/{first_scene}/{first_factory} ---")
        try:
            async with RobotClient.from_settings(settings) as client:
                settings_list = await reader.list_nodes(
                    client,
                    parent=f"tcfg:{target_state}/{first_scene}/{first_factory}",
                )
        except Exception as exc:
            fail(
                f"list_config_nodes (tcfg:{target_state}/{first_scene}/{first_factory})",
                str(exc),
            )

        for node in settings_list.nodes[:10]:  # show at most 10
            size_kb = (node.size_bytes or 0) / 1024
            print(
                f"  [{node.kind.value}] {node.locator}  name={node.name!r}  "
                f"size={size_kb:.1f}KB  revision={node.revision}"
            )
        if len(settings_list.nodes) > 10:
            print(f"  ... and {len(settings_list.nodes) - 10} more")
        if not settings_list.nodes:
            print("  (no settings found)")
        else:
            first_setting_id = settings_list.nodes[0].locator.split("/")[-1]
            ok(f"{len(settings_list.nodes)} settings found")

    # ---- Tool 3: get_config_detail -----------------------------------------

    if first_setting_id and first_factory:
        locator = f"tcfg:{target_state}/{first_scene}/{first_factory}/{first_setting_id}"
        banner("Tool 3: get_config_detail")
        print(f"  → Read detail for {locator}")

        try:
            async with RobotClient.from_settings(settings) as client:
                detail = await reader.get_detail(client, locator=locator)
        except Exception as exc:
            fail(f"get_config_detail ({locator})", str(exc))

        print(f"  locator:       {detail.locator}")
        print(f"  state:         {detail.state}")
        print(f"  revision:      {detail.revision}")
        print(f"  content_hash:  {detail.content_hash}")
        print(f"  truncated:     {detail.truncated}")
        if detail.truncated:
            print(f"  truncated_at:  {detail.truncated_at}")
            print(f"  next_actions:  {detail.next_actions}")
        print(f"  bytes_returned: {detail.bytes_returned}")
        if detail.bytes_estimated_total:
            print(f"  bytes_total:    {detail.bytes_estimated_total}")
        if detail.redacted:
            print(f"  redacted:       {len(detail.redacted)} fields ({detail.redacted})")
        print("  subtree (preview):")
        subtree_str = pretty(detail.subtree)
        for line in subtree_str.splitlines()[:20]:
            print(f"    | {line}")
        if len(subtree_str.splitlines()) > 20:
            print(f"    | ... ({len(subtree_str.splitlines()) - 20} more lines)")

        ok("Detail retrieved")
    else:
        banner("Tool 3: get_config_detail (SKIPPED — no settings found)")

    # ---- Tool 4: get_llms_doc ----------------------------------------------

    banner("Tool 4: get_llms_doc")
    print("  → Step 4a: Fetch /llms.txt index")

    from theseus_kit.services.llms_doc_reader import LlmsDocReader

    doc_reader = LlmsDocReader()

    try:
        async with RobotClient.from_settings(settings) as client:
            index = await doc_reader.get_doc(client, path="")
    except Exception as exc:
        fail("get_llms_doc (index)", str(exc))

    print(f"  path:          {index.path!r}")
    print(f"  is_index:      {index.is_index}")
    print(f"  bytes_returned: {index.bytes_returned}")
    print(f"  truncated:     {index.truncated}")
    preview = index.content[:500]
    print(f"  preview ({len(preview)}/{len(index.content)} chars):")
    for line in preview.splitlines()[:15]:
        print(f"    | {line}")
    if len(index.content) > 500:
        print(f"    | ... ({len(index.content) - 500} more chars)")

    ok("llms.txt index retrieved")

    # 4b: Parse the index to find available doc pages, then fetch the first one.
    print("\n  → Step 4b: Parse index for doc pages, fetch first schema page")

    # Parse markdown links from the index: [text](path)
    import re

    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    doc_pages: list[tuple[str, str]] = []
    for match in link_pattern.finditer(index.content):
        label = match.group(1)
        path = match.group(2)
        if path and not path.startswith(("http://", "https://")):
            doc_pages.append((label, path))

    if doc_pages:
        print(f"  Found {len(doc_pages)} doc pages:")
        for label, path in doc_pages[:10]:
            print(f"    - [{label}]({path})")
        if len(doc_pages) > 10:
            print(f"    ... and {len(doc_pages) - 10} more")

        # Fetch the first doc page.
        first_label, first_path = doc_pages[0]
        print(f"\n  → Fetching: [{first_label}]({first_path})")
        try:
            async with RobotClient.from_settings(settings) as client:
                doc = await doc_reader.get_doc(client, path=first_path)
        except Exception as exc:
            fail(f"get_llms_doc ({first_path})", str(exc))

        print(f"  path:          {doc.path!r}")
        print(f"  bytes_returned: {doc.bytes_returned}")
        print(f"  truncated:     {doc.truncated}")
        preview2 = doc.content[:400]
        print(f"  preview ({len(preview2)}/{len(doc.content)} chars):")
        for line in preview2.splitlines()[:12]:
            print(f"    | {line}")
        if len(doc.content) > 400:
            print(f"    | ... ({len(doc.content) - 400} more chars)")

        ok("Schema doc page retrieved")
    else:
        print("  No doc pages found in index (no relative markdown links)")
        ok("(no doc pages to drill into)")

    # ---- Done --------------------------------------------------------------

    banner("All 4 read tools exercised successfully!")
    print()
    print("  ✓ get_config_summary  — state overview")
    print("  ✓ list_config_nodes   — forest → scenes → factories → settings")
    print("  ✓ get_config_detail   — bounded subtree with content_hash")
    print("  ✓ get_llms_doc        — llms.txt index + schema page")
    print()


if __name__ == "__main__":
    asyncio.run(main())
