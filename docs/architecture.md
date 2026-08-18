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

The TFRobot client authenticates via `tfrs-auth` and routes over the cluster's
`api.<clusterDomain>` entry. Contract (confirmed by tfrs-operator, tracked in
[#17](https://github.com/A2C-SMCP/theseus-kit/issues/17)):

- **Entry**: `https://api.<clusterDomain>` (Istio gateway, host-authoritative);
  the in-cluster `tfrobot-http.<ns>` service is not reachable from outside the
  cluster and is never configured by theseus-kit.
- **Routing headers**: every robot request carries `X-TF-Namespace`,
  `X-TF-RobotId`, and `X-TF-RobotType` — all three mandatory (missing any ⇒ 400).
  theseus-kit produces them from explicit config via `RequestContext`; it never
  self-derives them and has no dependency on a Manager discovery API.
- **Identity split**: the token `audience` is `robot:<Account.ID>` (numeric) — a
  *different* identifier from `X-TF-RobotId` (the rid). These must never be
  conflated; a single robot has both.
- **Credentials**: `user_pat` (user personal access token exchanged via
  token-exchange for a robot-scoped JWT) is the explicit credential path.
  Uses `tfrs-auth`'s `AsyncCachingTokenSource` (cache / single-flight /
  near-expiry refresh / backoff) — theseus-kit reimplements none of that
  machinery.
- **No auto refresh-retry on robot 401/403**: a rejected token surfaces as a
  typed `AuthRejectedError`. Refresh-on-401 would require an upstream
  token-source invalidator (not yet available); until then it is out of scope.

## OAuth (no-PAT) path (#18)

When ``kind=oauth`` is configured, theseus-kit acts as an OAuth
Protected Resource (RS) per the MCP Authorization specification — an alternative
to the ``user_pat`` path. The MCP Client
drives the standard OAuth 2.0/2.1 authorization-code + PKCE flow against the
TFRSManager AS; theseus-kit validates the resulting Bearer token and forwards it
directly to TFRobotServer — **no token exchange** (TFRobotServer natively accepts
OAuth AS tokens, confirmed by the TFRobotServer team). Full rationale:
[`docs/auth-oauth-design.md`](auth-oauth-design.md) §10.1-new.

The two credential paths converge at `RobotClient` but follow different mechanics
upstream:

**Path A — user_pat** (exchange pipeline, #17):

```
TheseusSettings(credential)
  → build_credential()         # config → tfrs_auth.PatCredential
  → build_token_source()       # AsyncCachingTokenSource (cache / single-flight / near-expiry refresh / backoff)
  → RobotClient(RobotAuth)     # Bearer + X-TF-* per request
```

**Path B — OAuth** (direct forward, #18 S1–S3):

```
MCP Client (OAuth授权 + Bearer)
  → TheseusTokenVerifier       # RS256 + JWKS (tfrs-auth JwtVerifier), local validation
  → StaticTokenSource          # pre-validated token, no exchange / caching / refresh
  → RobotClient(RobotAuth)     # same Bearer + X-TF-* injection
```

| Path | Token source | Exchange (RFC 8693) | Cache / refresh |
|------|-------------|---------------------|-----------------|
| user_pat | `AsyncCachingTokenSource` | Yes (RFC 8693 token-exchange) | Yes |
| OAuth | `StaticTokenSource` | No (direct forward) | No (MCP Client side) |

Key modules added for #18 (see [`auth-oauth-design.md`](auth-oauth-design.md) §12 for
the full slice breakdown):

- **`oauth.py`**: `TheseusTokenVerifier` — adapts `tfrs_auth.JwtVerifier` →
  MCP SDK `TokenVerifier` protocol. `build_token_verifier()` performs RFC 8414
  AS metadata discovery to obtain ``jwks_uri`` and ``issuer``.
- **`server.py`**: `_LazyOAuthTokenVerifier` defers async AS discovery to the first
  ``verify_token`` call (single-flight lock). `create_mcp_server()` wires
  `AuthSettings` + `token_verifier` when `OAuthConfig` is detected.
- **`transport.py`**: `StaticTokenSource` — holds a pre-validated bearer with no
  exchange/caching/refresh. `RobotClient.for_static_token()` factory for the OAuth
  path. `RobotAuth` accepts `AsyncCachingTokenSource | StaticTokenSource`.
- **`config.py`**: `OAuthConfig` — `authorization_server`, `scopes`, `client_id`,
  `redirect_uri`, `resource_server_url`. Added as third variant to the
  `CredentialConfig` discriminated union.

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
