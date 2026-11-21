"""Source-bound Godot runtime inspection of self-contained GLB files."""

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from . import engine, review
from .shapes import count, number, transform, vector

Limit = Annotated[int, Field(strict=True, ge=0)]

REPORT_LIMIT = 1024 * 1024
LIMITS = {"nodes": 256, "surfaces": 256, "bones": 256, "clips": 128}
TRIANGLES = 3
QA_COUNTS = (
    "triangles",
    "vertices",
    "degenerate_triangles",
    "uv0_outside_unit_surfaces",
    "materials",
    "textures",
    "texture_rgba8_bytes",
    "max_texture_size",
    "npot_textures",
    "max_bone_influences",
    "max_blend_shapes",
    "mirrored_meshes",
    "non_uniform_scale_meshes",
    "animation_keys",
)


def validate_glb(path):
    return review.read_glb(path, static_review=False)


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
        or report.get("units") != "metres"
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
    qa = report.get("qa")
    if (
        not isinstance(qa, dict)
        or set(qa) != {*QA_COUNTS, "bounds"}
        or not all(count(qa[key]) for key in QA_COUNTS)
        or qa["max_bone_influences"] not in (0, 4, 8)
        or qa["degenerate_triangles"] > qa["triangles"]
        or qa["uv0_outside_unit_surfaces"] > totals["surfaces"]
        or qa["npot_textures"] > qa["textures"]
        or (qa["max_texture_size"] == 0) is not (qa["textures"] == 0)
        or not (qa["bounds"] is None or bounds(qa["bounds"]))
    ):
        raise ValueError("Inspection QA metrics are invalid")
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
        or "degenerate_triangles" not in s
        or not (
            count(s["triangles"])
            and count(s["degenerate_triangles"])
            and s["degenerate_triangles"] <= s["triangles"]
            if s["primitive"] == TRIANGLES
            else s["triangles"] is None and s["degenerate_triangles"] is None
        )
        or type(s.get("uv0")) is not bool
        or type(s.get("uv1")) is not bool
        or s.get("uv0_outside_unit") not in (False, s["uv0"])
        or type(s["uv0_outside_unit"]) is not bool
        or not material(s.get("material"))
        for s in surfaces
    ) or len({(s["node"], s["surface"]) for s in surfaces}) != len(surfaces):
        raise ValueError("Inspection surface inventory is invalid")
    if not truncated["surfaces"] and [
        qa["triangles"],
        qa["vertices"],
        qa["degenerate_triangles"],
        qa["uv0_outside_unit_surfaces"],
    ] != [
        sum(s["triangles"] or 0 for s in surfaces),
        sum(s["vertices"] for s in surfaces),
        sum(s["degenerate_triangles"] or 0 for s in surfaces),
        sum(s["uv0_outside_unit"] for s in surfaces),
    ]:
        raise ValueError("Inspection QA totals differ from its surfaces")
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
        or not number(c.get("duration"))
        or c["duration"] < 0
        or not count(c.get("tracks"))
        or not count(c.get("keys"))
        for c in clips
    ) or len({(c["player"], c["name"]) for c in clips}) != len(clips):
        raise ValueError("Inspection clip inventory is invalid")
    if not truncated["clips"] and qa["animation_keys"] != sum(c["keys"] for c in clips):
        raise ValueError("Inspection QA totals differ from its clips")
    return report


class Budget(BaseModel):
    """Upper limits on inspection metrics; a limit left out is not checked.

    ``draw_calls`` limits surfaces and ``bones`` limits skeleton bones;
    ``base_offset`` limits the distance in metres from the scene origin to the
    bottom centre of the rest-pose bounds. The others limit the report's `qa`
    metric of the same name.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    triangles: Limit | None = None
    vertices: Limit | None = None
    draw_calls: Limit | None = None
    materials: Limit | None = None
    textures: Limit | None = None
    texture_rgba8_bytes: Limit | None = None
    max_texture_size: Limit | None = None
    npot_textures: Limit | None = None
    degenerate_triangles: Limit | None = None
    uv0_outside_unit_surfaces: Limit | None = None
    bones: Limit | None = None
    max_bone_influences: Limit | None = None
    max_blend_shapes: Limit | None = None
    mirrored_meshes: Limit | None = None
    non_uniform_scale_meshes: Limit | None = None
    animation_keys: Limit | None = None
    base_offset: (
        Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)] | None
    ) = None


def base_offset(report):
    box = report["qa"]["bounds"]
    if box is None:
        return None
    (x, y, z), (width, _, depth) = box["position"], box["size"]
    return math.hypot(x + width / 2, y, z + depth / 2)


def evaluate(limits, report):
    """Each limit in ``limits`` with the report's value and whether it is within it.

    A scene without meshes has no base offset, so that limit is not met.
    """
    values = {
        **report["qa"],
        "draw_calls": report["totals"]["surfaces"],
        "bones": report["totals"]["bones"],
        "base_offset": base_offset(report),
    }
    checks = [
        {
            "rule": rule,
            "limit": limit,
            "value": values[rule],
            "within": values[rule] is not None and values[rule] <= limit,
        }
        for rule, limit in limits.items()
    ]
    return {
        "limits": limits,
        "checks": checks,
        "within": all(check["within"] for check in checks),
    }


def inspect_asset(root, source, directory, limits=None):
    """Inspect ``source``; ``limits`` from a `Budget` are checked and recorded."""
    src = review.source_glb(root, source)
    dest = review.new_output(root, directory)
    validate_glb(src)
    if limits is not None:
        limits = Budget.model_validate(limits).model_dump(exclude_none=True)
    return engine.run(
        src,
        dest,
        source,
        kind="godot-asset-inspection",
        name="inspection",
        script="inspect.gd",
        settings=None,
        report_limit=REPORT_LIMIT,
        validate=validate_report,
        assess=None
        if limits is None
        else lambda report: {"budget": evaluate(limits, report)},
    )
