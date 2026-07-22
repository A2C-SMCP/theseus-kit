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

