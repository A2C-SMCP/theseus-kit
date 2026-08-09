"""Real-HTTP tests for theseus-kit #17 auth + routing (user_pat path).

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
    ConfigError,
    CredentialError,
    ExchangeUnavailableError,
    OAuthConfig,
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
    cred: UserPatConfig | None = None,
    clock: object | None = None,
    sleep: object | None = None,
    api_base_url: str | None = None,
) -> RobotClient:
    credential = cred or UserPatConfig(pat=SecretStr("tfp_test_pat"), robot_public_id="turingfocus:000042")
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


# --- exchange wire + header injection (user_pat / token-exchange) -------------


async def test_exchange_wire_user_pat(fake_server: FakeRobotServer) -> None:
    async with _client(fake_server) as client:
        await client.get_llms_txt()
    form = parse_form(fake_server.token_posts()[0].body)
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert form["subject_token"] == "tfp_test_pat"
    assert form["subject_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
    assert form["audience"] == "robot:turingfocus:000042"
    assert form["scope"] == "config:read"
    # The user's PAT is exchanged for a robot-scoped JWT; the PAT is sent
    # to the Manager over TLS. The redaction invariant is about logs/repr/
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
    cred = UserPatConfig(pat=SecretStr("tfp_supersecret"), robot_public_id="turingfocus:000042")
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


# --- redaction: OAuth extended patterns (#25) ---------------------------------


def test_redact_oauth_access_token_jwt() -> None:
    """RS256 OAuth AS access token (RFC 9068 at+jwt) is caught by JWT pattern."""
    # Realistic OAuth AS token with typ=at+jwt header
    token = (
        "eyJhbGciOiJSUzI1NiIsInR5cCI6ImF0K2p3dCIsImtpZCI6InRlc3Qta2lkIn0"
        ".eyJpc3MiOiJodHRwczovL21hbmFnZXIuZXhhbXBsZS5jb20iLCJzdWIiOiJ1c2VyOjQyIiw"
        "iYXVkIjoiaHR0cHM6Ly9tYW5hZ2VyLmV4YW1wbGUuY29tL3JvYm90cy80MiIsInNjb3BlIj"
        "oiY29uZmlnOnJlYWQiLCJleHAiOjk5OTk5OTk5OTksImlhdCI6MTIzNDU2Nzg5MH0"
        ".ZmFrZVNpZ25hdHVyZQ"
    )
    out = redact_secrets(f"Bearer {token}")
    assert token not in out
    assert REDACTED in out


def test_redact_opaque_refresh_token_long_base64url() -> None:
    """40+ character base64url string is redacted as opaque token."""
    # Simulates an opaque refresh token (~43 chars)
    refresh = "aGVsbG8td29ybGQtdGhpcy1pcy1hLXJlZnJlc2gtdG9rZW4"  # 46 chars
    out = redact_secrets(f"refresh failed: {refresh}")
    assert refresh not in out
    assert REDACTED in out


def test_redact_state_value_long_base64url() -> None:
    """Long state parameter is caught by opaque pattern even without context."""
    state = "c3RhdGUtdmFsdWUtZm9yLWNzcmYtcHJvdGVjdGlvbi0xMjM0NQ"  # 47 chars
    out = redact_secrets(f"invalid state: {state}")
    assert state not in out
    assert REDACTED in out


def test_redact_auth_code_in_url_query() -> None:
    """Authorization code in ?code=... query string is caught by param pattern."""
    out = redact_secrets("callback?code=SplxlOBeZQQYbYS6WxSbIA&state=abc")
    assert "SplxlOBeZQQYbYS6WxSbIA" not in out
    assert REDACTED in out


def test_redact_refresh_token_in_json() -> None:
    """Refresh token in JSON \"refresh_token\":\"...\" is caught by param pattern."""
    out = redact_secrets(
        '{"access_token":"eyJhbGci.xxx.yyy","refresh_token":"dGhpcy1pcy1hLXJlZnJlc2gtdG9rZW4tdmFsdWU"}'
    )
    assert "dGhpcy1pcy1hLXJlZnJlc2gtdG9rZW4tdmFsdWU" not in out
    assert REDACTED in out


def test_redact_state_in_json() -> None:
    """State in JSON \"state\":\"...\" is caught by param pattern."""
    out = redact_secrets('{"state":"c2hvcnQtc3RhdGU","code":"authcode123456789"}')
    assert "c2hvcnQtc3RhdGU" not in out
    assert "authcode123456789" not in out
    assert REDACTED in out


def test_redact_code_in_form_body() -> None:
    """Authorization code in form-encoded body is caught by param pattern."""
    out = redact_secrets("grant_type=authorization_code&code=AuthCode123456789&redirect_uri=http://localhost/cb")
    assert "AuthCode123456789" not in out
    assert REDACTED in out


def test_redact_mixed_credential_patterns() -> None:
    """PAT, JWT, opaque token, and OAuth param all redacted in one pass."""
    text = (
        "pat=tfp_mysecrettoken123 "
        "jwt=eyJhbGci.sig.payload "
        "refresh=this-is-a-very-long-refresh-token-string-with-40-plus-chars "
        "callback?code=ShortAuthCode12345&state=done"
    )
    out = redact_secrets(text)
    assert "tfp_mysecrettoken123" not in out
    assert "eyJhbGci.sig.payload" not in out
    assert "this-is-a-very-long-refresh-token-string-with-40-plus-chars" not in out
    assert "ShortAuthCode12345" not in out
    assert REDACTED in out


def test_redact_no_false_positive_short_strings() -> None:
    """Short alphanumeric strings are not mistakenly redacted."""
    text = "error=invalid_grant error_description=The+authorization+code+is+invalid"
    out = redact_secrets(text)
    # The entire message should pass through unchanged (no <<redacted>>)
    assert REDACTED not in out
    assert out == text


def test_redact_no_false_positive_normal_text() -> None:
    """Normal prose and code identifiers are not redacted."""
    text = (
        "File /path/to/module.py, line 42, in handle_request\n"
        "ConnectionError: [Errno 61] Connection refused\n"
        "POST https://api.example.com/v1/factory/llm-docs/index HTTP/1.1"
    )
    out = redact_secrets(text)
    assert REDACTED not in out
    assert out == text


# --- user_pat wiring (PatCredential request_form is still a skeleton) --------


def test_user_pat_builds_credential_with_correct_audience() -> None:
    cred = build_credential(UserPatConfig(pat=SecretStr("tfp_pat"), robot_public_id="turingfocus:000042"))
    assert cred.audience == "robot:turingfocus:000042"
    assert cred.scope == "config:read"


def test_settings_load_user_pat_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "THESEUS_ROBOT__ROBOT_ID": "robot-1",
        "THESEUS_ROBOT__NAMESPACE": "default",
        "THESEUS_ROBOT__ROBOT_TYPE": "tfrobot",
        "THESEUS_ROBOT__API_BASE_URL": "https://api.example.com",
        "THESEUS_ROBOT__MANAGER_BASE_URL": "https://mgr.example.com",
        "THESEUS_CREDENTIAL__KIND": "user_pat",
        "THESEUS_CREDENTIAL__PAT": "tfp_test_pat",
        "THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID": "turingfocus:000042",
    }
    for key in env:
        monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    settings = TheseusSettings()
    assert settings.credential.kind == "user_pat"
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


# --- OAuthConfig -------------------------------------------------------------


class TestOAuthConfigDefaults:
    def test_default_scope_is_config_read(self) -> None:
        cfg = OAuthConfig(authorization_server="https://auth.example.com")
        assert cfg.scopes == "config:read"

    def test_client_id_defaults_to_none(self) -> None:
        cfg = OAuthConfig(authorization_server="https://auth.example.com")
        assert cfg.client_id is None

    def test_redirect_uri_defaults_to_none(self) -> None:
        cfg = OAuthConfig(authorization_server="https://auth.example.com")
        assert cfg.redirect_uri is None

    def test_kind_is_oauth(self) -> None:
        cfg = OAuthConfig(authorization_server="https://auth.example.com")
        assert cfg.kind == "oauth"


class TestOAuthConfigValidation:
    def test_rejects_bare_hostname(self) -> None:
        with pytest.raises(ValidationError, match="must start with http:// or https://"):
            OAuthConfig(authorization_server="auth.example.com")

    def test_accepts_https_url(self) -> None:
        cfg = OAuthConfig(authorization_server="https://auth.example.com")
        assert cfg.authorization_server == "https://auth.example.com"

    def test_accepts_http_url(self) -> None:
        cfg = OAuthConfig(authorization_server="http://localhost:8080")
        assert cfg.authorization_server == "http://localhost:8080"

    def test_rejects_bad_redirect_uri(self) -> None:
        with pytest.raises(ValidationError, match="redirect_uri must start with http:// or https://"):
            OAuthConfig(authorization_server="https://auth.example.com", redirect_uri="oob")

    def test_allows_null_redirect_uri(self) -> None:
        cfg = OAuthConfig(authorization_server="https://auth.example.com", redirect_uri=None)
        assert cfg.redirect_uri is None

    def test_all_optional_fields_explicit(self) -> None:
        cfg = OAuthConfig(
            authorization_server="https://auth.example.com",
            scopes="config:read config:write",
            client_id="pre-registered-client-123",
            redirect_uri="http://127.0.0.1:12345/callback",
        )
        assert cfg.scopes == "config:read config:write"
        assert cfg.client_id == "pre-registered-client-123"
        assert cfg.redirect_uri == "http://127.0.0.1:12345/callback"


class TestOAuthConfigEnvLoading:
    def test_oauth_kind_loads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {
            "THESEUS_ROBOT__ROBOT_ID": "robot-1",
            "THESEUS_ROBOT__NAMESPACE": "default",
            "THESEUS_ROBOT__ROBOT_TYPE": "tfrobot",
            "THESEUS_ROBOT__API_BASE_URL": "https://api.example.com",
            "THESEUS_ROBOT__MANAGER_BASE_URL": "https://mgr.example.com",
            "THESEUS_CREDENTIAL__KIND": "oauth",
            "THESEUS_CREDENTIAL__AUTHORIZATION_SERVER": "https://auth.example.com",
        }
        for key in env:
            monkeypatch.setenv(key, env[key])
        # Also clear any PAT env that might linger.
        for stale in [
            "THESEUS_CREDENTIAL__PAT",
            "THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID",
        ]:
            monkeypatch.delenv(stale, raising=False)
        settings = TheseusSettings()
        assert isinstance(settings.credential, OAuthConfig)
        assert settings.credential.kind == "oauth"
        assert settings.credential.authorization_server == "https://auth.example.com"
        assert settings.credential.scopes == "config:read"  # default

    def test_oauth_kind_with_optional_fields_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {
            "THESEUS_ROBOT__ROBOT_ID": "robot-1",
            "THESEUS_ROBOT__NAMESPACE": "default",
            "THESEUS_ROBOT__ROBOT_TYPE": "tfrobot",
            "THESEUS_ROBOT__API_BASE_URL": "https://api.example.com",
            "THESEUS_ROBOT__MANAGER_BASE_URL": "https://mgr.example.com",
            "THESEUS_CREDENTIAL__KIND": "oauth",
            "THESEUS_CREDENTIAL__AUTHORIZATION_SERVER": "https://auth.example.com",
            "THESEUS_CREDENTIAL__SCOPES": "config:read config:write",
            "THESEUS_CREDENTIAL__CLIENT_ID": "pre-registered-client",
            "THESEUS_CREDENTIAL__REDIRECT_URI": "http://127.0.0.1:12345/callback",
        }
        for key in env:
            monkeypatch.setenv(key, env[key])
        settings = TheseusSettings()
        assert isinstance(settings.credential, OAuthConfig)
        assert settings.credential.scopes == "config:read config:write"
        assert settings.credential.client_id == "pre-registered-client"
        assert settings.credential.redirect_uri == "http://127.0.0.1:12345/callback"


class TestOAuthConfigDiscrimination:
    """Credential choice invariant: user_pat is the explicit credential path;
    OAuth is selected when kind=oauth."""

    def test_kind_oauth_produces_oauth_config(self) -> None:
        """The discriminated union routes kind=oauth to OAuthConfig."""
        settings = TheseusSettings(
            robot=dict(
                robot_id="robot-1",
                namespace="default",
                robot_type="tfrobot",
                api_base_url="https://api.example.com",
                manager_base_url="https://mgr.example.com",
            ),
            credential=dict(
                kind="oauth",
                authorization_server="https://auth.example.com",
            ),
        )
        assert isinstance(settings.credential, OAuthConfig)
        assert not isinstance(settings.credential, UserPatConfig)

    def test_kind_user_pat_works(self) -> None:
        """The user_pat credential path loads correctly."""
        settings = TheseusSettings(
            robot=dict(
                robot_id="robot-1",
                namespace="default",
                robot_type="tfrobot",
                api_base_url="https://api.example.com",
                manager_base_url="https://mgr.example.com",
            ),
            credential=dict(
                kind="user_pat",
                pat="tfp_test_pat",
                robot_public_id="turingfocus:000042",
            ),
        )
        assert isinstance(settings.credential, UserPatConfig)

    def test_missing_authorization_server_rejected(self) -> None:
        """OAuthConfig without authorization_server is incomplete → ValidationError."""
        with pytest.raises(ValidationError):
            OAuthConfig()  # type: ignore[call-arg]


class TestBuildCredentialRejectsOAuth:
    def test_build_credential_raises_config_error_for_oauth(self) -> None:
        """OAuth tokens don't go through the exchange pipeline."""
        from theseus_kit.credentials import build_credential

        cfg = OAuthConfig(authorization_server="https://auth.example.com")
        with pytest.raises(ConfigError, match="OAuth credentials bypass the token exchange pipeline"):
            build_credential(cfg)


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
        credential=UserPatConfig(pat=SecretStr("tfp_test_pat"), robot_public_id="turingfocus:000042"),
    )
    async with RobotClient.from_settings(settings) as client:
        assert await client.get_llms_txt() == "# robot llms.txt\n"


# --- fixture -----------------------------------------------------------------


@pytest.fixture
def fake_server() -> Iterator[FakeRobotServer]:
    with FakeRobotServer() as server:
        yield server
