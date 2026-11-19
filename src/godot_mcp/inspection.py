"""Source-bound Godot runtime inspection of self-contained GLB files."""

import math
import shutil
import tempfile
from pathlib import Path

from cavi_mcp_core import files
from cavi_mcp_core.contracts import OperationUnknown

from . import config, review

LIMITS = {"nodes": 256, "surfaces": 256, "bones": 256, "clips": 128}
TRIANGLES = 3


def validate_glb(path):
    return review.read_glb(path, static_review=False)


def count(value):
    return type(value) is int and value >= 0


def vector(value):
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(type(n) in (int, float) and math.isfinite(n) for n in value)
    )


def transform(value):
    return (
        isinstance(value, dict)
        and set(value) == {"origin", "basis"}
        and vector(value["origin"])
        and isinstance(value["basis"], list)
        and len(value["basis"]) == 3
        and all(vector(v) for v in value["basis"])
    )


def bounds(value):
    return (
        isinstance(value, dict)
        and set(value) == {"position", "size"}
        and vector(value["position"])
        and vector(value["size"])
    )


def material(value):
    return value is None or (
        isinstance(value, dict)
        and isinstance(value.get("name"), str)
        and isinstance(value.get("type"), str)
        and isinstance(value.get("textures"), dict)
        and all(
            isinstance(texture, dict)
            and isinstance(texture.get("name"), str)
            and count(texture.get("width"))
            and count(texture.get("height"))
            for texture in value["textures"].values()
        )
    )


def validate_report(report):
    """The inspect.gd report, refused when its shape, counts or references differ."""
    if not isinstance(report, dict) or report.get("status") != "ok":
        raise ValueError("Invalid Godot inspection report")
    totals, truncated = report.get("totals"), report.get("truncated")
    if (
        not isinstance(report.get("godot_version"), dict)
        or report.get("bounds_semantics") != "imported rest pose"
        or not isinstance(totals, dict)
        or set(totals) != {*LIMITS, "unsupported_primitives"}
        or not all(count(v) for v in totals.values())
        or totals["unsupported_primitives"] > totals["surfaces"]
        or not isinstance(truncated, dict)
        or set(truncated) != set(LIMITS)
        or any(
            not isinstance(report.get(key), list)
            or len(report[key]) != min(totals[key], limit)
            or truncated[key] is not (totals[key] > limit)
            for key, limit in LIMITS.items()
        )
    ):
        raise ValueError("Inspection report totals or limits differ")
    nodes = report["nodes"]
    if any(
        not isinstance(n, dict)
        or not isinstance(n.get("path"), str)
        or not n["path"].startswith("/")
        or not isinstance(n.get("parent"), str)
        or not isinstance(n.get("type"), str)
        or not n["type"]
        or any(
            key in n and not transform(n[key])
            for key in ("local_transform", "world_transform")
        )
        or any(
            key in n and not bounds(n[key])
            for key in ("mesh_rest_local_bounds", "mesh_rest_world_bounds")
        )
        for n in nodes
    ) or len({n["path"] for n in nodes}) != len(nodes):
        raise ValueError("Inspection node inventory is invalid")
    paths = None if truncated["nodes"] else {n["path"] for n in nodes}

    def known(path):
        return isinstance(path, str) and (paths is None or path in paths)

    surfaces = report["surfaces"]
    if any(
        not isinstance(s, dict)
        or not known(s.get("node"))
        or not count(s.get("surface"))
        or not count(s.get("primitive"))
        or not count(s.get("vertices"))
        or "triangles" not in s
        or not (
            count(s["triangles"])
            if s["primitive"] == TRIANGLES
            else s["triangles"] is None
        )
        or type(s.get("uv0")) is not bool
        or type(s.get("uv1")) is not bool
        or not material(s.get("material"))
        for s in surfaces
    ) or len({(s["node"], s["surface"]) for s in surfaces}) != len(surfaces):
        raise ValueError("Inspection surface inventory is invalid")
    bones = report["bones"]
    if any(
        not isinstance(b, dict)
        or not known(b.get("skeleton"))
        or not count(b.get("index"))
        or not isinstance(b.get("name"), str)
        or not b["name"]
        or type(b.get("parent")) is not int
        or b["parent"] < -1
        or b["parent"] == b["index"]
        or not transform(b.get("rest_transform"))
        for b in bones
    ) or len({(b["skeleton"], b["index"]) for b in bones}) != len(bones):
        raise ValueError("Inspection bone inventory is invalid")
    clips = report["clips"]
    if any(
        not isinstance(c, dict)
        or not known(c.get("player"))
        or not isinstance(c.get("name"), str)
        or not c["name"]
        or type(c.get("duration")) not in (int, float)
        or not math.isfinite(c["duration"])
        or c["duration"] < 0
        or not count(c.get("tracks"))
        for c in clips
    ) or len({(c["player"], c["name"]) for c in clips}) != len(clips):
        raise ValueError("Inspection clip inventory is invalid")
    return report


def inspect_asset(root, source, directory):
    src = review.inside(root, source)
    dest = review.inside(root, directory)
    if not src.is_file() or src.suffix.lower() != ".glb":
        raise ValueError("Source must be a GLB file")
    validate_glb(src)
    engine = config.tools((config.GODOT,))[config.GODOT]
    runtime = Path(__file__).parent / "runtime" / "inspect.gd"
    snapshot = {
        "source_sha256": review.digest(src, review.LIMIT),
        "engine_sha256": review.digest(engine),
        "provider": review.provider_identity(),
    }
    dest.mkdir(parents=False, exist_ok=False)
    pending = dest / ".inspection.pending"
    project = None
    try:
        review.atomic_json(
            dest / "owner.json", {"kind": "godot-asset-inspection", "source": source}
        )
        review.atomic_json(pending, {"kind": "inspection_pending"})
        review.atomic_json(dest / "inputs.json", {"source": source, "inputs": snapshot})
        project = Path(tempfile.mkdtemp(prefix=".inspection-", dir=dest))
        (project / "project.godot").write_text(
            '[application]\nconfig/name="Inspection"\n'
        )
        shutil.copyfile(src, project / "subject.glb")
        shutil.copyfile(runtime, project / "inspect.gd")
        validate_glb(project / "subject.glb")
        if (
            review.digest(project / "subject.glb", review.LIMIT)
            != snapshot["source_sha256"]
            or review.digest(project / "inspect.gd")
            != snapshot["provider"]["runtime"]["inspect.gd"]
        ):
            raise ValueError("Inspection inputs changed while copying")
        review.run_owned(
            [
                str(engine),
                "--headless",
                "--path",
                str(project),
                "--script",
                "res://inspect.gd",
            ],
            120,
            dest,
            "godot",
        )
        if (
            b"ERROR:" in (dest / "godot.stderr").read_bytes()
            or b"ERROR:" in (dest / "godot.stdout").read_bytes()
        ):
            raise ValueError("Godot reported an inspection error")
        report = validate_report(files.load_json(project / "report.json"))
        if (
            review.digest(src, review.LIMIT) != snapshot["source_sha256"]
            or review.digest(engine) != snapshot["engine_sha256"]
            or review.provider_identity() != snapshot["provider"]
        ):
            raise ValueError("Inspection inputs changed")
        review.atomic_json(dest / "report.json", report)
        result = {
            "kind": "godot-asset-inspection",
            "source": source,
            "inputs": snapshot,
            "report_sha256": review.digest(dest / "report.json", 1024 * 1024),
            "report": report,
        }
        review.atomic_json(dest / "receipt.json", result)
        shutil.rmtree(project)
        project = None
        pending.unlink(missing_ok=True)
        return result
    except review.CleanupUnverified:
        # The process may still use the project. Preserve both project and marker.
        raise
    except Exception as exc:
        cleanup_error = None
        if project is not None:
            try:
                shutil.rmtree(project)
            except OSError as error:
                cleanup_error = error
        try:
            pending.unlink(missing_ok=True)
        except OSError as error:
            cleanup_error = cleanup_error or error
        message = str(exc)
        if cleanup_error is not None:
            message += f"; output cleanup failed: {cleanup_error}"
        raise OperationUnknown(message) from exc
