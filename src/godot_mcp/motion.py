"""Source-bound engine evaluation of a named imported animation."""

import itertools
import json
import math
import shutil
import tempfile
from pathlib import Path

from cavi_mcp_core import files
from cavi_mcp_core.contracts import OperationUnknown

from . import inspection, review
from .config import GODOT, tools

REPORT_LIMIT = 4 * 1024 * 1024


def validate_times(times):
    if (
        not isinstance(times, list)
        or not 2 <= len(times) <= 9
        or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
            for v in times
        )
        or any(a >= b for a, b in itertools.pairwise(times))
    ):
        raise ValueError(
            "Choose 2–9 strictly increasing finite normalized times in [0, 1]"
        )


def validate_report(report, player, animation, times):
    if not isinstance(report, dict) or report.get("status") != "ok":
        raise ValueError("Invalid Godot motion report")
    duration = report.get("duration")
    if (
        report.get("player") != player
        or report.get("animation") != animation
        or type(duration) not in (int, float)
        or not math.isfinite(duration)
        or duration <= 0
        or not isinstance(report.get("nodes"), list)
        or not 1 <= len(report["nodes"]) <= 256
        or not isinstance(report.get("bones"), list)
        or len(report["bones"]) > 256
        or not isinstance(report.get("samples"), list)
        or len(report["samples"]) != len(times)
        or not isinstance(report.get("godot_version"), dict)
        or report.get("units") != "Godot scene units"
        or report.get("bone_space") != "skeleton relative and Godot world"
        or type(report.get("loop_mode")) is not int
    ):
        raise ValueError("Motion report identity or limits differ")

    def transform(value):
        if not isinstance(value, dict) or set(value) != {"origin", "basis"}:
            return False
        origin, basis = value["origin"], value["basis"]

        def vector(v):
            return (
                isinstance(v, list)
                and len(v) == 3
                and all(type(n) in (int, float) and math.isfinite(n) for n in v)
            )

        return (
            vector(origin)
            and isinstance(basis, list)
            and len(basis) == 3
            and all(vector(v) for v in basis)
        )

    nodes, bones = report["nodes"], report["bones"]
    if (
        any(
            not isinstance(n, dict)
            or not isinstance(n.get("path"), str)
            or not n["path"]
            or not isinstance(n.get("type"), str)
            for n in nodes
        )
        or len({n["path"] for n in nodes}) != len(nodes)
        or any(
            not isinstance(b, dict)
            or not isinstance(b.get("skeleton"), str)
            or not isinstance(b.get("name"), str)
            or not b["name"]
            or type(b.get("index")) is not int
            or b["index"] < 0
            or type(b.get("parent")) is not int
            or b["parent"] < -1
            or b["parent"] >= b["index"]
            or not any(
                n["path"] == b["skeleton"] and n["type"] == "Skeleton3D" for n in nodes
            )
            for b in bones
        )
        or len({(b["skeleton"], b["index"]) for b in bones}) != len(bones)
    ):
        raise ValueError("Motion inventory is invalid")
    for skeleton in {b["skeleton"] for b in bones}:
        subset = [b for b in bones if b["skeleton"] == skeleton]
        if sorted(b["index"] for b in subset) != list(range(len(subset))) or len(
            {b["name"] for b in subset}
        ) != len(subset):
            raise ValueError("Skeleton bone inventory is incomplete")
    for sample, t in zip(report["samples"], times, strict=True):
        if (
            not isinstance(sample, dict)
            or type(sample.get("normalized_time")) not in (int, float)
            or not math.isfinite(sample["normalized_time"])
            or sample["normalized_time"] != t
            or type(sample.get("seconds")) not in (int, float)
            or not math.isfinite(sample["seconds"])
            or not math.isclose(
                sample["seconds"], t * duration, rel_tol=1e-6, abs_tol=1e-6
            )
            or not isinstance(sample.get("nodes"), list)
            or len(sample["nodes"]) != len(nodes)
            or not isinstance(sample.get("bones"), list)
            or len(sample["bones"]) != len(bones)
            or any(
                not isinstance(n, dict)
                or not transform(n.get("local_transform"))
                or not transform(n.get("world_transform"))
                for n in sample["nodes"]
            )
            or any(
                not isinstance(b, dict)
                or not transform(b.get("skeleton_transform"))
                or not transform(b.get("world_transform"))
                for b in sample["bones"]
            )
        ):
            raise ValueError("Motion sample is incomplete or has the wrong time")
    return report


def inspect_motion(root, source, directory, player, animation, normalized_times):
    validate_times(normalized_times)
    if (
        not isinstance(player, str)
        or not player
        or not isinstance(animation, str)
        or not animation
    ):
        raise ValueError("Choose an explicit player and animation")
    src = review.inside(root, source)
    dest = review.inside(root, directory)
    if not src.is_file() or src.suffix.lower() != ".glb":
        raise ValueError("Source must be a GLB file")
    review.select_clip(inspection.validate_glb(src), animation)
    engine = tools((GODOT,))[GODOT]
    runtime = Path(__file__).parent / "runtime" / "motion.gd"
    config = {
        "player": player,
        "animation": animation,
        "normalized_times": normalized_times,
    }
    snapshot = {
        "source_sha256": review.digest(src, review.LIMIT),
        "engine_sha256": review.digest(engine),
        "provider": review.provider_identity(),
    }
    dest.mkdir(parents=False, exist_ok=False)
    pending = dest / ".motion.pending"
    project = None
    try:
        review.atomic_json(
            dest / "owner.json", {"kind": "godot-motion-inspection", "source": source}
        )
        review.atomic_json(pending, {"kind": "motion_pending"})
        review.atomic_json(
            dest / "inputs.json",
            {"source": source, "config": config, "inputs": snapshot},
        )
        project = Path(tempfile.mkdtemp(prefix=".motion-", dir=dest))
        (project / "project.godot").write_text(
            '[application]\nconfig/name="Motion inspection"\n'
        )
        shutil.copyfile(src, project / "subject.glb")
        shutil.copyfile(runtime, project / "motion.gd")
        review.atomic_json(project / "config.json", config)
        if (
            review.digest(project / "subject.glb", review.LIMIT)
            != snapshot["source_sha256"]
            or review.digest(project / "motion.gd")
            != snapshot["provider"]["runtime"]["motion.gd"]
            or json.loads(files.read_bytes(project / "config.json", 1024 * 1024))
            != config
        ):
            raise ValueError("Motion inputs changed while copying")
        review.run_owned(
            [
                str(engine),
                "--headless",
                "--path",
                str(project),
                "--script",
                "res://motion.gd",
            ],
            120,
            dest,
            "godot",
        )
        if (
            b"ERROR:" in (dest / "godot.stderr").read_bytes()
            or b"ERROR:" in (dest / "godot.stdout").read_bytes()
        ):
            raise ValueError("Godot reported a motion inspection error")
        report = validate_report(
            json.loads(files.read_bytes(project / "report.json", REPORT_LIMIT)),
            player,
            animation,
            normalized_times,
        )
        if (
            review.digest(src, review.LIMIT) != snapshot["source_sha256"]
            or review.digest(engine) != snapshot["engine_sha256"]
            or review.provider_identity() != snapshot["provider"]
            or review.digest(project / "subject.glb", review.LIMIT)
            != snapshot["source_sha256"]
            or review.digest(project / "motion.gd")
            != snapshot["provider"]["runtime"]["motion.gd"]
            or json.loads(files.read_bytes(project / "config.json", 1024 * 1024))
            != config
        ):
            raise ValueError("Motion inspection inputs changed")
        review.atomic_json(dest / "report.json", report)
        result = {
            "kind": "godot-motion-inspection",
            "source": source,
            "config": config,
            "inputs": snapshot,
            "report_sha256": review.digest(dest / "report.json", REPORT_LIMIT),
            "report": report,
        }
        review.atomic_json(
            dest / "receipt.json", {k: v for k, v in result.items() if k != "report"}
        )
        shutil.rmtree(project)
        project = None
        pending.unlink(missing_ok=True)
        return result
    except review.CleanupUnverified:
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
        raise OperationUnknown(
            str(exc)
            + (f"; output cleanup failed: {cleanup_error}" if cleanup_error else "")
        ) from exc
