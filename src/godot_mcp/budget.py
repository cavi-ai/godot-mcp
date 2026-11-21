"""Asset budgets checked against a validated inspection report."""

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Limit = Annotated[int, Field(strict=True, ge=0)]


class Budget(BaseModel):
    """Upper limits on inspection metrics; a limit left out is not checked.

    ``draw_calls`` limits surfaces and ``bones`` limits skeleton bones;
    ``base_offset`` limits the distance in metres from the scene origin to the
    bottom centre of the rest-pose bounds. The others limit the report's `qa`
    metric of the same name, including ``zero_weight_vertices`` and
    ``unnormalized_weight_vertices``.
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
    zero_weight_vertices: Limit | None = None
    unnormalized_weight_vertices: Limit | None = None
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
