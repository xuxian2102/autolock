"""Coil geometry primitives and representative phone receiver models."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, radians, sin
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class RectangularCoil:
    """Planar rectangular coil represented by closed filament centre-lines.

    ``turn_sizes_mm`` contains (width, height) for every turn.  The finite trace
    dimensions are retained for resistance and self-inductance estimation; the
    field and mutual-inductance solvers use centre-line filaments.
    """

    name: str
    turn_sizes_mm: tuple[tuple[float, float], ...]
    trace_width_mm: float
    spacing_mm: float
    copper_thickness_um: float
    center_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    turns_equivalent: float = 1.0
    metadata: dict = field(default_factory=dict, compare=False)

    @property
    def turns(self) -> int:
        return len(self.turn_sizes_mm)

    @property
    def outer_width_mm(self) -> float:
        return max(width for width, _ in self.turn_sizes_mm)

    @property
    def outer_height_mm(self) -> float:
        return max(height for _, height in self.turn_sizes_mm)

    @property
    def inner_width_mm(self) -> float:
        width = min(width for width, _ in self.turn_sizes_mm)
        return max(0.01, width - self.trace_width_mm)

    @property
    def inner_height_mm(self) -> float:
        height = min(height for _, height in self.turn_sizes_mm)
        return max(0.01, height - self.trace_width_mm)

    @property
    def conductor_length_mm(self) -> float:
        return float(self.metadata.get(
            "actual_conductor_length_mm",
            sum(2.0 * (width + height) for width, height in self.turn_sizes_mm),
        ))

    def loops(self, *, offset_mm=(0.0, 0.0, 0.0), tilt_deg=(0.0, 0.0, 0.0)) -> list[np.ndarray]:
        center = np.asarray(self.center_mm, dtype=float) + np.asarray(offset_mm, dtype=float)
        rotation = rotation_matrix(*tilt_deg)
        loops = []
        for width, height in self.turn_sizes_mm:
            points = rectangle_points(width, height)
            loops.append(points @ rotation.T + center)
        return loops


def rectangle_points(width_mm: float, height_mm: float) -> np.ndarray:
    """Return a counter-clockwise closed rectangle in the local xy plane."""
    x = width_mm / 2.0
    y = height_mm / 2.0
    return np.asarray([[-x, -y, 0.0], [x, -y, 0.0], [x, y, 0.0], [-x, y, 0.0], [-x, -y, 0.0]])


def rotation_matrix(x_deg: float = 0.0, y_deg: float = 0.0, z_deg: float = 0.0) -> np.ndarray:
    ax, ay, az = map(radians, (x_deg, y_deg, z_deg))
    rx = np.asarray([[1, 0, 0], [0, cos(ax), -sin(ax)], [0, sin(ax), cos(ax)]], dtype=float)
    ry = np.asarray([[cos(ay), 0, sin(ay)], [0, 1, 0], [-sin(ay), 0, cos(ay)]], dtype=float)
    rz = np.asarray([[cos(az), -sin(az), 0], [sin(az), cos(az), 0], [0, 0, 1]], dtype=float)
    return rz @ ry @ rx


PHONE_CASES: dict[str, RectangularCoil] = {
    # These are deliberately generic equivalents, not claims about any iPhone.
    "Phone-S": RectangularCoil("Phone-S", ((22.0, 28.0),), 1.0, 0.0, 100.0,
                                metadata={"status": "representative equivalent, not a device teardown"}),
    "Phone-M": RectangularCoil("Phone-M", ((30.0, 38.0),), 1.0, 0.0, 100.0,
                                metadata={"status": "representative equivalent, not a device teardown"}),
    "Phone-L": RectangularCoil("Phone-L", ((40.0, 50.0),), 1.0, 0.0, 100.0,
                                metadata={"status": "representative equivalent, not a device teardown"}),
}


def phone_case(name: str) -> RectangularCoil:
    try:
        return PHONE_CASES[name]
    except KeyError as exc:
        raise KeyError(f"unknown phone case {name!r}; choose {', '.join(PHONE_CASES)}") from exc


def bbox(points: Iterable[Iterable[float]]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    array = np.asarray(list(points), dtype=float)
    return tuple(array.min(axis=0)), tuple(array.max(axis=0))
