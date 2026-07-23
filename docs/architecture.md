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
- **Credentials**: `client_credentials` (the robot's own machine credential; the
  audience self-derives as `robot:<machine_client_id>`) is the primary source.
  `user_pat` is supported. Both reuse `tfrs-auth`'s `AsyncCachingTokenSource`
  (cache / single-flight / near-expiry refresh / backoff) — theseus-kit
  reimplements none of that machinery.
- **No auto refresh-retry on robot 401/403**: a rejected token surfaces as a
  typed `AuthRejectedError`. Refresh-on-401 would require an upstream
  token-source invalidator (not yet available); until then it is out of scope.

## Safety invariants

- Credentials stay in the MCP server process and never enter tool output,
  resources, logs, or SKILL content.
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

