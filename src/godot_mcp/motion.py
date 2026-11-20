"""Source-bound engine evaluation of a named imported animation."""

import itertools
import math

from . import engine, inspection, review
from .shapes import number, transform, vector

REPORT_LIMIT = 4 * 1024 * 1024
FRAME_RATE = 30
MAX_FRAMES = 1801


def validate_times(times):
    if (
        not isinstance(times, list)
        or not 2 <= len(times) <= 9
        or any(not number(v) or not 0 <= v <= 1 for v in times)
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
        or not number(duration)
        or duration <= 0
        or not isinstance(report.get("nodes"), list)
        or not 1 <= len(report["nodes"]) <= 256
        or not isinstance(report.get("bones"), list)
        or len(report["bones"]) > 256
        or not isinstance(report.get("samples"), list)
        or len(report["samples"]) != len(times)
        or not isinstance(report.get("godot_version"), dict)
        or report.get("units") != "metres"
        or report.get("bone_space") != "skeleton relative and Godot world"
        or type(report.get("loop_mode")) is not int
    ):
        raise ValueError("Motion report identity or limits differ")
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
            or not number(sample.get("normalized_time"))
            or sample["normalized_time"] != t
            or not number(sample.get("seconds"))
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
    validate_qa(report.get("qa"), duration, nodes, bones)
    return report


def validate_qa(qa, duration, nodes, bones):
    """The 30 fps motion metrics, refused when a value or its target is invalid."""
    paths = {n["path"] for n in nodes}
    bone_keys = {(b["skeleton"], b["name"]) for b in bones}

    def target(value, allow_none):
        path, bone = value.get("path"), value.get("bone")
        if path is None and bone is None:
            return allow_none
        return (bone is None and path in paths) or (
            isinstance(bone, str) and (path, bone) in bone_keys
        )

    def speed(value):
        return (
            isinstance(value, dict)
            and set(value) == {"value", "path", "bone"}
            and number(value["value"])
            and value["value"] >= 0
            and target(value, value["value"] == 0)
            and (value["value"] == 0) is (value["path"] is None)
        )

    if (
        not isinstance(qa, dict)
        or set(qa)
        != {
            "frame_rate",
            "frames",
            "seconds_per_frame",
            "loop_seam",
            "max_linear_speed",
            "max_angular_speed",
            "root_motion",
        }
        or qa["frame_rate"] != FRAME_RATE
        or type(qa["frames"]) is not int
        or not 2 <= qa["frames"] <= MAX_FRAMES
        or not number(qa["seconds_per_frame"])
        or not math.isclose(
            qa["seconds_per_frame"], duration / (qa["frames"] - 1), rel_tol=1e-6
        )
        or not isinstance(qa["loop_seam"], dict)
        or set(qa["loop_seam"]) != {"position", "rotation"}
        or any(not number(v) or v < 0 for v in qa["loop_seam"].values())
        or not speed(qa["max_linear_speed"])
        or not speed(qa["max_angular_speed"])
        or not isinstance(qa["root_motion"], list)
        or any(
            not isinstance(m, dict)
            or set(m) != {"path", "bone", "displacement"}
            or not target(m, False)
            or not vector(m["displacement"])
            for m in qa["root_motion"]
        )
    ):
        raise ValueError("Motion QA metrics are invalid")


def inspect_motion(root, source, directory, player, animation, normalized_times):
    validate_times(normalized_times)
    if (
        not isinstance(player, str)
        or not player
        or not isinstance(animation, str)
        or not animation
    ):
        raise ValueError("Choose an explicit player and animation")
    src = review.source_glb(root, source)
    dest = review.new_output(root, directory)
    review.select_clip(inspection.validate_glb(src), animation)
    return engine.run(
        src,
        dest,
        source,
        kind="godot-motion-inspection",
        name="motion",
        script="motion.gd",
        settings={
            "player": player,
            "animation": animation,
            "normalized_times": normalized_times,
        },
        report_limit=REPORT_LIMIT,
        validate=lambda report: validate_report(
            report, player, animation, normalized_times
        ),
    )
