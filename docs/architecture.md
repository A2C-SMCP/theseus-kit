# Architecture baseline

## Compatibility boundary

`theseus-kit` is first a standard MCP server. Tools use MCP input/output schemas,
and resources use standard `resources/list` and `resources/read`. A2C-SMCP adds
recognizable URI schemes and metadata; generic MCP clients may ignore those
extensions without losing the core configuration tools.

## Planned layers

1. **MCP surface** — tool/resource declarations and stable public schemas.
2. **Application services** — read, edit, save-template, and publish use cases.
3. **TFRobot client** — authenticated HTTP adapter for `/v1/factory/**` and
   `/llms.txt` endpoints.
4. **Resource projection** — compact `window://` state and distributable
   `skill://` packages.

The server does not copy the robot's Factory schema. It reads the target
robot's llms.txt documentation at runtime so each deployed TFRobotServer
version remains its own configuration-documentation source of truth.

## Auth and routing (layer 3)

The TFRobot client authenticates via `tfrs-auth` and routes over the Operator's
Host-authoritative tenant ingress. Contract confirmed by TFRM/TFRO/TFRS develop
(2026-11); full upgrade report: [`upgrade-plan-robot-address-v2.md`](upgrade-plan-robot-address-v2.md).

- **Entry**: `https://{alias}/c/tfrobot/{rid}/` — Manager's authoritative
  `accessURL`. The Host (tenant alias) determines the namespace; the
  `/c/{robotType}/{robotId}` path determines the robot. The Operator strips that
  path prefix and forwards `/v1/factory/**` / `/llms.txt` to TFRobotServer.
- **No routing headers**: `X-TF-*` is retired; the gateway removes any inbound
  values and derives routing only from Host + path. theseus-kit sends only
  `Authorization: Bearer`.
- **Manager machine API** is separate: `manager_api_base_url` (test
  `https://rest.test.turingfocus.cn`, staging `api-staging…`, prod `api…`) is the
  token-exchange/discovery origin; it is not the JWT issuer and not the robot
  entry.
- **Identity split**: the token `audience` is `robot:{orgSlug}:{employeeNo}`
  (public_id) for PAT token-exchange — a *different* identifier from the rid in
  the URL path. OAuth Authorization Code uses `{issuer}/robots/{public_id}`
  (`typ=at+jwt`, TFRM-760).
- **Credentials**: `user_pat` (user personal access token exchanged via
  token-exchange for a robot-scoped JWT) is the production path. Uses
  `tfrs-auth`'s `AsyncCachingTokenSource` (cache / single-flight / near-expiry
  refresh / backoff) — theseus-kit reimplements none of that machinery.
- **Gateway errors**: unknown Host / robot-not-on-Host → 404 (typed
  `RobotRouteNotFoundError`); retained old alias → 409 `ROBOT_ADDRESS_CHANGED`
  (typed `RobotAddressChangedError`, no retry); invalid path → 400.
- **No auto refresh-retry on robot 401/403**: a rejected token surfaces as a
  typed `AuthRejectedError`. Refresh-on-401 would require an upstream
  token-source invalidator (not yet available); until then it is out of scope.

## OAuth (no-PAT) path (#18)

When ``kind=oauth`` is configured, theseus-kit acts as an OAuth Protected
Resource (RS) per the MCP Authorization specification — an alternative to the
``user_pat`` path. The MCP Client drives the authorization-code + PKCE flow
against the TFRSManager AS; theseus-kit validates the resulting Bearer token and
forwards it directly to TFRobotServer — **no token exchange**. TFRobotServer
accepts the Manager Authorization Code profile when the audience is
`{issuer}/robots/{public_id}` and `typ=at+jwt` (TFRM-760). Full rationale:
[`docs/auth-oauth-design.md`](auth-oauth-design.md).

The two credential paths converge at `RobotClient` but follow different mechanics
upstream:

**Path A — user_pat** (exchange pipeline, #17):

```
TheseusSettings(credential)
  → build_credential()         # config → tfrs_auth.PatCredential
  → build_token_source()       # AsyncCachingTokenSource (cache / single-flight / near-expiry refresh / backoff)
  → RobotClient(RobotAuth)     # Bearer only, base_url = access_url
```

**Path B — OAuth** (direct forward, #18):

```
MCP Client (OAuth授权 + Bearer)
  → TheseusTokenVerifier       # RS256 + JWKS; audience = {issuer}/robots/{public_id}
  → StaticTokenSource          # pre-validated token, no exchange / caching / refresh
  → RobotClient(RobotAuth)     # Bearer only, base_url = access_url
```

| Path | Token source | Exchange (RFC 8693) | Cache / refresh |
|------|-------------|---------------------|-----------------|
| user_pat | `AsyncCachingTokenSource` | Yes (RFC 8693 token-exchange) | Yes |
| OAuth | `StaticTokenSource` | No (direct forward) | No (MCP Client side) |

Key modules for #18 (see [`auth-oauth-design.md`](auth-oauth-design.md)):

- **`oauth.py`**: `TheseusTokenVerifier` — adapts `tfrs_auth.JwtVerifier` →
  MCP SDK `TokenVerifier` protocol. `build_token_verifier()` performs RFC 8414
  AS metadata discovery and derives the expected robot audience from the
  discovered issuer + `robot_public_id`.
- **`server.py`**: `_LazyOAuthTokenVerifier` defers async AS discovery to the first
  ``verify_token`` call (single-flight lock). `create_mcp_server()` wires
  `AuthSettings` + `token_verifier` when `OAuthConfig` is detected.
- **`transport.py`**: `StaticTokenSource` — holds a pre-validated bearer with no
  exchange/caching/refresh. `RobotClient.for_static_token()` factory for the OAuth
  path. `RobotAuth` accepts `AsyncCachingTokenSource | StaticTokenSource`.
- **`config.py`**: `OAuthConfig` — `authorization_server`, `robot_public_id`,
  `scopes`, `client_id`, `redirect_uri`, `resource_server_url`.

**Topology B (STDIO external callback, S4)**: not yet implemented — depends on the
P1 upstream capability in tfrs-auth (transport-agnostic OAuth 2.1 acquisition client).
See [`docs/upstream/tfrs-auth-oauth-feature-request.md`](upstream/tfrs-auth-oauth-feature-request.md).

**Credential selection**: the `CredentialConfig` discriminated union is keyed on
``kind``. Set ``kind=oauth`` to activate the OAuth path; set
``kind=user_pat`` for the explicit credential path. Only one kind
is active at a time — the discriminated union prevents co-existence. See
[`docs/oauth-operations.md`](oauth-operations.md) for the user-facing guide.

## Resource projection (layer 4)

The server exposes three `window://com.a2c-smcp.theseus-kit` resources per the
[A2C-SMCP Desktop spec](https://github.com/A2C-SMCP/a2c-smcp-protocol/blob/main/docs/specification/desktop.md)
— pure-identifier URIs, metadata via `annotations` (priority/audience), no
`_meta.fullscreen` (a single fullscreen window would exclude the others):

| Resource | Priority | Content |
|----------|----------|---------|
| `config/summary` | 0.9 | Robot identity + three-state overview |
| `config/recent` | 0.8 | The most recently opened configuration detail (`_last_locator` set by `get_config_detail`) |
| `config/topology` | 0.7 | Draft configuration reference graph (`GET /v1/factory/drafts/topology`: roots/orphans/adjacency-list nodes) |

Desktop participation requires the `resources.subscribe` capability, which the
official mcp SDK (<2.0) hardcodes off.  `subscriptions.py` patches both gaps on
a `DesktopFastMCP` subclass: the capability flag and per-session subscription
tracking (weak-keyed registry).  `notifications/resources/updated` fires after
mutations (and after `get_config_detail` changes the recent window) to every
subscribed session plus the tool-calling session.  Window failures render as
`available:false` sentinels rather than errors.  The window list is static, so
`listChanged` stays off.

## Safety invariants

- Credentials stay in the MCP server process and never enter tool output,
  resources, logs, or SKILL content.
- The two credential paths intentionally use different defaults: ``user_pat``
  requests all three configuration scopes so the kit can expose its complete
  lifecycle surface, while TFRSManager intersects that request with the PAT and
  target Robot permission ceilings; OAuth defaults to least-privilege
  ``config:read`` and requires explicit write/publish consent.  Code must not
  treat a per-request error ``scope_hint`` as a token-exchange scope request.
- Read, write, and publish capabilities remain distinct and map to the robot's
  `config:read`, `config:write`, and `config:publish` scopes.
- Mutation tools return the affected object and revision evidence where the
  upstream API provides it; errors remain actionable and redact request auth.
- Publish is an explicit operation and is never triggered as a side effect of
  draft editing or template saving.
- The large-configuration disclosure contract is frozen as a stateless
  index + structural-selector scheme (Scheme A) covering draft, template, and
  online states with an 8 KiB default / 32 KiB hard-cap budget. See
  [progressive-disclosure.md](progressive-disclosure.md) for the canonical
  spec that the read tools and `window://` resources implement.
