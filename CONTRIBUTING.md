# Contributing

Use Python 3.15 or newer, `uv`, and a Rust toolchain for the protocol evaluator.

```sh
uv sync --locked --dev
uv run --locked python -m unittest discover -s tests
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
```

The unit suite and protocol evaluator do not launch Godot or Chromium. Build and run the evaluator with:

```sh
uv run --locked python evals/evaluator.py build --root .tmp/mcp-eval --branch main
MCPEVAL_EXECUTABLE="$PWD/.tmp/mcp-eval/bin/mcpeval" uv run --locked python evals/run.py
```

The evaluator build records its Git revision and executable hash; evaluation rejects a changed executable.

Native visual acceptance needs the configured Godot editor, matching Web template, headless Chromium and Node. Keep its workspace isolated, retain actual process and image evidence, and inspect the result. Native visual acceptance requires an actual Godot render and the resulting image evidence.
