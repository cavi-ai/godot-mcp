# Godot MCP

An optional stdio provider for reviewing static textured GLB assets in Godot Web Compatibility rendering.

Run `uv sync --no-editable` in this repository; the provider entrypoint is `.venv/bin/cavi-godot-mcp`. Set `CAVI_GAME_ART_WORKSPACE` to an existing workspace, `CAVI_GODOT_BIN` to a Godot editor executable, `CAVI_GODOT_WEB_TEMPLATE` to a compatible single-threaded Web export template, `CAVI_CHROMIUM_BIN` to a Chromium headless shell, and `CAVI_NODE_BIN` to Node 22 or newer. The provider does not start an application when listing tools or checking status.

Prepare a new workspace-relative review directory with `godot_review_prepare`, capture with `godot_review_capture`, and check source and artifact hashes with `godot_review_inspect`. The retained bundle can be served locally with `python3 -m http.server --bind 127.0.0.1 --directory <review>/<bundle>`. Drag to orbit, scroll to zoom, and press R to reset. The ruler is one metre long. Stop the server after review.

Captures show Godot Web Compatibility rendering. Review the image separately and perform final acceptance in the consuming engine.
