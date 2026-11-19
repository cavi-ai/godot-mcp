"""Validated selection and engine evidence for an animation pose."""

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .shapes import basis, count, number, vector


class Pose(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    player: Annotated[str, Field(min_length=1, max_length=512)]
    animation: Annotated[str, Field(min_length=1, max_length=512)]
    time_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]


def validate_scene(selection, scene):
    if not isinstance(scene, dict):
        raise ValueError("Missing rendered pose metadata")  # noqa: TRY004
    time, duration, bones = (
        scene.get("time_seconds"),
        scene.get("duration"),
        scene.get("bones"),
    )
    if (
        scene.get("player") != selection["player"]
        or scene.get("animation") != selection["animation"]
        or not number(time)
        or time < 0
        or not math.isclose(time, selection["time_seconds"], abs_tol=1e-5, rel_tol=0)
        or not number(duration)
        or duration <= 0
        or time > duration + 1e-5
        or not isinstance(bones, list)
        or type(scene.get("bone_count")) is not int
        or scene["bone_count"] != len(bones)
        or len(bones) > 256
    ):
        raise ValueError("Rendered pose does not match the selection")
    for bone in bones:
        if (
            not isinstance(bone, dict)
            or not isinstance(bone.get("name"), str)
            or not bone["name"]
            or not isinstance(bone.get("skeleton"), str)
            or not bone["skeleton"]
            or not count(bone.get("index"))
            or not vector(bone.get("origin"))
            or not basis(bone.get("basis"))
        ):
            raise ValueError("Invalid rendered skeleton pose")
    if len({(bone["skeleton"], bone["index"]) for bone in bones}) != len(bones):
        raise ValueError("Duplicate rendered skeleton bone")
