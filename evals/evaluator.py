"""Build the evaluator and bind its binary to its Cargo source record."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cavi_mcp_core import owned_exec


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def source_from_metadata(path):
    installs = json.loads(path.read_text())["installs"]
    entries = [key for key in installs if key.startswith("mcpeval ")]
    if len(entries) != 1 or "github.com/cavi-ai/mcp-eval" not in entries[0]:
        raise ValueError("Evaluator install metadata lacks its source")
    source = entries[0]
    revision = source.rsplit("#", 1)[-1].removesuffix(")")
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("Evaluator install metadata lacks a full revision")
    return source, revision


def validate_binding(binary):
    binary = Path(binary).resolve(strict=True)
    root = binary.parent.parent
    record = json.loads((root / "build-record.json").read_text())
    source, revision = source_from_metadata(root / ".crates2.json")
    if (
        record.get("returncode") != 0
        or record.get("version_returncode") != 0
        or not isinstance(record.get("command"), list)
        or not record.get("started")
        or not record.get("finished")
        or record.get("install_source") != source
        or record.get("source_revision") != revision
    ):
        raise ValueError("Evaluator build source differs")
    if record.get("metadata_sha256") != sha(root / ".crates2.json"):
        raise ValueError("Evaluator metadata changed")
    if record.get("executable_sha256") != sha(binary):
        raise ValueError("Evaluator executable changed")
    for name in ("stdout", "stderr"):
        if record.get(name + "_sha256") != sha(root / ("build." + name)):
            raise ValueError("Evaluator build output changed")
    return record


def build(root, revision=None, branch="main"):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "build-record.json").exists():
        raise ValueError("Evaluator build record already exists")
    command = [
        "cargo",
        "install",
        "--locked",
        "--git",
        "https://github.com/cavi-ai/mcp-eval",
        "--rev" if revision else "--branch",
        revision or branch,
        "--root",
        str(root),
        "mcpeval",
    ]
    env = dict(os.environ)
    started = datetime.now(UTC).isoformat()
    actual_command = owned_exec.command(command, env=env)
    process = subprocess.run(actual_command, env=env, capture_output=True, check=False)
    finished = datetime.now(UTC).isoformat()
    binary = root / "bin" / "mcpeval"
    metadata = root / ".crates2.json"
    # Capture the executable and installation metadata immediately after a successful build.
    executable_hash = sha(binary) if process.returncode == 0 else None
    metadata_hash = sha(metadata) if process.returncode == 0 else None
    source, resolved = (
        source_from_metadata(metadata) if process.returncode == 0 else (None, None)
    )
    (root / "build.stdout").write_bytes(process.stdout)
    (root / "build.stderr").write_bytes(process.stderr)
    version = None
    version_returncode = None
    version_command = [str(binary), "--version"]
    if process.returncode == 0:
        result = subprocess.run(
            owned_exec.command(version_command, env=env),
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )
        version = result.stdout.strip()
        version_returncode = result.returncode
        (root / "version.stdout").write_text(result.stdout)
        (root / "version.stderr").write_text(result.stderr)
    record = {
        "command": command,
        "actual_command": actual_command,
        "version_command": version_command,
        "version_returncode": version_returncode,
        "started": started,
        "finished": finished,
        "returncode": process.returncode,
        "install_source": source,
        "source_revision": resolved,
        "metadata_sha256": metadata_hash,
        "executable_sha256": executable_hash,
        "version": version,
        "stdout_sha256": sha(root / "build.stdout"),
        "stderr_sha256": sha(root / "build.stderr"),
    }
    (root / "build-record.json").write_text(json.dumps(record, indent=2) + "\n")
    if process.returncode:
        raise RuntimeError("Evaluator build failed; inspect its logs")
    if version_returncode != 0 or version != "mcpeval " + source.split()[1]:
        raise ValueError("Evaluator version differs from metadata")
    if revision and resolved != revision:
        raise ValueError("Evaluator revision differs")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build"])
    parser.add_argument("--root", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--rev")
    group.add_argument("--branch", default="main")
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.rev, args.branch), indent=2))


if __name__ == "__main__":
    main()
