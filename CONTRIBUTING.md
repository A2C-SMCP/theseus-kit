# Contributing

Development targets the `develop` branch. Keep each change tied to a GitHub
issue in the active milestone.

Before opening a pull request, run:

```bash
uv sync
uv run poe check
uv run poe test
```

Protocol-facing changes must preserve standard MCP interoperability. A2C-SMCP
metadata and URI schemes are additive extensions and must not be required for
using the server from a generic MCP client.

