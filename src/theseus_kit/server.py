"""MCP server composition root."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="theseus-kit",
    instructions=(
        "Inspect and manage TFRobot configuration. Read the exposed editing skills "
        "and version-specific llms.txt documentation before mutating configuration."
    ),
)


def main() -> None:
    """Run the MCP server over the portable stdio transport."""
    mcp.run(transport="stdio")
