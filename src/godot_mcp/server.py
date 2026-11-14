"""Scoped tools for reviewing static Godot assets."""

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import review

mcp = MCPServer("godot-asset-review")


def workspace():
    return Path(os.environ["CAVI_GAME_ART_WORKSPACE"]).resolve(strict=True)


@mcp.tool()
def godot_status() -> dict:
    """Read configured paths without launching a process."""
    return {
        name: bool(os.environ.get(name))
        for name in (
            "CAVI_GODOT_BIN",
            "CAVI_GODOT_WEB_TEMPLATE",
            "CAVI_CHROMIUM_BIN",
            "CAVI_NODE_BIN",
        )
    }


@mcp.tool()
def godot_review_prepare(
    source: str, directory: str, view: str = "three-quarter"
) -> dict:
    """Prepare a new owned static GLB review."""
    return review.prepare(workspace(), source, directory, view)


@mcp.tool()
def godot_review_inspect(directory: str) -> dict:
    """Check review source and artifact hashes."""
    return review.inspect(workspace(), directory)


@mcp.tool()
def godot_review_capture(directory: str) -> dict:
    """Capture the prepared review in an isolated browser."""
    return review.capture(workspace(), directory)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
