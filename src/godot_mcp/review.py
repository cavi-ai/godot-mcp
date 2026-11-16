"""Owned, source-bound review requests for static GLB assets."""

import hashlib
import json
import os
import stat
import struct
import tempfile
from datetime import UTC
from pathlib import Path

from cavi_mcp_core import files, journal, owned_exec, processes
from cavi_mcp_core.contracts import OperationUnknown

LIMIT = 64 * 1024 * 1024
ARTIFACT_LIMIT = 256 * 1024 * 1024


class CleanupUnverified(OperationUnknown):
    """A spawned process group may still be active."""

    def __init__(self, message):
        super().__init__(message, code="CLEANUP_UNVERIFIED")


def digest(path, limit=None):
    """Hash an unchanged regular file without buffering it in memory."""
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    result = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or (
            limit is not None and before.st_size > limit
        ):
            raise ValueError("Expected a bounded regular file")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
        after = os.fstat(stream.fileno())
    current = path.stat()

    def identity(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    if identity(before) != identity(after) or identity(after) != identity(current):
        raise ValueError("Input changed while hashing")
    return result.hexdigest()


def inside(root, relative):
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts:
        raise ValueError("Path must be relative to the workspace")
    return files.inside(root, rel)


def read_glb(path, *, static_review=True):
    raw = files.read_bytes(path, LIMIT)
    if len(raw) < 20:
        raise ValueError("Invalid GLB size")
    magic, version, length = struct.unpack_from("<4sII", raw)
    if magic != b"glTF" or version != 2 or length != len(raw):
        raise ValueError("Invalid GLB header")
    n, kind = struct.unpack_from("<I4s", raw, 12)
    if kind != b"JSON" or n % 4 or n + 20 > len(raw):
        raise ValueError("Invalid GLB JSON chunk")
    document = json.loads(raw[20 : 20 + n])
    if static_review and (document.get("skins") or document.get("animations")):
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
    if (static_review and not images) or any(
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
    journal.write_json(path, value)


def prepare(root, source, directory, view="three-quarter", pose=None):
    if view not in ("front", "three-quarter", "side"):
        raise ValueError("Unsupported view")
    src, dest = inside(root, source), inside(root, directory)
    if not src.is_file() or src.suffix.lower() != ".glb":
        raise ValueError("Source must be a GLB file")
    if pose is not None:
        from .pose import Pose

        pose = Pose.model_validate(pose).model_dump()
    document = read_glb(src, static_review=pose is None)
    if pose is not None and not document.get("animations"):
        raise ValueError("Pose review requires an animated GLB")
    source_sha256 = digest(src, LIMIT)
    dest.mkdir(parents=False, exist_ok=False)
    receipt = {
        "version": 1,
        "kind": "godot-asset-review",
        "source": source,
        "source_sha256": source_sha256,
        "view": view,
        "state": "prepared",
    }
    if pose is not None:
        receipt["pose"] = pose
    owner = {
        "kind": receipt["kind"],
        "source": source,
        "source_sha256": receipt["source_sha256"],
        "view": view,
    }
    if pose is not None:
        owner["pose"] = pose
    try:
        atomic_json(dest / "owner.json", owner)
        atomic_json(dest / "review.json", receipt)
    except Exception as exc:
        raise OperationUnknown(
            "Preparation did not complete; inspect the output directory"
        ) from exc
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
    source_hash = digest(inside(root, receipt["source"]), LIMIT)
    if source_hash != receipt["source_sha256"]:
        raise ValueError("Source changed")
    return {
        "source_sha256": source_hash,
        "view": receipt["view"],
        **({"pose": receipt["pose"]} if "pose" in receipt else {}),
        "provider": provider_identity(),
        "tools": {name: digest(path) for name, path in tools.items()},
    }


def inspect(root, directory, include_status=True):
    dest = inside(root, directory)
    receipt = files.load_json(dest / "review.json")
    if not isinstance(receipt, dict):
        raise ValueError("Invalid review receipt")  # noqa: TRY004 - malformed input
    if receipt.get("kind") != "godot-asset-review" or receipt.get("version") != 1:
        raise ValueError("Invalid review ownership marker")
    if (
        not isinstance(receipt.get("source"), str)
        or not isinstance(receipt.get("source_sha256"), str)
        or receipt.get("view") not in ("front", "three-quarter", "side")
        or receipt.get("state") not in ("prepared", "captured")
        or not isinstance(receipt.get("artifacts", {}), dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in receipt.get("artifacts", {}).items()
        )
    ):
        raise ValueError("Invalid review receipt")
    owner = files.load_json(dest / "owner.json")
    if not isinstance(owner, dict) or not all(
        key in receipt for key in ("kind", "source", "source_sha256", "view")
    ):
        raise ValueError("Invalid review ownership marker")
    if "pose" in receipt:
        from .pose import Pose

        Pose.model_validate(receipt["pose"])
    if owner != {
        key: receipt[key]
        for key in (
            "kind",
            "source",
            "source_sha256",
            "view",
            *(["pose"] if "pose" in receipt else []),
        )
    }:
        raise ValueError("Review ownership marker changed")
    if digest(inside(root, receipt["source"]), LIMIT) != receipt["source_sha256"]:
        raise ValueError("Source changed")
    if receipt.get("state") == "captured":
        inputs = receipt.get("inputs")
        names = {
            "CAVI_GODOT_BIN",
            "CAVI_GODOT_WEB_TEMPLATE",
            "CAVI_CHROMIUM_BIN",
            "CAVI_NODE_BIN",
        }
        if (
            not isinstance(inputs, dict)
            or not isinstance(inputs.get("tools"), dict)
            or set(inputs["tools"]) != names
        ):
            raise ValueError("Invalid review inputs")
        tools = {
            name: Path(os.environ[name]).resolve(strict=True)
            for name in receipt["inputs"]["tools"]
        }
        if input_snapshot(root, receipt, tools) != receipt["inputs"]:
            raise ValueError("Review input changed")
    for relative, expected in receipt.get("artifacts", {}).items():
        path = (dest / relative).resolve()
        if not path.is_relative_to(dest) or digest(path, ARTIFACT_LIMIT) != expected:
            raise ValueError("Review artifact changed")
    if receipt.get("state") == "captured" and "pose" in receipt:
        bundle = receipt.get("bundle")
        if not isinstance(bundle, str):
            raise ValueError("Missing pose review bundle")
        metadata = (dest / bundle / "capture.png.json").resolve()
        if not metadata.is_relative_to(dest) or str(
            metadata.relative_to(dest)
        ) not in receipt.get("artifacts", {}):
            raise ValueError("Missing captured pose evidence")
        scene = files.load_json(metadata)
        if not isinstance(scene, dict) or scene != receipt.get("scene"):
            raise ValueError("Captured pose metadata changed")
        validate_pose_scene(receipt["pose"], scene.get("pose"))
        if (
            not isinstance(scene.get("godot_version"), dict)
            or type(scene["godot_version"].get("major")) is not int
        ):
            raise ValueError("Missing rendered Godot version")
    if include_status:
        receipt = dict(receipt)
        receipt["cleanup_pending"] = (dest / ".capture.pending").exists()
        attempts = dest / "attempts"
        entries = (
            [p for p in attempts.iterdir() if p.is_dir()] if attempts.exists() else []
        )
        receipt["latest_attempt"] = (
            max(entries, key=lambda p: p.stat().st_mtime_ns).name if entries else None
        )
    return receipt


def run_owned(command, timeout, evidence=None, label="process"):
    import signal
    import subprocess
    import time
    from datetime import datetime

    observations = []

    def group_members(group):
        result = subprocess.run(
            owned_exec.command(["ps", "-axo", "pid=,pgid=,uid=,stat="], env=os.environ),
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
        cleanup_error = None
        try:
            with out_path.open("wb") as stdout, err_path.open("wb") as stderr:
                actual = owned_exec.command(command, env=os.environ)
                process = subprocess.Popen(
                    actual,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                    env=dict(os.environ),
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
                            raise RuntimeError(
                                "Process group contains an unowned process"
                            )
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
        except BaseException as evidence_error:
            if cleanup_error is not None:
                raise CleanupUnverified(
                    f"Process cleanup failed: {cleanup_error}; evidence failed: {evidence_error}"
                ) from cleanup_error
            raise
        try:
            out, err = out_path.read_bytes(), err_path.read_bytes()
            if evidence is not None:
                atomic_json(
                    output_dir / (label + ".groups.json"),
                    {"observations": observations},
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
        except BaseException as evidence_error:
            if cleanup_error is not None:
                raise CleanupUnverified(
                    f"Process cleanup failed: {cleanup_error}; evidence failed: {evidence_error}"
                ) from cleanup_error
            raise
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


def run_with_browser(command, profile, task, evidence):
    """Own a registered browser group while a bounded CDP client uses it."""
    import signal
    import subprocess
    import threading
    import time
    from datetime import datetime

    actual = owned_exec.command(command, env=os.environ)
    started = datetime.now(UTC).isoformat()
    known, sampling_errors = {}, []
    identity = None
    execution_error = None
    cleanup_error = None
    stop = threading.Event()
    try:
        with (
            (evidence / "browser-process.stdout").open("wb") as stdout,
            (evidence / "browser-process.stderr").open("wb") as stderr,
        ):
            process = subprocess.Popen(
                actual,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                env=dict(os.environ),
            )
            sampler = None
            try:
                identity = processes.inspect(process.pid)
                if (
                    identity is None
                    or identity["pid"] != process.pid
                    or identity["pgid"] != process.pid
                ):
                    raise CleanupUnverified("Browser identity could not be established")
                known[process.pid] = identity
                groups = {process.pid: identity["birth"]}

                def sample():
                    while not stop.wait(0.1):
                        try:
                            known.update(processes.members(groups, known))
                        except BaseException as exc:  # noqa: BLE001 - retain uncertain cleanup
                            sampling_errors.append(repr(exc))
                            stop.set()

                sampler = threading.Thread(target=sample, daemon=True)
                sampler.start()
                active = profile / "DevToolsActivePort"
                deadline = time.monotonic() + 15
                while not active.is_file():
                    if (
                        os.waitid(
                            os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT
                        )
                        or sampling_errors
                    ):
                        raise RuntimeError(
                            "Browser exited or ownership sampling failed"
                        )
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Browser startup timed out")
                    time.sleep(0.05)
                port = int(active.read_text().splitlines()[0])
                task(port)
            except BaseException as exc:  # noqa: BLE001 - cleanup must run on interruption
                execution_error = exc
            finally:
                stop.set()
                if sampler is not None:
                    sampler.join()
                try:
                    if identity is None:
                        # The unreaped leader pins this process group even without a census.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        raise CleanupUnverified(
                            "Browser identity could not be established"
                        )
                    processes.cleanup({process.pid: identity["birth"]}, known=known)
                    if sampling_errors:
                        raise CleanupUnverified(
                            "Browser ownership sampling failed: "
                            + "; ".join(sampling_errors)
                        )
                except BaseException as exc:  # noqa: BLE001 - preserve failed ownership proof
                    cleanup_error = exc
                    try:
                        if (
                            identity is not None
                            and processes.birth(process.pid) == identity["birth"]
                            and os.getpgid(process.pid) == process.pid
                        ):
                            os.killpg(process.pid, signal.SIGKILL)
                    except OSError, ValueError, subprocess.SubprocessError:
                        pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired as exc:
                    cleanup_error = cleanup_error or exc
    except BaseException as evidence_error:
        if cleanup_error is not None:
            raise CleanupUnverified(
                f"Browser cleanup failed: {cleanup_error}; evidence failed: {evidence_error}"
            ) from cleanup_error
        raise
    try:
        atomic_json(
            evidence / "browser-process.json",
            {
                "command": command,
                "actual_command": actual,
                "started": started,
                "finished": datetime.now(UTC).isoformat(),
                "pid": process.pid,
                "identity": identity,
                "known": list(known.values()),
                "sampling_errors": sampling_errors,
                "returncode": process.returncode,
                "execution_error": repr(execution_error) if execution_error else None,
                "cleanup_error": repr(cleanup_error) if cleanup_error else None,
            },
        )
    except BaseException as evidence_error:
        if cleanup_error is not None:
            raise CleanupUnverified(
                f"Browser cleanup failed: {cleanup_error}; evidence failed: {evidence_error}"
            ) from cleanup_error
        raise
    if cleanup_error is not None:
        raise CleanupUnverified(
            "Browser cleanup could not be verified: " + str(cleanup_error)
        ) from cleanup_error
    if execution_error is not None:
        raise execution_error


def share_runtime(root, wasm):
    cache = inside(root, ".godot-review-runtime")
    cache.mkdir(exist_ok=True)
    fingerprint = digest(wasm, ARTIFACT_LIMIT)
    target = cache / (fingerprint + ".wasm")
    try:
        os.link(wasm, target)
    except FileExistsError:
        if not target.is_file() or digest(target, ARTIFACT_LIMIT) != fingerprint:
            raise ValueError("Shared runtime cache changed")
    replacement = wasm.with_suffix(".shared")
    os.link(target, replacement)
    os.replace(replacement, wasm)


def capture(root, directory):
    import fcntl

    dest = inside(root, directory)
    inspect(root, directory, include_status=False)
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
        required = (
            "CAVI_GODOT_BIN",
            "CAVI_GODOT_WEB_TEMPLATE",
            "CAVI_CHROMIUM_BIN",
            "CAVI_NODE_BIN",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ValueError("Missing review configuration: " + ", ".join(missing))
        atomic_json(marker, {"kind": "capture_pending"})
        verified = False
        try:
            result = capture_locked(root, directory)
            verified = True
            return result
        except CleanupUnverified:
            raise
        except Exception as exc:
            verified = True
            raise OperationUnknown(str(exc)) from exc
        finally:
            if verified:
                marker.unlink()
    finally:
        os.close(fd)


def validate_pose_scene(selection, scene):
    from .pose import validate_scene

    validate_scene(selection, scene)


def capture_locked(root, directory):
    import http.server
    import shutil
    import socketserver
    import threading

    receipt = inspect(root, directory, include_status=False)
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
            **({"pose": receipt["pose"]} if "pose" in receipt else {}),
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
            script.read_text()
            .replace(
                "VIEW_YAW",
                {"front": "0.0", "three-quarter": "0.65", "side": "1.5708"}[
                    receipt["view"]
                ],
            )
            .replace("POSE_ENABLED", "true" if "pose" in receipt else "false")
        )
        if "pose" in receipt:
            atomic_json(project / "pose.json", receipt["pose"])
            pose_hash = digest(project / "pose.json")
            if files.load_json(project / "pose.json") != receipt["pose"]:
                raise ValueError("Copied pose configuration changed")
        shutil.copyfile(inside(root, receipt["source"]), project / "subject.glb")
        if digest(project / "subject.glb", LIMIT) != inputs["source_sha256"]:
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
            .replace("POSE_ENABLED", "true" if "pose" in receipt else "false")
        )
        if (project / "main.gd").read_text() != expected_script or digest(
            runtime / "main.gd"
        ) != inputs["provider"]["runtime"]["main.gd"]:
            raise ValueError("Copied runtime changed")
        export = project / "bundle"
        export.mkdir()
        preset = (
            '[preset.0]\nname="Web"\nplatform="Web"\nrunnable=true\n'
            'export_filter="all_resources"\ninclude_filter='
            + ('"pose.json"' if "pose" in receipt else '""')
            + '\nexclude_filter=""\n'
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
                profile = Path(temp) / "browser"
                command = [
                    str(tools["CAVI_CHROMIUM_BIN"]),
                    "--headless",
                    "--use-angle=swiftshader",
                    "--enable-unsafe-swiftshader",
                    "--remote-debugging-port=0",
                    "--user-data-dir=" + str(profile),
                    "--window-size=1024,768",
                    "about:blank",
                ]

                def client(port):
                    run_owned(
                        [
                            str(tools["CAVI_NODE_BIN"]),
                            str(runtime / "capture.mjs"),
                            str(port),
                            url,
                            str(export / "capture.png"),
                        ],
                        90,
                        evidence,
                        "browser",
                    )

                run_with_browser(command, profile, client, evidence)
            finally:
                server.shutdown()
                thread.join()
        godot_version = run_owned([godot, "--version"], 10, evidence, "version").strip()
        if (
            digest(project / "subject.glb", LIMIT) != inputs["source_sha256"]
            or (project / "main.gd").read_text() != expected_script
        ):
            raise ValueError("Copied project changed during capture")
        if "pose" in receipt and (
            digest(project / "pose.json") != pose_hash
            or files.load_json(project / "pose.json") != receipt["pose"]
        ):
            raise ValueError("Pose configuration changed during capture")
        inspect(root, directory, include_status=False)
        if input_snapshot(root, receipt, tools) != inputs:
            raise ValueError("Review input changed during capture")
        scene = files.load_json(export / "capture.png.json")
        if not isinstance(scene, dict):
            raise ValueError("Invalid captured scene metadata")  # noqa: TRY004
        if "pose" in receipt:
            validate_pose_scene(receipt["pose"], scene.get("pose"))
            if (
                not isinstance(scene.get("godot_version"), dict)
                or type(scene["godot_version"].get("major")) is not int
            ):
                raise ValueError("Missing rendered Godot version")
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
        receipt["scene"] = scene
        if "pose" in receipt:
            receipt["project"]["pose.json"] = pose_hash
        receipt["artifacts"] = {
            str((bundle / path.relative_to(export)).relative_to(dest)): digest(
                path, ARTIFACT_LIMIT
            )
            for path in export.rglob("*")
            if path.is_file()
        }
        for filename in (
            "project.godot",
            "main.gd",
            "main.tscn",
            "export_presets.cfg",
            *(["pose.json"] if "pose" in receipt else []),
        ):
            shutil.copyfile(project / filename, evidence / filename)
        receipt["artifacts"].update(
            {
                str(path.relative_to(dest)): digest(path, ARTIFACT_LIMIT)
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
