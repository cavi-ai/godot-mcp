"""Owned, source-bound review requests for static GLB assets."""

import hashlib
import json
import os
import struct
import tempfile
from datetime import UTC
from pathlib import Path

LIMIT = 64 * 1024 * 1024


class CleanupUnverified(RuntimeError):
    """A spawned process group may still be active."""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(root, relative):
    root = Path(root).resolve(strict=True)
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts:
        raise ValueError("Path must be relative to the workspace")
    path = (root / rel).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Path must be inside the workspace")
    return path


def read_glb(path):
    raw = path.read_bytes()
    if len(raw) < 20 or len(raw) > LIMIT:
        raise ValueError("Invalid GLB size")
    magic, version, length = struct.unpack_from("<4sII", raw)
    if magic != b"glTF" or version != 2 or length != len(raw):
        raise ValueError("Invalid GLB header")
    n, kind = struct.unpack_from("<I4s", raw, 12)
    if kind != b"JSON" or n % 4 or n + 20 > len(raw):
        raise ValueError("Invalid GLB JSON chunk")
    document = json.loads(raw[20 : 20 + n])
    if document.get("skins") or document.get("animations"):
        raise ValueError("Only static GLB assets are supported")
    if not document.get("meshes") or not document.get("nodes"):
        raise ValueError("GLB needs mesh nodes")
    buffers = document.get("buffers", [])
    if len(buffers) != 1 or "uri" in buffers[0]:
        raise ValueError("Buffers must be embedded")
    position = 20 + n
    if position + 8 > len(raw):
        raise ValueError("Missing embedded binary buffer")
    binary_length, binary_kind = struct.unpack_from("<I4s", raw, position)
    if binary_kind != b"BIN\0" or position + 8 + binary_length != len(raw):
        raise ValueError("Invalid embedded binary buffer")
    if (
        type(buffers[0].get("byteLength")) is not int
        or buffers[0]["byteLength"] > binary_length
    ):
        raise ValueError("Invalid binary buffer length")
    views = document.get("bufferViews", [])
    for view in views:
        start, size = view.get("byteOffset", 0), view.get("byteLength")
        if (
            view.get("buffer") != 0
            or type(start) is not int
            or type(size) is not int
            or start < 0
            or size < 1
            or start + size > buffers[0]["byteLength"]
        ):
            raise ValueError("Invalid embedded buffer view")
    images = document.get("images", [])
    if not images or any(
        "uri" in image
        or type(image.get("bufferView")) is not int
        or not 0 <= image["bufferView"] < len(views)
        or image.get("mimeType") not in ("image/png", "image/jpeg")
        for image in images
    ):
        raise ValueError("Images must be embedded PNG or JPEG")
    for texture in document.get("textures", []):
        if type(texture.get("source")) is not int or not 0 <= texture["source"] < len(
            images
        ):
            raise ValueError("Texture must reference an embedded image")
    texture_count = len(document.get("textures", []))
    for material in document.get("materials", []):
        pbr = material.get("pbrMetallicRoughness", {})
        slots = [
            pbr.get("baseColorTexture"),
            pbr.get("metallicRoughnessTexture"),
            material.get("normalTexture"),
            material.get("occlusionTexture"),
            material.get("emissiveTexture"),
        ]
        for slot in slots:
            if slot is not None and (
                not isinstance(slot, dict)
                or type(slot.get("index")) is not int
                or not 0 <= slot["index"] < texture_count
            ):
                raise ValueError("Invalid material texture reference")
    return document


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".receipt-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def prepare(root, source, directory, view="three-quarter"):
    if view not in ("front", "three-quarter", "side"):
        raise ValueError("Unsupported view")
    src, dest = inside(root, source), inside(root, directory)
    if not src.is_file() or src.suffix.lower() != ".glb":
        raise ValueError("Source must be a GLB file")
    read_glb(src)
    dest.mkdir(parents=False, exist_ok=False)
    receipt = {
        "version": 1,
        "kind": "godot-asset-review",
        "source": source,
        "source_sha256": digest(src),
        "view": view,
        "state": "prepared",
    }
    owner = {
        "kind": receipt["kind"],
        "source": source,
        "source_sha256": receipt["source_sha256"],
        "view": view,
    }
    atomic_json(dest / "owner.json", owner)
    atomic_json(dest / "review.json", receipt)
    return receipt


def provider_identity():
    from importlib.metadata import version

    package = Path(__file__).parent
    return {
        "version": version("cavi-godot-mcp"),
        "source": {p.name: digest(p) for p in package.glob("*.py")},
        "runtime": {
            p.name: digest(p) for p in (package / "runtime").iterdir() if p.is_file()
        },
    }


def input_snapshot(root, receipt, tools):
    source_hash = digest(inside(root, receipt["source"]))
    if source_hash != receipt["source_sha256"]:
        raise ValueError("Source changed")
    return {
        "source_sha256": source_hash,
        "view": receipt["view"],
        "provider": provider_identity(),
        "tools": {name: digest(path) for name, path in tools.items()},
    }


def inspect(root, directory):
    dest = inside(root, directory)
    receipt = json.loads((dest / "review.json").read_text())
    if receipt.get("kind") != "godot-asset-review" or receipt.get("version") != 1:
        raise ValueError("Invalid review ownership marker")
    owner = json.loads((dest / "owner.json").read_text())
    if owner != {
        key: receipt[key] for key in ("kind", "source", "source_sha256", "view")
    }:
        raise ValueError("Review ownership marker changed")
    if digest(inside(root, receipt["source"])) != receipt["source_sha256"]:
        raise ValueError("Source changed")
    if receipt.get("state") == "captured":
        tools = {
            name: Path(os.environ[name]).resolve(strict=True)
            for name in receipt["inputs"]["tools"]
        }
        if input_snapshot(root, receipt, tools) != receipt["inputs"]:
            raise ValueError("Review input changed")
    for relative, expected in receipt.get("artifacts", {}).items():
        path = (dest / relative).resolve()
        if not path.is_relative_to(dest) or digest(path) != expected:
            raise ValueError("Review artifact changed")
    return receipt


def run_owned(command, timeout, evidence=None, label="process"):
    import signal
    import subprocess
    import time
    from datetime import datetime

    observations = []

    def group_members(group):
        result = subprocess.run(
            ["ps", "-axo", "pid=,pgid=,uid=,stat="],
            capture_output=True,
            text=True,
            check=False,
        )
        observations.append(
            {
                "command": result.args,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        )
        if result.returncode:
            raise RuntimeError("Could not inspect process groups")
        members = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if (
                len(parts) == 4
                and int(parts[1]) == group
                and not parts[3].startswith("Z")
            ):
                members.append((int(parts[0]), int(parts[2])))
        return members

    started = datetime.now(UTC).isoformat()
    with tempfile.TemporaryDirectory() as scratch:
        output_dir = evidence or Path(scratch)
        out_path, err_path = (
            output_dir / (label + ".stdout"),
            output_dir / (label + ".stderr"),
        )
        timed_out = False
        with out_path.open("wb") as stdout, err_path.open("wb") as stderr:
            process = subprocess.Popen(
                command, stdout=stdout, stderr=stderr, start_new_session=True
            )
            deadline = time.monotonic() + timeout
            execution_error = None
            cleanup_error = None
            try:
                while not os.waitid(
                    os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT
                ):
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    time.sleep(0.05)
                group_members(process.pid)
            except (
                OSError,
                RuntimeError,
                subprocess.SubprocessError,
                ValueError,
            ) as exc:
                execution_error = exc
            finally:
                members = None
                try:
                    members = group_members(process.pid)
                    if any(uid != os.getuid() for _, uid in members):
                        members = []
                        raise RuntimeError("Process group contains an unowned process")
                except (
                    OSError,
                    RuntimeError,
                    subprocess.SubprocessError,
                    ValueError,
                ) as exc:
                    cleanup_error = exc
                try:
                    # This leader is unreaped, so the group id is pinned to this launch.
                    if members is None or members:
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    cleanup_error = cleanup_error or exc
                try:
                    remaining = group_members(process.pid)
                    cleanup_deadline = time.monotonic() + 3
                    while remaining and time.monotonic() < cleanup_deadline:
                        time.sleep(0.05)
                        remaining = group_members(process.pid)
                    if remaining:
                        raise RuntimeError("Owned processes remain after cleanup")
                except (
                    OSError,
                    RuntimeError,
                    subprocess.SubprocessError,
                    ValueError,
                ) as exc:
                    cleanup_error = cleanup_error or exc
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired as exc:
                    cleanup_error = cleanup_error or exc
        out, err = out_path.read_bytes(), err_path.read_bytes()
        if evidence is not None:
            atomic_json(
                output_dir / (label + ".groups.json"), {"observations": observations}
            )
            atomic_json(
                output_dir / (label + ".json"),
                {
                    "command": command,
                    "started": started,
                    "finished": datetime.now(UTC).isoformat(),
                    "returncode": process.returncode,
                    "timeout": timed_out,
                    "execution_error": repr(execution_error)
                    if execution_error
                    else None,
                    "cleanup_error": repr(cleanup_error) if cleanup_error else None,
                },
            )
        if cleanup_error is not None:
            raise CleanupUnverified(
                f"Process cleanup failed: {cleanup_error}"
            ) from cleanup_error
        if execution_error is not None:
            raise RuntimeError(
                f"Process execution failed: {execution_error}"
            ) from execution_error
        if timed_out:
            raise subprocess.TimeoutExpired(command, timeout, output=out, stderr=err)
        if process.returncode:
            raise RuntimeError(
                "Review process failed: " + err.decode(errors="replace")[-4000:]
            )
        return out.decode(errors="replace")


def share_runtime(root, wasm):
    cache = inside(root, ".godot-review-runtime")
    cache.mkdir(exist_ok=True)
    fingerprint = digest(wasm)
    target = cache / (fingerprint + ".wasm")
    try:
        os.link(wasm, target)
    except FileExistsError:
        if not target.is_file() or digest(target) != fingerprint:
            raise ValueError("Shared runtime cache changed")
    replacement = wasm.with_suffix(".shared")
    os.link(target, replacement)
    os.replace(replacement, wasm)


def capture(root, directory):
    import fcntl

    dest = inside(root, directory)
    inspect(root, directory)
    fd = os.open(
        dest / ".capture.lock",
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("A capture is already active") from exc
        marker = dest / ".capture.pending"
        if marker.exists():
            raise CleanupUnverified("Previous capture cleanup is unverified")
        atomic_json(marker, {"kind": "capture_pending"})
        verified = False
        try:
            result = capture_locked(root, directory)
            verified = True
            return result
        except CleanupUnverified:
            raise
        except Exception:
            verified = True
            raise
        finally:
            if verified:
                marker.unlink()
    finally:
        os.close(fd)


def capture_locked(root, directory):
    import http.server
    import shutil
    import socketserver
    import threading

    receipt = inspect(root, directory)
    dest = inside(root, directory)
    if receipt["state"] != "prepared":
        raise ValueError("Review is not prepared")
    tools = {
        name: Path(os.environ[name]).resolve(strict=True)
        for name in (
            "CAVI_GODOT_BIN",
            "CAVI_GODOT_WEB_TEMPLATE",
            "CAVI_CHROMIUM_BIN",
            "CAVI_NODE_BIN",
        )
    }
    if any(not p.is_file() for p in tools.values()):
        raise ValueError("Configured tools must be files")
    runtime = Path(__file__).parent / "runtime"
    inputs = input_snapshot(root, receipt, tools)
    attempts = dest / "attempts"
    attempts.mkdir(exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="attempt-", dir=attempts))
    atomic_json(
        evidence / "request.json",
        {
            "source_sha256": receipt["source_sha256"],
            "view": receipt["view"],
            "directory": directory,
        },
    )
    with tempfile.TemporaryDirectory(prefix=".godot-review-", dir=dest) as temp:
        project = Path(temp) / "project"
        project.mkdir()
        for filename in ("project.godot", "main.gd", "main.tscn"):
            shutil.copyfile(runtime / filename, project / filename)
        script = project / "main.gd"
        script.write_text(
            script.read_text().replace(
                "VIEW_YAW",
                {"front": "0.0", "three-quarter": "0.65", "side": "1.5708"}[
                    receipt["view"]
                ],
            )
        )
        shutil.copyfile(inside(root, receipt["source"]), project / "subject.glb")
        if digest(project / "subject.glb") != inputs["source_sha256"]:
            raise ValueError("Copied source changed")
        for filename in ("project.godot", "main.tscn"):
            if digest(project / filename) != inputs["provider"]["runtime"][filename]:
                raise ValueError("Copied runtime changed")
        expected_script = (
            (runtime / "main.gd")
            .read_text()
            .replace(
                "VIEW_YAW",
                {"front": "0.0", "three-quarter": "0.65", "side": "1.5708"}[
                    receipt["view"]
                ],
            )
        )
        if (project / "main.gd").read_text() != expected_script or digest(
            runtime / "main.gd"
        ) != inputs["provider"]["runtime"]["main.gd"]:
            raise ValueError("Copied runtime changed")
        export = project / "bundle"
        export.mkdir()
        preset = (
            '[preset.0]\nname="Web"\nplatform="Web"\nrunnable=true\n'
            'export_filter="all_resources"\ninclude_filter=""\nexclude_filter=""\n'
            "[preset.0.options]\nvariant/thread_support=false\n"
            "custom_template/release="
            + json.dumps(str(tools["CAVI_GODOT_WEB_TEMPLATE"]))
            + "\n"
        )
        (project / "export_presets.cfg").write_text(preset)
        godot = str(tools["CAVI_GODOT_BIN"])
        imported = run_owned(
            [godot, "--headless", "--path", str(project), "--editor", "--import"],
            120,
            evidence,
            "import",
        )
        if (
            "ERROR:" in imported
            or b"ERROR:" in (evidence / "import.stderr").read_bytes()
        ):
            raise RuntimeError("Godot reported an import error; inspect the raw logs")
        exported = run_owned(
            [
                godot,
                "--headless",
                "--path",
                str(project),
                "--export-release",
                "Web",
                str(export / "index.html"),
            ],
            120,
            evidence,
            "export",
        )
        if (
            "ERROR:" in exported
            or b"ERROR:" in (evidence / "export.stderr").read_bytes()
        ):
            raise RuntimeError("Godot reported an export error; inspect the raw logs")

        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        from functools import partial

        handler = partial(QuietHandler, directory=str(export))
        with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
                run_owned(
                    [
                        str(tools["CAVI_NODE_BIN"]),
                        str(runtime / "capture.mjs"),
                        str(tools["CAVI_CHROMIUM_BIN"]),
                        url,
                        str(export / "capture.png"),
                        str(Path(temp) / "browser"),
                    ],
                    90,
                    evidence,
                    "browser",
                )
            finally:
                server.shutdown()
                thread.join()
        godot_version = run_owned([godot, "--version"], 10, evidence, "version").strip()
        inspect(root, directory)
        if input_snapshot(root, receipt, tools) != inputs:
            raise ValueError("Review input changed during capture")
        share_runtime(root, export / "index.wasm")
        bundle = evidence / "bundle"
        receipt["state"] = "captured"
        receipt["project"] = {
            name: digest(project / name)
            for name in (
                "project.godot",
                "main.gd",
                "main.tscn",
                "export_presets.cfg",
                "subject.glb",
            )
        }
        receipt["inputs"] = inputs
        receipt["bundle"] = str(bundle.relative_to(dest))
        receipt["godot_version"] = godot_version
        receipt["renderer"] = "godot-web-compatibility-webgl2"
        receipt["scene"] = json.loads((export / "capture.png.json").read_text())
        receipt["artifacts"] = {
            str((bundle / path.relative_to(export)).relative_to(dest)): digest(path)
            for path in export.rglob("*")
            if path.is_file()
        }
        for filename in ("project.godot", "main.gd", "main.tscn", "export_presets.cfg"):
            shutil.copyfile(project / filename, evidence / filename)
        receipt["artifacts"].update(
            {
                str(path.relative_to(dest)): digest(path)
                for path in evidence.iterdir()
                if path.is_file()
            }
        )
        atomic_json(evidence / "result.json", receipt)
        os.rename(export, bundle)
        try:
            atomic_json(dest / "review.json", receipt)
        except BaseException:
            shutil.rmtree(bundle)
            raise
        return receipt
