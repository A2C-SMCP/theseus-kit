"""Real-HTTP tests for theseus-kit #17 auth + routing (client_credentials path).

Drives the REAL tfrs-auth ``AsyncCachingTokenSource`` and theseus-kit's
``RobotAuth``/``RobotClient`` against a real listening fake server (Manager
token endpoint + robot). No transport mocking — every assertion crosses a real
TCP socket, per #17's acceptance bar.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator

import pytest
from pydantic import SecretStr, ValidationError

from theseus_kit import (
    AuthRejectedError,
    ClientCredentialsConfig,
    CredentialError,
    ExchangeUnavailableError,
    RequestContext,
    RobotApiError,
    RobotClient,
    RoutingConfigError,
    TheseusError,
    TheseusSettings,
    UserPatConfig,
)
from theseus_kit.credentials import build_credential
from theseus_kit.redaction import REDACTED, redact_secrets
from theseus_kit.tokens import build_token_source

from ._fakeserver import FakeRobotServer, parse_form

_CTX = dict(robot_id="robot-1", namespace="default", robot_type="tfrobot")


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _no_sleep(_: float) -> None:
    return None


def _client(
    fake: FakeRobotServer,
    *,
    cred: ClientCredentialsConfig | UserPatConfig | None = None,
    clock: object | None = None,
    sleep: object | None = None,
    api_base_url: str | None = None,
) -> RobotClient:
    credential = cred or ClientCredentialsConfig(
        machine_client_id="turingfocus:000042", machine_client_secret=SecretStr("tfp_secret")
    )
    token_source = build_token_source(
        credential,
        manager_base_url=fake.manager_base_url,
        clock=clock,  # type: ignore[arg-type]
        sleep=sleep,  # type: ignore[arg-type]
    )
    context = RequestContext(**_CTX)
    return RobotClient(token_source, context, api_base_url=api_base_url or fake.api_base_url)


# --- routing context ---------------------------------------------------------


def test_routing_headers_carry_all_three_fields() -> None:
    headers = RequestContext(**_CTX).routing_headers()
    assert headers == {
        "X-TF-Namespace": "default",
        "X-TF-RobotId": "robot-1",
        "X-TF-RobotType": "tfrobot",
    }


@pytest.mark.parametrize("bad", ["UPPER", "has space", "under_score", "dot.dot", ""])
def test_routing_rejects_bad_charset(bad: str) -> None:
    with pytest.raises(RoutingConfigError):
        RequestContext(robot_id=bad, namespace="default", robot_type="tfrobot")


# --- exchange wire + header injection (client_credentials) -------------------


async def test_exchange_wire_client_credentials(fake_server: FakeRobotServer) -> None:
    async with _client(fake_server) as client:
        await client.get_llms_txt()
    form = parse_form(fake_server.token_posts()[0].body)
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == "turingfocus:000042"
    assert form["audience"] == "robot:turingfocus:000042"  # self-management: callee == client id
    assert form["scope"] == "config:read"
    assert form["client_secret"] == "tfp_secret"
    # client_secret IS on the exchange wire (client_credentials grant requires it,
    # sent to the Manager over TLS); the redaction invariant is about logs/repr/
    # errors, not the wire — covered by the redaction tests below.


async def test_robot_requests_carry_routing_headers(fake_server: FakeRobotServer) -> None:
    async with _client(fake_server) as client:
        text = await client.get_llms_txt()
    assert text == "# robot llms.txt\n"
    # The fake 400s on any missing X-TF-* header, so a 200 proves all three were
    # sent; assert the values explicitly as well (names lowercased on the wire).
    headers = fake_server.robot_gets_for("/llms.txt")[0].headers
    assert headers["x-tf-namespace"] == "default"
    assert headers["x-tf-robotid"] == "robot-1"
    assert headers["x-tf-robottype"] == "tfrobot"
    assert headers["authorization"].startswith("Bearer ")


async def test_read_paths_succeed(fake_server: FakeRobotServer) -> None:
    async with _client(fake_server) as client:
        llms = await client.get_llms_txt()
        doc = await client.get_factory_doc("schema/main")
        read_api = await client.get("/v1/factory/config")
    assert llms == "# robot llms.txt\n"
    assert "factory-doc" in doc
    assert read_api.status_code == 200
    assert fake_server.robot_gets_for("/v1/factory/llm-docs/schema/main")


# --- token-source behaviour (real AsyncCachingTokenSource) --------------------


async def test_token_cached_across_calls(fake_server: FakeRobotServer) -> None:
    async with _client(fake_server) as client:
        await client.get_llms_txt()
        await client.get_factory_doc("a")
    assert len(fake_server.token_posts()) == 1


async def test_token_refreshed_within_skew_window(fake_server: FakeRobotServer) -> None:
    fake_server.token.expires_in = 100  # default skew 30s -> refresh when now >= 70
    clock = FakeClock()
    async with _client(fake_server, clock=clock) as client:
        await client.get_llms_txt()  # issued at 0, expires_at 100
        clock.advance(69)  # 69 < 70: not yet near-expiry -> cache hit
        await client.get_factory_doc("a")
        assert len(fake_server.token_posts()) == 1
        clock.advance(6)  # now 75 >= 70 and < 100: within skew window -> refresh
        await client.get_factory_doc("b")
    assert len(fake_server.token_posts()) == 2


async def test_single_flight_concurrent_fetch(fake_server: FakeRobotServer) -> None:
    async with _client(fake_server) as client:
        await asyncio.gather(*(client.get_llms_txt() for _ in range(8)))
    assert len(fake_server.token_posts()) == 1


# --- exchange-endpoint failure modes -----------------------------------------


async def test_exchange_401_maps_to_credential_error(fake_server: FakeRobotServer) -> None:
    fake_server.token.fail_sequence = [(401, "invalid_client")]
    async with _client(fake_server) as client:
        with pytest.raises(CredentialError):
            await client.get_llms_txt()
    assert len(fake_server.token_posts()) == 1  # not retryable -> single attempt


async def test_exchange_503_exhausted_maps_to_unavailable(fake_server: FakeRobotServer) -> None:
    fake_server.token.fail_sequence = [(503, "temporarily_unavailable")] * 5
    async with _client(fake_server, sleep=_no_sleep) as client:
        with pytest.raises(ExchangeUnavailableError) as exc_info:
            await client.get_llms_txt()
    assert exc_info.value.retryable is True
    assert len(fake_server.token_posts()) == 3  # 1 + max_retries(2)


async def test_exchange_transient_then_success(fake_server: FakeRobotServer) -> None:
    fake_server.token.fail_sequence = [(503, "temporarily_unavailable")]
    async with _client(fake_server, sleep=_no_sleep) as client:
        text = await client.get_llms_txt()
    assert text == "# robot llms.txt\n"
    assert len(fake_server.token_posts()) == 2  # 1 failure retried, then success


# --- robot-side failures -----------------------------------------------------


async def test_robot_401_maps_to_auth_rejected(fake_server: FakeRobotServer) -> None:
    from ._fakeserver import RobotResponse

    fake_server.robot_responses["/llms.txt"] = RobotResponse(401, b'{"error":"unauth"}')
    async with _client(fake_server) as client:
        with pytest.raises(AuthRejectedError):
            await client.get_llms_txt()


async def test_robot_500_maps_to_robot_api_error(fake_server: FakeRobotServer) -> None:
    from ._fakeserver import RobotResponse

    fake_server.robot_responses["/llms.txt"] = RobotResponse(500, b'{"error":"boom"}')
    async with _client(fake_server) as client:
        with pytest.raises(RobotApiError) as exc_info:
            await client.get_llms_txt()
    assert exc_info.value.status_code == 500


async def test_network_failure_maps_to_robot_api_error(fake_server: FakeRobotServer) -> None:
    # A guaranteed-closed port: bind+release an ephemeral port, nothing listens.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    async with _client(fake_server, api_base_url=f"http://127.0.0.1:{port}") as client:
        with pytest.raises(RobotApiError) as exc_info:
            await client.get_llms_txt()
    assert exc_info.value.status_code == 0


# --- redaction ---------------------------------------------------------------


def test_secret_not_in_config_repr() -> None:
    cred = ClientCredentialsConfig(
        machine_client_id="turingfocus:000042", machine_client_secret=SecretStr("tfp_supersecret")
    )
    assert "tfp_supersecret" not in repr(cred)


def test_redact_secrets_masks_pat_and_jwt() -> None:
    out = redact_secrets("pat=tfp_abc123 token=eyJhbGci.sig.payload trailing")
    assert "tfp_abc123" not in out
    assert "eyJhbGci.sig.payload" not in out
    assert REDACTED in out


async def test_error_message_has_no_secret(fake_server: FakeRobotServer) -> None:
    fake_server.token.fail_sequence = [(401, "invalid_client")]
    async with _client(fake_server) as client:
        with pytest.raises(CredentialError) as exc_info:
            await client.get_llms_txt()
    assert "tfp_secret" not in str(exc_info.value)


# --- user_pat wiring (PatCredential request_form is still a skeleton) --------


def test_user_pat_builds_credential_with_correct_audience() -> None:
    cred = build_credential(UserPatConfig(pat=SecretStr("tfp_pat"), robot_public_id="turingfocus:000042"))
    assert cred.audience == "robot:turingfocus:000042"
    assert cred.scope == "config:read"


def test_settings_load_client_credentials_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "THESEUS_ROBOT__ROBOT_ID": "robot-1",
        "THESEUS_ROBOT__NAMESPACE": "default",
        "THESEUS_ROBOT__ROBOT_TYPE": "tfrobot",
        "THESEUS_ROBOT__API_BASE_URL": "https://api.example.com",
        "THESEUS_ROBOT__MANAGER_BASE_URL": "https://mgr.example.com",
        "THESEUS_CREDENTIAL__KIND": "client_credentials",
        "THESEUS_CREDENTIAL__MACHINE_CLIENT_ID": "turingfocus:000042",
        "THESEUS_CREDENTIAL__MACHINE_CLIENT_SECRET": "tfp_secret",
    }
    for key in [*env, "THESEUS_CREDENTIAL__PAT", "THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID"]:
        monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    settings = TheseusSettings()
    assert settings.credential.kind == "client_credentials"
    assert settings.robot.api_base_url == "https://api.example.com"


def test_config_rejects_bad_url_scheme() -> None:
    from theseus_kit import RobotTarget

    with pytest.raises(ValidationError):
        RobotTarget(
            robot_id="robot-1",
            namespace="default",
            robot_type="tfrobot",
            api_base_url="api.example.com",  # missing http(s)://
            manager_base_url="https://mgr.example.com",
        )


def test_config_rejects_missing_ca_bundle() -> None:
    from pathlib import Path

    from theseus_kit import RobotTarget

    with pytest.raises(ValidationError):
        RobotTarget(
            robot_id="robot-1",
            namespace="default",
            robot_type="tfrobot",
            api_base_url="https://api.example.com",
            manager_base_url="https://mgr.example.com",
            ca_bundle=Path("/does/not/exist"),
        )


# --- exchange-error mapping (remaining branches) -----------------------------


def test_map_exchange_error_branches() -> None:
    from tfrs_auth.errors import (
        InvalidGrantError,
        InvalidScopeError,
        InvalidTargetError,
        PaymentRequiredError,
        RateLimitedError,
        TokenExchangeError,
        TransportError,
    )

    from theseus_kit import ScopeOrAudienceError, SubscriptionFrozenError
    from theseus_kit.errors import map_exchange_error

    transport = map_exchange_error(TransportError("net down"))
    assert isinstance(transport, ExchangeUnavailableError) and transport.retryable

    assert isinstance(map_exchange_error(InvalidGrantError()), CredentialError)
    assert isinstance(map_exchange_error(RateLimitedError()), ExchangeUnavailableError)
    assert isinstance(map_exchange_error(InvalidTargetError()), ScopeOrAudienceError)
    assert isinstance(map_exchange_error(InvalidScopeError()), ScopeOrAudienceError)

    frozen = map_exchange_error(PaymentRequiredError("frozen", renew_url="https://pay.example"))
    assert isinstance(frozen, SubscriptionFrozenError)
    assert frozen.renew_url == "https://pay.example"

    # Unknown OAuth error code -> base TheseusError (not a more specific subclass).
    fallback = map_exchange_error(TokenExchangeError("odd"))
    assert type(fallback) is TheseusError


async def test_from_settings_builds_working_client(fake_server: FakeRobotServer) -> None:
    from theseus_kit import RobotTarget

    settings = TheseusSettings(
        robot=RobotTarget(
            robot_id="robot-1",
            namespace="default",
            robot_type="tfrobot",
            api_base_url=fake_server.api_base_url,
            manager_base_url=fake_server.manager_base_url,
        ),
        credential=ClientCredentialsConfig(
            machine_client_id="turingfocus:000042", machine_client_secret=SecretStr("tfp_secret")
        ),
    )
    async with RobotClient.from_settings(settings) as client:
        assert await client.get_llms_txt() == "# robot llms.txt\n"


# --- fixture -----------------------------------------------------------------


@pytest.fixture
def fake_server() -> Iterator[FakeRobotServer]:
    with FakeRobotServer() as server:
        yield server
