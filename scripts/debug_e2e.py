"""Step-by-step E2E debugging script.

Usage:
    cd /Users/jqq/PycharmProjects/theseus-kit
    uv run python scripts/debug_e2e.py
"""

from __future__ import annotations

import asyncio
import sys
from typing import NoReturn


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


async def main() -> None:
    # ---- Step 1: Load settings --------------------------------------------

    banner("Step 1: Load TheseusSettings from .env")

    from theseus_kit import TheseusSettings

    try:
        settings = TheseusSettings()
    except Exception as exc:
        fail("Step 1", f"Cannot load settings: {exc}")

    print(f"  robot_id:      {settings.robot.robot_id}")
    print(f"  namespace:     {settings.robot.namespace}")
    print(f"  robot_type:    {settings.robot.robot_type}")
    print(f"  api_base_url:  {settings.robot.api_base_url}")
    print(f"  manager_url:   {settings.robot.manager_base_url}")
    print(f"  verify:        {settings.robot.verify}")
    print(f"  credential:    kind={settings.credential.kind}")
    ok()

    # ---- Step 2: Build credential -----------------------------------------

    banner("Step 2: Build tfrs_auth credential + token source")

    from theseus_kit.credentials import build_credential
    from theseus_kit.tokens import build_token_source

    try:
        cred = build_credential(settings.credential)
        print(f"  credential type: {type(cred).__name__}")
        # PatCredential uses pat + robot_public_id; no client_id.
        public_id = getattr(cred, "robot_public_id", None)
        if public_id is not None:
            print(f"  robot_public_id: {public_id}")
    except Exception as exc:
        fail("Step 2", f"Cannot build credential: {exc}")

    ok()

    # ---- Step 3: Token exchange -------------------------------------------

    banner("Step 3: Exchange token at Manager")

    from theseus_kit.tokens import TOKEN_PATH

    token_url = settings.robot.manager_base_url.rstrip("/") + TOKEN_PATH
    print(f"  endpoint: {token_url}")

    try:
        token_source = build_token_source(
            settings.credential,
            manager_base_url=settings.robot.manager_base_url,
        )
        token = await token_source.token()
    except Exception as exc:
        fail("Step 3", f"Token exchange failed: {exc}")

    mask_len = min(20, max(0, len(token.access_token) - 10))
    print(f"  access_token:  {token.access_token[:mask_len]}...{token.access_token[-10:]}")
    print(f"  token_type:    {token.token_type}")
    print(f"  scope:         {token.scope}")
    print(f"  expires_at:    {token.expires_at}")
    ok("Token obtained from Manager")

    # ---- Step 4: Call robot /llms.txt -------------------------------------

    banner("Step 4: Call robot /llms.txt with Bearer + X-TF-*")

    from theseus_kit.routing import RequestContext

    context = RequestContext(
        robot_id=settings.robot.robot_id,
        namespace=settings.robot.namespace,
        robot_type=settings.robot.robot_type,
    )

    for name, value in context.routing_headers().items():
        print(f"  {name}: {value}")

    from theseus_kit.transport import RobotClient

    try:
        async with RobotClient.from_settings(settings) as client:
            llms_txt = await client.get_llms_txt()
    except Exception as exc:
        fail("Step 4", f"Call to /llms.txt failed: {exc}")

    line_count = len(llms_txt.strip().splitlines())
    preview = llms_txt[:300]
    print(f"  response lines: {line_count}")
    print("  preview:")
    for line in preview.splitlines()[:10]:
        print(f"    | {line}")
    if len(preview) < len(llms_txt):
        print(f"    | ... ({len(llms_txt) - len(preview)} more chars)")
    ok("Robot /llms.txt returned successfully")

    # ---- Done -------------------------------------------------------------

    banner("All 4 steps passed!")
    print()
    print("  ✓ Settings loaded")
    print("  ✓ Credential built")
    print("  ✓ Token exchanged")
    print("  ✓ Robot /llms.txt reached")
    print()
    print("  Ready for E2E tests:")
    print("    THESEUS_E2E=1 uv run pytest tests/test_e2e_robot.py -v -m e2e")
    print()


if __name__ == "__main__":
    asyncio.run(main())
