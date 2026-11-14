"""MCP tools for reviewing static Godot assets."""

import os
from functools import partial
from pathlib import Path
from typing import Any, Literal

import anyio
from cavi_mcp_core.contracts import failure
from cavi_mcp_core.server import READ_ONLY, Server
from mcp.types import ToolAnnotations

from . import review


class GodotServer(Server):
    def failure(self, name, error):
        action = (
            "godot_review_inspect"
            if name == "godot_review_capture"
            else "inspect_inputs"
        )
        return failure(name, error, unknown_next_action=action)


mcp = GodotServer("godot-asset-review", package="cavi-godot-mcp")
WRITES = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


def workspace():
    value = os.environ.get("CAVI_GAME_ART_WORKSPACE")
    if not value:
        raise ValueError("CAVI_GAME_ART_WORKSPACE is required")
    return Path(value).resolve(strict=True)


async def call(function, *args):
    return await anyio.to_thread.run_sync(partial(function, *args))


@mcp.tool(annotations=READ_ONLY)
async def godot_status() -> dict[str, bool]:
    """Report whether each review dependency is configured without launching it."""
    return {
        name: bool(os.environ.get(name))
        for name in (
            "CAVI_GODOT_BIN",
            "CAVI_GODOT_WEB_TEMPLATE",
            "CAVI_CHROMIUM_BIN",
            "CAVI_NODE_BIN",
        )
    }


@mcp.tool(annotations=WRITES)
async def godot_review_prepare(
    source: str,
    directory: str,
    view: Literal["front", "three-quarter", "side"] = "three-quarter",
) -> dict[str, Any]:
    """Prepare a new owned static GLB review in the workspace."""
    return await call(review.prepare, workspace(), source, directory, view)


@mcp.tool(annotations=READ_ONLY)
async def godot_review_inspect(directory: str) -> dict[str, Any]:
    """Check review hashes and report pending cleanup and the latest attempt."""
    return await call(review.inspect, workspace(), directory)


@mcp.tool(annotations=WRITES)
async def godot_review_capture(directory: str) -> dict[str, Any]:
    """Capture the prepared review in an isolated browser."""
    return await call(review.capture, workspace(), directory)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
