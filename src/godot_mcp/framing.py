"""Validated static camera framing and rendered evidence."""

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

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
