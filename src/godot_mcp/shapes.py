"""Checks for the numbers, vectors and transforms in engine reports."""

import math


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def count(value):
    return type(value) is int and value >= 0


def vector(value):
    return isinstance(value, list) and len(value) == 3 and all(map(number, value))


def basis(value):
    return isinstance(value, list) and len(value) == 3 and all(map(vector, value))


def transform(value):
    return (
        isinstance(value, dict)
        and set(value) == {"origin", "basis"}
        and vector(value["origin"])
        and basis(value["basis"])
    )
