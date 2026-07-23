"""E2E test against a real (near-prod) robot.

Hermetic tests in ``test_auth_routing.py`` prove the contract against a fake
server. This test exercises the *real* Manager token endpoint + real robot over
the network, so it is gated: it skips unless ``THESEUS_E2E=1`` is set and the
``THESEUS_*`` settings resolve. Run with::

    THESEUS_E2E=1 \\
    THESEUS_ROBOT__ROBOT_ID=<rid> \\
    THESEUS_ROBOT__NAMESPACE=<ns> \\
    THESEUS_ROBOT__ROBOT_TYPE=<tfrobot|openclaw|...> \\
    THESEUS_ROBOT__API_BASE_URL=https://api.<clusterDomain> \\
    THESEUS_ROBOT__MANAGER_BASE_URL=https://<manager-host> \\
    THESEUS_CREDENTIAL__KIND=client_credentials \\
    THESEUS_CREDENTIAL__MACHINE_CLIENT_ID=<robot Account.ID> \\
    THESEUS_CREDENTIAL__MACHINE_CLIENT_SECRET=<tfp_...> \\
    THESEUS_E2E_FACTORY_DOC=<llm-docs path, optional> \\
    THESEUS_E2E_READ_API=<read API path, optional> \\
    uv run pytest tests/test_e2e_robot.py -v -m e2e

(user_pat works once tfrs-auth PatCredential — cnb#1 — is released; set
``THESEUS_CREDENTIAL__KIND=user_pat``, ``...__PAT``, ``...__ROBOT_ACCOUNT_ID``.)
"""

from __future__ import annotations

import os

import pytest

from theseus_kit import RobotClient, TheseusSettings

_E2E_ENABLED = bool(os.environ.get("THESEUS_E2E"))


@pytest.mark.e2e
@pytest.mark.skipif(not _E2E_ENABLED, reason="set THESEUS_E2E=1 plus THESEUS_* settings to run")
async def test_real_robot_read_paths() -> None:
    settings = TheseusSettings()  # loads from env / .env
    factory_doc = os.environ.get("THESEUS_E2E_FACTORY_DOC")
    read_api = os.environ.get("THESEUS_E2E_READ_API")

    async with RobotClient.from_settings(settings) as client:
        llms = await client.get_llms_txt()
        assert llms.strip(), "/llms.txt returned empty body"

        if factory_doc:
            doc = await client.get_factory_doc(factory_doc)
            assert doc.strip(), f"{factory_doc} returned empty body"

        if read_api:
            response = await client.get(read_api)
            assert response.status_code == 200, f"{read_api} -> {response.status_code}"

    # Reaching here means the exchange, header injection, and routing contract
    # all held against the real Manager + robot (AC #1 for /llms.txt).
