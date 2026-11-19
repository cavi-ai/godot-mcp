"""Review tool locations: an environment variable, else the installed application."""

import os
import re
import sys
from pathlib import Path

from cavi_mcp_core import apps

GODOT = "CAVI_GODOT_BIN"
TEMPLATE = "CAVI_GODOT_WEB_TEMPLATE"
CHROMIUM = "CAVI_CHROMIUM_BIN"
NODE = "CAVI_NODE_BIN"
REVIEW_TOOLS = (GODOT, TEMPLATE, CHROMIUM, NODE)
# Each executable's default install and its command name on PATH.
EXECUTABLES = {
    GODOT: (Path("/Applications/Godot.app"), "godot"),
    CHROMIUM: (Path("/Applications/Google Chrome.app"), "chromium"),
    NODE: (Path("/opt/homebrew/bin/node"), "node"),
}
WEB_TEMPLATE = "web_nothreads_release.zip"
VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.([a-z]+\d*))?")


def export_templates():
    """The directory Godot installs export templates into for this user."""
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Godot/export_templates"
    data = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
    return Path(data) / "godot/export_templates"


def web_template(godot):
    """The installed single-threaded Web template for the editor's version, if exactly one."""
    match = VERSION.match(apps.version(godot) or "") if godot else None
    if match is None:
        return None
    major, minor, patch, status = match.groups()
    number = f"{major}.{minor}" + (f".{patch}" if patch and patch != "0" else "")
    name = re.compile(re.escape(number) + r"\.([a-z]+\d*)")
    found = [
        path / WEB_TEMPLATE
        for path in export_templates().glob(number + ".*")
        if (installed := name.fullmatch(path.name))
        and status in (None, installed.group(1))
        and (path / WEB_TEMPLATE).is_file()
    ]
    return found[0] if len(found) == 1 else None


def locate(name):
    """The tool's resolved file, or None. A set variable is never replaced by a default."""
    if name == TEMPLATE:
        value = os.environ.get(TEMPLATE)
        path = Path(value).expanduser() if value else web_template(locate(GODOT))
    else:
        default, command = EXECUTABLES[name]
        path = apps.find_executable(default, command, name, None).path
    if path is None or not path.is_file():
        return None
    return path.resolve()


def available():
    return {name: locate(name) is not None for name in REVIEW_TOOLS}


def tools(names=REVIEW_TOOLS):
    """Each named tool's resolved file; refuses when any is not found."""
    found = {name: locate(name) for name in names}
    missing = [name for name, path in found.items() if path is None]
    if missing:
        raise ValueError(
            "Review tools not found; install them or set " + ", ".join(missing)
        )
    return found
