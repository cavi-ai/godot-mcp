# Cavi Godot MCP

Review a static textured GLB as an interactive Godot Web scene and capture a source-bound image. The server runs locally over MCP stdio.

## Install

Requires Python 3.15 or newer and `uv`. In a source checkout:

```sh
uv sync --locked
```

The command is `.venv/bin/cavi-godot-mcp`. Configure an MCP client with that executable and these environment variables:

```json
{
  "mcpServers": {
    "godot": {
      "command": "/absolute/path/to/godot-mcp/.venv/bin/cavi-godot-mcp",
      "env": {
        "CAVI_GAME_ART_WORKSPACE": "/absolute/path/to/workspace",
        "CAVI_GODOT_BIN": "/absolute/path/to/godot",
        "CAVI_GODOT_WEB_TEMPLATE": "/absolute/path/to/web_nothreads_release.zip",
        "CAVI_CHROMIUM_BIN": "/absolute/path/to/chrome-headless-shell",
        "CAVI_NODE_BIN": "/absolute/path/to/node"
      }
    }
  }
}
```

Capture requires macOS or Linux. Use a Godot editor and matching single-threaded Web export template, a headless Chromium with WebGL 2 support, and Node.js 22 or newer. The workspace must already exist. Tool discovery and `godot_status` do not launch these programs.

## Review an asset

1. Place a self-contained static textured `.glb` in the workspace. Inputs are static GLB 2.0 files up to 64 MiB with embedded PNG or JPEG textures; skins and animations are rejected.
2. Call `godot_review_prepare` with its relative `source`, a new relative `directory`, and a `view` of `front`, `three-quarter`, or `side`.
3. Call `godot_review_capture` with the review directory. It imports and exports with Godot, captures a rendered frame in headless Chromium, and records its inputs and process evidence.
4. Call `godot_review_inspect` to check the source, tool and artifact hashes and inspect pending cleanup and the latest attempt. Open the capture for visual review. Review inspection limits receipt metadata to 1 MiB and individual artifacts to 256 MiB.

To use the interactive view, serve the receipt's relative `bundle` within the review directory:

```sh
python3 -m http.server --bind 127.0.0.1 --directory <review-directory>/<bundle>
```

Drag to orbit, scroll to zoom, and press R to reset. The white bar is one metre long. Stop the server after review.

Godot's headless mode does not render; the capture uses a Godot Web export with the Compatibility renderer and WebGL 2. A prop-only input cannot establish room architecture, collision, or traversal. Godot appearance is not Unity toon appearance: perform final import, visual, and runtime acceptance in the consuming engine.

If capture reports an unknown outcome, call `godot_review_inspect` and inspect the latest attempt's evidence. A pending cleanup marker blocks another capture; do not remove it until the owned processes and output have been resolved. Report security concerns privately as described in [SECURITY.md](SECURITY.md).
