# theseus-kit

`theseus-kit` is an open-source MCP server for inspecting, editing, templating,
and publishing TFRobot configuration. It speaks standard MCP so it can run in
any MCP-capable client, and exposes optional A2C-SMCP-compatible `window://` and
`skill://` resources when hosted by an A2C Computer.

> Status: project scaffold only. The 0.1.0 behavior is tracked in the GitHub
> milestone and its issues; configuration mutation tools are not implemented
> yet.

## Intended 0.1.0 surface

- Read Draft, Template, and Online configuration without returning secrets.
- Update an existing draft.
- Save a draft as a reusable template.
- Validate and publish the draft configuration.
- Expose a compact configuration summary and the most recently opened detail
  through `window://com.a2c-smcp.theseus-kit/...` resources.
- Distribute task-focused editing guidance through A2C-SMCP `skill://`
  resources while remaining a valid standard MCP server.
- Reuse the robot's `/llms.txt` and `/v1/factory/llm-docs/**` documentation as
  the source of truth for version-specific configuration guidance.

The progressive-disclosure contract for large configurations is intentionally
not frozen in this scaffold. Its alternatives and acceptance criteria are
tracked as a blocking 0.1.0 design issue for maintainer approval.

## Development

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked --all-groups
uv run theseus-kit
```

Quality checks:

```bash
uv run poe ci
uv run poe build
uv run poe package-check
```

常用任务可通过 `uv run poe --help` 查看。版本更新和 PyPI/TestPyPI
Trusted Publishing 流程见 [发布文档](docs/releasing.md)。

The executable currently starts an empty MCP server over stdio. Tools and
resources will land incrementally under the 0.1.0 milestone.

## Protocol references

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [A2C-SMCP protocol](https://github.com/A2C-SMCP/a2c-smcp-protocol)

## License

MIT
