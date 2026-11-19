"""Review tool locations configured through the environment."""

import os
from pathlib import Path

GODOT = "CAVI_GODOT_BIN"
REVIEW_TOOLS = (GODOT, "CAVI_GODOT_WEB_TEMPLATE", "CAVI_CHROMIUM_BIN", "CAVI_NODE_BIN")


def tools(names=REVIEW_TOOLS):
    """Each named tool's resolved file; refuses a missing variable or a non-file."""
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise ValueError("Missing review configuration: " + ", ".join(missing))
    resolved = {name: Path(os.environ[name]).resolve() for name in names}
    if any(not path.is_file() for path in resolved.values()):
        raise ValueError("Configured tools must be files")
    return resolved
