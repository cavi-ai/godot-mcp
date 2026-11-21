"""Source-bound Godot runtime inspection of self-contained GLB files."""

from . import budget, engine, review
from .shapes import count, number, transform, vector

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
    "zero_weight_vertices",
    "unnormalized_weight_vertices",
    "max_blend_shapes",
    "mirrored_meshes",
    "non_uniform_scale_meshes",
    "animation_keys",
)
TRACK_TYPES = (
    "value",
    "position",
    "rotation",
    "scale",
    "blend_shape",
    "method",
    "bezier",
    "audio",
    "animation",
)
MATERIAL_TYPES = frozenset({"StandardMaterial3D", "ORMMaterial3D"})
REPRESENTED_MATERIAL_EXTENSIONS = frozenset(
    {
        "KHR_materials_unlit",
        "KHR_materials_emissive_strength",
        "KHR_materials_pbrSpecularGlossiness",
    }
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


def rgba(value):
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(number(item) for item in value)
    )


def material(value):
    if value is None:
        return True
    textures = value.get("textures") if isinstance(value, dict) else None
    if not (
        isinstance(value, dict)
        and isinstance(value.get("name"), str)
        and isinstance(value.get("type"), str)
        and isinstance(textures, dict)
        and all(
            isinstance(texture, dict)
            and isinstance(texture.get("name"), str)
            and count(texture.get("width"))
            and count(texture.get("height"))
            for texture in textures.values()
        )
    ):
        return False
    if value["type"] not in MATERIAL_TYPES:
        return True
    return (
        rgba(value.get("albedo"))
        and rgba(value.get("emission"))
        and all(
            number(value.get(key))
            for key in ("metallic", "roughness", "emission_energy", "normal_scale")
        )
        and type(value.get("transparency")) is int
        and 0 <= value["transparency"] <= 4
        and type(value.get("cull")) is int
        and 0 <= value["cull"] <= 2
        and type(value.get("shading")) is int
        and 0 <= value["shading"] <= 2
    )


def clip_tracks(types, tracks):
    return (
        isinstance(types, dict)
        and set(types) == set(TRACK_TYPES)
        and all(count(types[key]) for key in TRACK_TYPES)
        and sum(types.values()) == tracks
    )


def _dropped_material_name(name):
    return (
        isinstance(name, str)
        and name.startswith("KHR_materials_")
        and name not in REPRESENTED_MATERIAL_EXTENSIONS
    )


def dropped_material_extensions(document):
    """Sorted ``KHR_materials_*`` names Godot 4.7 does not map onto the material."""
    found = set()
    used = document.get("extensionsUsed")
    if isinstance(used, list):
        found.update(name for name in used if _dropped_material_name(name))
    materials = document.get("materials")
    if isinstance(materials, list):
        for item in materials:
            extensions = item.get("extensions") if isinstance(item, dict) else None
            if isinstance(extensions, dict):
                found.update(
                    name for name in extensions if _dropped_material_name(name)
                )
    return sorted(found)


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
        or type(c.get("loop_mode")) is not int
        or not 0 <= c["loop_mode"] <= 2
        or not clip_tracks(c.get("track_types"), c.get("tracks"))
        for c in clips
    ) or len({(c["player"], c["name"]) for c in clips}) != len(clips):
        raise ValueError("Inspection clip inventory is invalid")
    if not truncated["clips"] and qa["animation_keys"] != sum(c["keys"] for c in clips):
        raise ValueError("Inspection QA totals differ from its clips")
    return report


def inspect_asset(root, source, directory, limits=None):
    """Inspect ``source``; ``limits`` from a `budget.Budget` are checked and recorded."""
    src = review.source_glb(root, source)
    dest = review.new_output(root, directory)
    document = validate_glb(src)
    if limits is not None:
        limits = budget.Budget.model_validate(limits).model_dump(exclude_none=True)

    def validate(report):
        checked = validate_report(report)
        checked["dropped_material_extensions"] = dropped_material_extensions(document)
        return checked

    return engine.run(
        src,
        dest,
        source,
        kind="godot-asset-inspection",
        name="inspection",
        script="inspect.gd",
        settings=None,
        report_limit=REPORT_LIMIT,
        validate=validate,
        assess=None
        if limits is None
        else lambda report: {"budget": budget.evaluate(limits, report)},
    )
