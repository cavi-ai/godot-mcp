"""MCP tools for reviewing static Godot assets."""

from functools import partial
from typing import Annotated, Any, Literal

import anyio
from cavi_mcp_core import workspace as core_workspace
from cavi_mcp_core.contracts import failure
from cavi_mcp_core.server import READ_ONLY, WRITES, Server
from pydantic import Field, StrictBool

from . import config, inspection, motion, review
from .framing import Framing
from .pose import Pose


class GodotServer(Server):
    def failure(self, name, error):
        if name == "godot_review_capture":
            return failure(name, error, unknown_next_action="godot_review_inspect")
        return failure(name, error)


mcp = GodotServer("godot-asset-review", package="cavi-godot-mcp")


def workspace():
    return core_workspace.existing_root("CAVI_GAME_ART_WORKSPACE")


async def call(function, *args):
    return await anyio.to_thread.run_sync(partial(function, *args))


@mcp.tool(annotations=READ_ONLY)
async def godot_status() -> dict[str, bool]:
    """Report whether each review dependency is set or installed."""
    return await call(config.available)


@mcp.tool(annotations=WRITES)
async def godot_review_prepare(
    source: str,
    directory: str,
    view: Literal["front", "three-quarter", "side"] = "three-quarter",
    pose: Pose | None = None,
    all_views: StrictBool = False,
    framing: Framing | None = None,
) -> dict[str, Any]:
    """Prepare a GLB review, optionally at a selected animation pose."""
    return await call(
        review.prepare,
        workspace(),
        source,
        directory,
        view,
        pose.model_dump() if pose is not None else None,
        all_views,
        framing.model_dump() if framing is not None else None,
    )


@mcp.tool(annotations=READ_ONLY)
async def godot_review_inspect(directory: str) -> dict[str, Any]:
    """Check review hashes and report pending cleanup and the latest attempt."""
    return await call(review.inspect, workspace(), directory)


@mcp.tool(annotations=WRITES)
async def godot_review_capture(directory: str) -> dict[str, Any]:
    """Capture the prepared review in an isolated browser."""
    return await call(review.capture, workspace(), directory)


@mcp.tool(annotations=WRITES)
async def godot_asset_inspect(source: str, directory: str) -> dict[str, Any]:
    """Inspect a self-contained GLB with Godot and save a source-bound scene report."""
    return await call(inspection.inspect_asset, workspace(), source, directory)


@mcp.tool(annotations=WRITES)
async def godot_motion_inspect(
    source: str,
    directory: str,
    player: str,
    animation: str,
    normalized_times: Annotated[
        list[Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0, le=1)]],
        Field(min_length=2, max_length=9),
    ],
) -> dict[str, Any]:
    """Sample a named GLB animation and save engine-evaluated transforms."""
    return await call(
        motion.inspect_motion,
        workspace(),
        source,
        directory,
        player,
        animation,
        normalized_times,
    )


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
