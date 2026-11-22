# Contributing

Use Python 3.15 or newer, `uv` and a stable Godot editor. Set `CAVI_GODOT_BIN` to the editor executable if it is outside the default locations.

```sh
uv sync --locked --dev
uv run --locked python -m unittest discover -s tests
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
```

The suite runs inspection, motion and review scene tests in headless Godot. The check and release workflows install the newest stable Linux editor and require those tests. The protocol evaluator uses the MCP server over stdio. Install a Rust toolchain to build and run it:

```sh
uv run --locked python evals/evaluator.py build --root .tmp/mcp-eval --branch main
MCPEVAL_EXECUTABLE="$PWD/.tmp/mcp-eval/bin/mcpeval" uv run --locked python evals/run.py
```

The evaluator build records its Git revision and executable hash; evaluation rejects a changed executable.

For changes to capture or rendering, prepare and capture a GLB in a separate workspace with the matching Web template, Chromium and Node.js. Check the rendered PNG and use `godot_review_inspect` to verify the bundle.
