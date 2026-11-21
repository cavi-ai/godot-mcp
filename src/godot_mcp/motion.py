"""Source-bound engine evaluation of a named imported animation."""

import itertools
import math

from . import engine, inspection, review
from .shapes import number, transform, vector

REPORT_LIMIT = 4 * 1024 * 1024
FRAME_RATE = 30
MAX_FRAMES = 1801
QA_KEYS = {
    "frame_rate",
    "frames",
    "seconds_per_frame",
    "loop_seam",
    "max_linear_speed",
    "max_angular_speed",
    "root_motion",
}


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
    qa = validate_qa(report.get("qa"), duration, nodes, bones)
    if not _root_motion_matches(qa, report["samples"], times, nodes, bones):
        raise ValueError("Motion QA metrics are invalid")
    report["qa"] = qa
    return report


def _dot(left, right):
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _unit(components):
    length = math.sqrt(_dot(components, components))
    if length == 0 or not math.isfinite(length):
        return None
    return tuple(component / length for component in components)


def _orthonormal_columns(columns):
    """Godot Basis.orthonormalize, which turn() applies before the quaternion."""
    x = _unit(columns[0])
    if x is None:
        return None
    xy = _dot(x, columns[1])
    y = _unit(tuple(columns[1][i] - x[i] * xy for i in range(3)))
    if y is None:
        return None
    xz, yz = _dot(x, columns[2]), _dot(y, columns[2])
    z = _unit(tuple(columns[2][i] - x[i] * xz - y[i] * yz for i in range(3)))
    if z is None:
        return None
    return (x, y, z)


def _determinant(columns):
    x, y, z = columns
    return (
        x[0] * (y[1] * z[2] - y[2] * z[1])
        - x[1] * (y[0] * z[2] - y[2] * z[0])
        + x[2] * (y[0] * z[1] - y[1] * z[0])
    )


def _quaternion(columns):
    """Godot Basis.get_rotation_quaternion for a basis stored as its columns."""
    columns = _orthonormal_columns(columns)
    if columns is None:
        return None
    if _determinant(columns) < 0:
        columns = tuple(tuple(-value for value in column) for column in columns)
    rows = tuple(tuple(columns[column][row] for column in range(3)) for row in range(3))
    trace = rows[0][0] + rows[1][1] + rows[2][2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0)
        weight = scale * 0.5
        scale = 0.5 / scale
        return (
            (rows[2][1] - rows[1][2]) * scale,
            (rows[0][2] - rows[2][0]) * scale,
            (rows[1][0] - rows[0][1]) * scale,
            weight,
        )
    if rows[0][0] < rows[1][1]:
        index = 2 if rows[1][1] < rows[2][2] else 1
    else:
        index = 2 if rows[0][0] < rows[2][2] else 0
    j = (index + 1) % 3
    k = (index + 2) % 3
    radicand = rows[index][index] - rows[j][j] - rows[k][k] + 1.0
    if radicand <= 0:
        return None
    scale = math.sqrt(radicand)
    quat = [0.0, 0.0, 0.0, 0.0]
    quat[index] = scale * 0.5
    scale = 0.5 / scale
    quat[3] = (rows[k][j] - rows[j][k]) * scale
    quat[j] = (rows[j][index] + rows[index][j]) * scale
    quat[k] = (rows[k][index] + rows[index][k]) * scale
    return tuple(quat)


def _rotation_angle(before, after):
    """Godot Quaternion.angle_to, the angle turn() divides by the sweep step."""
    start, end = _quaternion(before), _quaternion(after)
    if start is None or end is None:
        return None
    dot = sum(a * b for a, b in zip(start, end, strict=True))
    cosine = dot * dot * 2 - 1
    if not math.isfinite(cosine):
        return None
    return math.acos(min(1.0, max(-1.0, cosine)))


def _proof_amount(proof, accept, measure):
    if (
        not isinstance(proof, dict)
        or set(proof) != {"from", "to", "step"}
        or not number(proof["step"])
        or proof["step"] <= 0
        or not accept(proof["from"])
        or not accept(proof["to"])
    ):
        return None
    return measure(proof["from"], proof["to"])


def _speed_matches(proof, value, accept, measure):
    if value == 0:
        return proof is None
    amount = _proof_amount(proof, accept, measure)
    return (
        amount is not None
        and math.isfinite(amount)
        and math.isclose(amount / proof["step"], value, rel_tol=1e-5, abs_tol=1e-4)
    )


def _angular_separation(before, after):
    return _rotation_angle(before["basis"], after["basis"])


def _world_origin(sample, nodes, bones, path, bone):
    if bone is None:
        for spec, pose in zip(nodes, sample["nodes"], strict=True):
            if spec["path"] == path:
                return pose["world_transform"]["origin"]
        return None
    for spec, pose in zip(bones, sample["bones"], strict=True):
        if spec["skeleton"] == path and spec["name"] == bone:
            return pose["world_transform"]["origin"]
    return None


def _root_motion_matches(qa, samples, times, nodes, bones):
    if 0 not in times or 1 not in times:
        return True
    start, end = samples[times.index(0)], samples[times.index(1)]
    for item in qa["root_motion"]:
        before = _world_origin(start, nodes, bones, item["path"], item["bone"])
        after = _world_origin(end, nodes, bones, item["path"], item["bone"])
        if before is None or after is None:
            return False
        delta = [left - right for left, right in zip(after, before, strict=True)]
        if any(
            not math.isclose(got, want, rel_tol=0, abs_tol=1e-4)
            for got, want in zip(delta, item["displacement"], strict=True)
        ):
            return False
    return True


def validate_qa(qa, duration, nodes, bones):
    """The 30 fps motion metrics, refused when a value, its proof, or its target is invalid."""
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
        or set(qa) != QA_KEYS | {"linear_proof", "angular_proof"}
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
        or not _speed_matches(
            qa["linear_proof"], qa["max_linear_speed"]["value"], vector, math.dist
        )
        or not _speed_matches(
            qa["angular_proof"],
            qa["max_angular_speed"]["value"],
            transform,
            _angular_separation,
        )
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
    return {key: qa[key] for key in qa if key in QA_KEYS}


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
