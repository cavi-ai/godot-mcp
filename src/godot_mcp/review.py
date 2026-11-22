"""Owned, source-bound review requests for static GLB assets."""

import hashlib
import http.server
import json
import math
import os
import re
import shutil
import signal
import socketserver
import stat
import struct
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

from cavi_mcp_core import files, journal, owned_exec, processes, workspace
from cavi_mcp_core.contracts import OperationUnknown
from pydantic import BaseModel, ConfigDict, Field

from . import config
from .pose import Pose, validate_scene
from .shapes import number, vector

LIMIT = 64 * 1024 * 1024
ARTIFACT_LIMIT = 256 * 1024 * 1024
PLAIN_CLIP = re.compile(r"[A-Za-z0-9 _-]+")
CLIP_TOLERANCE = 1e-4

Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class Framing(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    center: Annotated[list[Finite], Field(min_length=3, max_length=3)]
    distance: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]


def validate_frame(observed, requested=None):
    frame = Framing.model_validate(observed).model_dump()
    if requested is not None:
        expected = Framing.model_validate(requested).model_dump()
        actual_values = [*frame["center"], frame["distance"]]
        expected_values = [*expected["center"], expected["distance"]]
        if any(
            not math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-5)
            for a, b in zip(actual_values, expected_values, strict=True)
        ):
            raise ValueError("Rendered camera frame differs from requested framing")
    return frame


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


def relative_path(value):
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Path must be relative to the workspace")
    return value


def inside(root, value):
    return files.inside(root, relative_path(value))


def source_glb(root, source):
    return workspace.input_file(root, relative_path(source), {".glb"})


def new_output(root, directory):
    """A new output directory inside the workspace, validated but not yet created."""
    return workspace.output_path(root, relative_path(directory))


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
    document = files.parse_json(raw[20 : 20 + n])
    if not isinstance(document, dict):
        raise ValueError("Invalid GLB JSON chunk")  # noqa: TRY004 - malformed input
    if static_review and (document.get("skins") or document.get("animations")):
        raise ValueError("Only static GLB assets are supported")
    if not document.get("meshes") or not document.get("nodes"):
        raise ValueError("GLB needs mesh nodes")
    buffers = entries(document, "buffers")
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
    views = entries(document, "bufferViews")
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
    images = entries(document, "images")
    if any(
        "uri" in image
        or type(image.get("bufferView")) is not int
        or not 0 <= image["bufferView"] < len(views)
        or image.get("mimeType") not in ("image/png", "image/jpeg")
        for image in images
    ):
        raise ValueError("Images must be embedded PNG or JPEG")
    textures = entries(document, "textures")
    for texture in textures:
        if type(texture.get("source")) is not int or not 0 <= texture["source"] < len(
            images
        ):
            raise ValueError("Texture must reference an embedded image")
    texture_count = len(textures)
    for material in entries(document, "materials"):
        pbr = material.get("pbrMetallicRoughness", {})
        if not isinstance(pbr, dict):
            raise ValueError("Invalid material")  # noqa: TRY004 - malformed input
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


def entries(document, key):
    value = document.get(key, [])
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        raise ValueError(f"GLB {key} must be a list of objects")
    return value


def member(values, index):
    if (
        isinstance(values, list)
        and type(index) is int
        and 0 <= index < len(values)
        and isinstance(values[index], dict)
    ):
        return values[index]
    return None


def clip_lengths(document):
    """Each animation's name and length (its latest sampler input), or None."""
    clips = []
    for animation in entries(document, "animations"):
        channels, times = animation.get("channels"), []
        for channel in channels if isinstance(channels, list) else []:
            sampler = member(
                animation.get("samplers"),
                channel.get("sampler") if isinstance(channel, dict) else None,
            )
            accessor = member(
                document.get("accessors"), sampler.get("input") if sampler else None
            )
            bound = accessor.get("max") if accessor else None
            if not isinstance(bound, list) or len(bound) != 1 or not number(bound[0]):
                times = []
                break
            times.append(bound[0])
        name = animation.get("name")
        clips.append(
            (name if isinstance(name, str) else None, max(times, default=None))
        )
    return clips


def select_clip(document, animation, seconds=None):
    """Refuse a clip, time or length Godot is certain to refuse.

    Godot renames clips with punctuation, loop or cycle suffixes or duplicate names,
    so names and per-clip lengths are checked only when every clip keeps its name.
    """
    clips = clip_lengths(document)
    if not clips:
        raise ValueError("The GLB has no animations")
    names = [name for name, _ in clips]
    kept = len(set(names)) == len(names) and all(
        name is not None
        and PLAIN_CLIP.fullmatch(name)
        and "loop" not in name.lower()
        and "cycle" not in name.lower()
        for name in names
    )
    if kept and animation not in names:
        raise ValueError("Animation not found in the GLB: " + animation)
    lengths = [length for name, length in clips if not kept or name == animation]
    length = None if None in lengths else max(lengths)
    if length is None:
        return
    if seconds is not None and seconds > length + CLIP_TOLERANCE:
        raise ValueError("Pose time is after the end of the animation")
    if kept and length <= 0:
        raise ValueError("Animation duration must be positive")


def geometry_only(document):
    """Identify unadorned triangle geometry without altering the source."""
    if not isinstance(document, dict) or any(
        document.get(key)
        for key in (
            "images",
            "textures",
            "materials",
            "skins",
            "animations",
            "extensionsUsed",
            "extensionsRequired",
            "extensions",
        )
    ):
        return False
    meshes, accessors, nodes = (
        document.get(key) for key in ("meshes", "accessors", "nodes")
    )
    if (
        not isinstance(meshes, list)
        or not meshes
        or not isinstance(accessors, list)
        or not isinstance(nodes, list)
        or any(not isinstance(node, dict) or node.get("extensions") for node in nodes)
    ):
        return False

    def valid_accessor(index, kind):
        return (
            type(index) is int
            and 0 <= index < len(accessors)
            and isinstance(accessors[index], dict)
            and accessors[index].get("type") == kind
            and not accessors[index].get("extensions")
        )

    for mesh in meshes:
        if (
            not isinstance(mesh, dict)
            or mesh.get("extensions")
            or not isinstance(mesh.get("primitives"), list)
            or not mesh["primitives"]
        ):
            return False
        for primitive in mesh["primitives"]:
            if not isinstance(primitive, dict):
                return False
            attributes = primitive.get("attributes")
            if (
                not isinstance(attributes, dict)
                or set(attributes) != {"POSITION"}
                or not valid_accessor(attributes["POSITION"], "VEC3")
                or type(primitive.get("mode", 4)) is not int
                or primitive.get("mode", 4) != 4
                or any(
                    key in primitive for key in ("material", "targets", "extensions")
                )
                or (
                    "indices" in primitive
                    and not valid_accessor(primitive["indices"], "SCALAR")
                )
            ):
                return False
    return True


def validate_geometry_preview(scene, expected):
    count = scene.get("geometry_preview_surfaces")
    if (
        type(count) is not int
        or (expected and (count < 1 or count != scene.get("material_surfaces")))
        or (not expected and count != 0)
    ):
        raise ValueError("Rendered geometry preview does not match source")


def atomic_json(path, value):
    journal.write_json(path, value)


def prepare(
    root,
    source,
    directory,
    view="three-quarter",
    pose=None,
    all_views=False,
    framing=None,
    lighting="studio",
):
    if lighting not in ("studio", "clay", "sun"):
        raise ValueError("Unsupported lighting")
    if framing is not None:
        if pose is not None:
            raise ValueError("Camera framing is only supported for static reviews")
        framing = Framing.model_validate(framing).model_dump()
    if type(all_views) is not bool:
        raise ValueError("all_views must be a boolean")
    if all_views and pose is not None:
        raise ValueError("Multiple views are only supported for static reviews")
    if view not in ("front", "three-quarter", "side"):
        raise ValueError("Unsupported view")
    src, dest = source_glb(root, source), new_output(root, directory)
    if pose is not None:
        pose = Pose.model_validate(pose).model_dump()
    document = read_glb(src, static_review=pose is None)
    if pose is not None and not document.get("animations"):
        raise ValueError("Pose review requires an animated GLB")
    if pose is not None:
        select_clip(document, pose["animation"], pose["time_seconds"])
    source_sha256 = digest(src, LIMIT)
    dest.mkdir()
    chosen = {"camera_frame_evidence": True} if pose is None else {"pose": pose}
    if all_views:
        chosen["all_views"] = True
    if framing is not None:
        chosen["framing"] = framing
    owner = {
        "kind": "godot-asset-review",
        "source": source,
        "source_sha256": source_sha256,
        "view": view,
        "lighting": lighting,
        **chosen,
    }
    receipt = {"version": 1, **owner, "state": "prepared"}
    try:
        atomic_json(dest / "owner.json", owner)
        atomic_json(dest / "review.json", receipt)
    except Exception as exc:
        raise OperationUnknown(
            "Preparation did not complete; inspect the output directory"
        ) from exc
    return receipt


def provider_identity():
    package = Path(__file__).parent
    return {
        "version": package_version("cavi-godot-mcp"),
        "source": {p.name: digest(p) for p in package.glob("*.py")},
        "runtime": {
            p.name: digest(p) for p in (package / "runtime").iterdir() if p.is_file()
        },
    }


SELECTION = ("pose", "framing", "camera_frame_evidence", "all_views", "lighting")
OWNER = ("kind", "source", "source_sha256", "view")


def selection(receipt):
    """The review choices a receipt carries, as recorded at preparation."""
    return {key: receipt[key] for key in SELECTION if key in receipt}


def input_snapshot(root, receipt, tools):
    source_hash = digest(inside(root, receipt["source"]), LIMIT)
    if source_hash != receipt["source_sha256"]:
        raise ValueError("Source changed")
    return {
        "source_sha256": source_hash,
        "view": receipt["view"],
        **selection(receipt),
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
        or (
            "lighting" in receipt
            and receipt["lighting"] not in ("studio", "clay", "sun")
        )
        or receipt.get("state") not in ("prepared", "captured")
        or not isinstance(receipt.get("artifacts", {}), dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in receipt.get("artifacts", {}).items()
        )
    ):
        raise ValueError("Invalid review receipt")
    if "all_views" in receipt and (
        receipt["all_views"] is not True or "pose" in receipt
    ):
        raise ValueError("Invalid multiple-view review")
    if "camera_frame_evidence" in receipt and (
        receipt["camera_frame_evidence"] is not True or "pose" in receipt
    ):
        raise ValueError("Invalid camera frame evidence")
    if "framing" in receipt:
        if "pose" in receipt:
            raise ValueError("Invalid camera framing for pose review")
        Framing.model_validate(receipt["framing"])
    owner = files.load_json(dest / "owner.json")
    if not isinstance(owner, dict) or not all(key in receipt for key in OWNER):
        raise ValueError("Invalid review ownership marker")
    if "pose" in receipt:
        Pose.model_validate(receipt["pose"])
    if owner != {key: receipt[key] for key in OWNER} | selection(receipt):
        raise ValueError("Review ownership marker changed")
    if digest(inside(root, receipt["source"]), LIMIT) != receipt["source_sha256"]:
        raise ValueError("Source changed")
    if receipt.get("state") == "captured":
        inputs = receipt.get("inputs")
        if (
            not isinstance(inputs, dict)
            or not isinstance(inputs.get("tools"), dict)
            or set(inputs["tools"]) != set(config.REVIEW_TOOLS)
        ):
            raise ValueError("Invalid review inputs")
        if input_snapshot(root, receipt, config.tools()) != receipt["inputs"]:
            raise ValueError("Review input changed")
    for relative, expected in receipt.get("artifacts", {}).items():
        path = (dest / relative).resolve()
        if not path.is_relative_to(dest) or digest(path, ARTIFACT_LIMIT) != expected:
            raise ValueError("Review artifact changed")
    if receipt.get("state") == "captured" and receipt.get("all_views"):
        validate_view_captures(dest, receipt)
    if receipt.get("state") == "captured" and (
        receipt.get("camera_frame_evidence") is True
        or "framing" in receipt
        or (
            isinstance(receipt.get("scene"), dict)
            and "camera_frame" in receipt["scene"]
        )
    ):
        validate_camera_evidence(dest, receipt)
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
        validate_scene(receipt["pose"], scene.get("pose"))
        if (
            not isinstance(scene.get("godot_version"), dict)
            or type(scene["godot_version"].get("major")) is not int
        ):
            raise ValueError("Missing rendered Godot version")
    if include_status:
        receipt = dict(receipt)
        marker = dest / ".capture.pending"
        receipt["cleanup_pending"] = False
        if marker.exists() or marker.is_symlink():
            try:
                check_capture_marker(marker, directory, receipt)
            except CleanupUnverified:
                receipt["cleanup_pending"] = True
        attempts = dest / "attempts"
        entries = (
            [p for p in attempts.iterdir() if p.is_dir()] if attempts.exists() else []
        )
        receipt["latest_attempt"] = (
            max(entries, key=lambda p: p.stat().st_mtime_ns).name if entries else None
        )
    return receipt


def validate_camera_evidence(dest, receipt):
    bundle = receipt.get("bundle")
    if not isinstance(bundle, str):
        raise ValueError("Missing camera review bundle")  # noqa: TRY004
    metadata = (dest / bundle / "capture.png.json").resolve()
    if not metadata.is_relative_to(dest) or str(
        metadata.relative_to(dest)
    ) not in receipt.get("artifacts", {}):
        raise ValueError("Missing camera frame evidence")
    scene = files.load_json(metadata)
    if not isinstance(scene, dict) or scene != receipt.get("scene"):
        raise ValueError("Captured camera metadata changed")
    validate_frame(scene.get("camera_frame"), receipt.get("framing"))


VIEWS = ("front", "three-quarter", "side")


def view_filename(view, primary):
    return "capture.png" if view == primary else f"capture-{view}.png"


def validate_view_files(bundle, primary):
    positions = set()
    for view in VIEWS:
        image = bundle / view_filename(view, primary)
        if not image.is_file():
            raise ValueError("Missing view capture artifact")
        metadata = files.load_json(Path(str(image) + ".view.json"))
        if not isinstance(metadata, dict) or metadata.get("view") != view:
            raise ValueError("Invalid view capture metadata")
        camera = metadata.get("camera_position")
        if not vector(camera):
            raise ValueError("Invalid view camera position")
        positions.add(tuple(camera))
    if len(positions) != len(VIEWS):
        raise ValueError("View camera positions must differ")


def validate_view_captures(dest, receipt):
    bundle = receipt.get("bundle")
    captures = receipt.get("view_captures")
    if (
        not isinstance(bundle, str)
        or not isinstance(captures, dict)
        or set(captures) != set(VIEWS)
    ):
        raise ValueError("Missing view captures")
    for view in VIEWS:
        image_path = str(Path(bundle) / view_filename(view, receipt["view"]))
        metadata_path = image_path + ".view.json"
        if captures[view] != {"image": image_path, "metadata": metadata_path}:
            raise ValueError("Invalid view capture binding")
        if (
            image_path not in receipt["artifacts"]
            or metadata_path not in receipt["artifacts"]
        ):
            raise ValueError("Missing view capture artifact")
    validate_view_files(dest / bundle, receipt["view"])


def run_owned(command, timeout, evidence=None, label="process"):
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


RUNTIME_FILES = ("project.godot", "main.tscn", "main.gd", "pose.gd")
SELECTION_FILE = "selection.json"
MANIFEST = "manifest.json"


def scene_selection(receipt, fallback):
    """The view and choices the review scene reads from selection.json."""
    return {
        "view": receipt["view"],
        "pose": receipt.get("pose"),
        "framing": receipt.get("framing"),
        "all_views": receipt.get("all_views") is True,
        "geometry_preview": fallback,
        "lighting": receipt.get("lighting", "studio"),
    }


def export_preset(template):
    return (
        '[preset.0]\nname="Web"\nplatform="Web"\nrunnable=true\n'
        'export_filter="all_resources"\ninclude_filter=""\nexclude_filter=""\n'
        "[preset.0.options]\nvariant/thread_support=false\n"
        "custom_template/release=" + json.dumps(str(template)) + "\n"
    )


def runtime_export(root, tools, inputs, evidence, temp):
    """The review runtime's Web export, built once per runtime and toolchain.

    Returns the cached export directory, its key and its manifest of file
    digests. The import and export logs go to ``evidence`` when it is built.
    """
    preset = export_preset(tools["CAVI_GODOT_WEB_TEMPLATE"])
    identity = {
        # Earlier exports can still share writable files with existing bundles.
        "bundle_files": "independent",
        "runtime": {
            name: inputs["provider"]["runtime"][name] for name in RUNTIME_FILES
        },
        "preset": preset,
        "godot": inputs["tools"]["CAVI_GODOT_BIN"],
        "template": inputs["tools"]["CAVI_GODOT_WEB_TEMPLATE"],
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache = inside(root, ".godot-review-runtime")
    cache.mkdir(exist_ok=True)
    target = cache / key
    if not target.exists():
        project = Path(temp) / "runtime"
        project.mkdir()
        runtime = Path(__file__).parent / "runtime"
        for name in RUNTIME_FILES:
            shutil.copyfile(runtime / name, project / name)
            if digest(project / name) != identity["runtime"][name]:
                raise ValueError("Copied runtime changed")
        (project / "export_presets.cfg").write_text(preset)
        staged = Path(tempfile.mkdtemp(prefix=".export-", dir=cache))
        try:
            godot = str(tools["CAVI_GODOT_BIN"])
            base = [godot, "--headless", "--path", str(project)]
            imported = run_owned(
                [*base, "--editor", "--import"], 120, evidence, "import"
            )
            if (
                "ERROR:" in imported
                or b"ERROR:" in (evidence / "import.stderr").read_bytes()
            ):
                raise RuntimeError(
                    "Godot reported an import error; inspect the raw logs"
                )
            exported = run_owned(
                [*base, "--export-release", "Web", str(staged / "index.html")],
                120,
                evidence,
                "export",
            )
            if (
                "ERROR:" in exported
                or b"ERROR:" in (evidence / "export.stderr").read_bytes()
            ):
                raise RuntimeError(
                    "Godot reported an export error; inspect the raw logs"
                )
            atomic_json(
                staged / MANIFEST,
                {
                    p.name: digest(p, ARTIFACT_LIMIT)
                    for p in staged.iterdir()
                    if p.is_file()
                },
            )
            try:
                os.rename(staged, target)
            except OSError:
                if not target.is_dir():
                    raise
                shutil.rmtree(staged)  # Another capture published the same export.
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
    manifest = files.load_json(target / MANIFEST)
    if (
        not isinstance(manifest, dict)
        or not manifest
        or any(
            not isinstance(name, str)
            or "/" in name
            or name in (MANIFEST, SELECTION_FILE, "subject.glb")
            or not isinstance(value, str)
            for name, value in manifest.items()
        )
    ):
        raise ValueError("Shared runtime cache changed")
    return target, key, manifest


def capture_marker(directory, receipt, phase):
    return {
        "version": 1,
        "kind": "capture_pending",
        "directory": directory,
        "owner": {key: receipt[key] for key in OWNER} | selection(receipt),
        "phase": phase,
    }


def check_capture_marker(path, directory, receipt, phase="safe"):
    try:
        if path.is_symlink():
            raise ValueError("Marker is a symlink")
        value = files.load_json(path)
        expected = capture_marker(directory, receipt, phase)
        if (
            type(value) is not dict
            or type(value.get("version")) is not int
            or json.dumps(value, sort_keys=True, allow_nan=False)
            != json.dumps(expected, sort_keys=True, allow_nan=False)
        ):
            raise ValueError("Marker does not match this review")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CleanupUnverified("Previous capture cleanup is unverified") from exc


def native_checkpoint(marker, directory, receipt, name, operation):
    atomic_json(marker, capture_marker(directory, receipt, name))
    try:
        result = operation()
    except CleanupUnverified:
        raise
    except Exception:
        # Native helpers report uncertain cleanup separately; ordinary errors have
        # verified cleanup and can return to the safe checkpoint.
        atomic_json(marker, capture_marker(directory, receipt, "safe"))
        raise
    atomic_json(marker, capture_marker(directory, receipt, "safe"))
    return result


def capture(root, directory):
    import fcntl  # POSIX only; the rest of the server also runs where it is missing.

    dest = inside(root, directory)
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
        receipt = inspect(root, directory, include_status=False)
        marker = dest / ".capture.pending"
        if marker.exists() or marker.is_symlink():
            check_capture_marker(marker, directory, receipt)
            if receipt["state"] == "captured":
                marker.unlink()
                return receipt
        else:
            if receipt["state"] != "prepared":
                raise ValueError("Review is not prepared")
            config.tools()
            atomic_json(marker, capture_marker(directory, receipt, "safe"))
        verified = False
        try:
            result = capture_locked(root, directory)
            verified = True
            return result
        except CleanupUnverified:
            raise
        except Exception as exc:
            # A failed checkpoint write can leave native state uncertain.
            check_capture_marker(marker, directory, receipt)
            verified = True
            raise OperationUnknown(str(exc)) from exc
        finally:
            if verified:
                marker.unlink()
    finally:
        os.close(fd)


def capture_locked(root, directory):
    receipt = inspect(root, directory, include_status=False)
    dest = inside(root, directory)
    if receipt["state"] != "prepared":
        raise ValueError("Review is not prepared")
    tools = config.tools()
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
            **selection(receipt),
            "directory": directory,
        },
    )
    with tempfile.TemporaryDirectory(prefix=".godot-review-", dir=dest) as temp:
        export = Path(temp) / "bundle"
        export.mkdir()
        shutil.copyfile(inside(root, receipt["source"]), export / "subject.glb")
        if digest(export / "subject.glb", LIMIT) != inputs["source_sha256"]:
            raise ValueError("Copied source changed")
        fallback = "pose" not in receipt and geometry_only(
            read_glb(export / "subject.glb")
        )
        chosen = scene_selection(receipt, fallback)
        atomic_json(export / SELECTION_FILE, chosen)
        selection_hash = digest(export / SELECTION_FILE)
        if files.load_json(export / SELECTION_FILE) != chosen:
            raise ValueError("Copied review selection changed")
        marker = dest / ".capture.pending"
        cached, runtime_key, manifest = native_checkpoint(
            marker,
            directory,
            receipt,
            "runtime_export",
            lambda: runtime_export(root, tools, inputs, evidence, temp),
        )
        for name, expected in manifest.items():
            shutil.copyfile(cached / name, export / name)
            if digest(export / name, ARTIFACT_LIMIT) != expected:
                raise ValueError("Shared runtime cache changed")
        godot = str(tools["CAVI_GODOT_BIN"])

        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

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
                            *([receipt["view"]] if receipt.get("all_views") else []),
                        ],
                        90,
                        evidence,
                        "browser",
                    )

                native_checkpoint(
                    marker,
                    directory,
                    receipt,
                    "browser",
                    lambda: run_with_browser(command, profile, client, evidence),
                )
            finally:
                server.shutdown()
                thread.join()
        godot_version = native_checkpoint(
            marker,
            directory,
            receipt,
            "version",
            lambda: run_owned([godot, "--version"], 10, evidence, "version"),
        ).strip()
        if (
            digest(export / "subject.glb", LIMIT) != inputs["source_sha256"]
            or digest(export / SELECTION_FILE) != selection_hash
            or files.load_json(export / SELECTION_FILE) != chosen
        ):
            raise ValueError("Copied subject or selection changed during capture")
        inspect(root, directory, include_status=False)
        if input_snapshot(root, receipt, tools) != inputs:
            raise ValueError("Review input changed during capture")
        scene = files.load_json(export / "capture.png.json")
        if not isinstance(scene, dict):
            raise ValueError("Invalid captured scene metadata")  # noqa: TRY004
        if "pose" in receipt:
            validate_scene(receipt["pose"], scene.get("pose"))
            if (
                not isinstance(scene.get("godot_version"), dict)
                or type(scene["godot_version"].get("major")) is not int
            ):
                raise ValueError("Missing rendered Godot version")
        if "pose" not in receipt:
            validate_frame(scene.get("camera_frame"), receipt.get("framing"))
            validate_geometry_preview(scene, fallback)
        if receipt.get("all_views"):
            validate_view_files(export, receipt["view"])
        bundle = evidence / "bundle"
        for name in RUNTIME_FILES:
            shutil.copyfile(runtime / name, evidence / name)
            if digest(evidence / name) != inputs["provider"]["runtime"][name]:
                raise ValueError("Copied runtime changed")
        (evidence / "export_presets.cfg").write_text(
            export_preset(tools["CAVI_GODOT_WEB_TEMPLATE"])
        )
        shutil.copyfile(export / SELECTION_FILE, evidence / SELECTION_FILE)
        receipt["state"] = "captured"
        receipt["project"] = {
            **{name: digest(evidence / name) for name in RUNTIME_FILES},
            "export_presets.cfg": digest(evidence / "export_presets.cfg"),
            "runtime_export": runtime_key,
            SELECTION_FILE: selection_hash,
            "subject.glb": inputs["source_sha256"],
        }
        receipt["inputs"] = inputs
        receipt["bundle"] = str(bundle.relative_to(dest))
        receipt["godot_version"] = godot_version
        receipt["renderer"] = "godot-web-compatibility-webgl2"
        receipt["scene"] = scene
        receipt["artifacts"] = {
            str((bundle / path.relative_to(export)).relative_to(dest)): digest(
                path, ARTIFACT_LIMIT
            )
            for path in export.rglob("*")
            if path.is_file()
        }
        if receipt.get("all_views"):
            receipt["view_captures"] = {
                view: {
                    "image": str(
                        (bundle / view_filename(view, receipt["view"])).relative_to(
                            dest
                        )
                    ),
                    "metadata": str(
                        (bundle / view_filename(view, receipt["view"])).relative_to(
                            dest
                        )
                    )
                    + ".view.json",
                }
                for view in VIEWS
            }
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
            if "pose" not in receipt:
                validate_camera_evidence(dest, receipt)
            if receipt.get("all_views"):
                validate_view_captures(dest, receipt)
            atomic_json(dest / "review.json", receipt)
        except BaseException:
            shutil.rmtree(bundle)
            raise
        return receipt
