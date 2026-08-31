"""Antenna self-inductance, resistance, Q, and quasi-static magnetic field."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, log, pi, sqrt

import numpy as np

from .geometry import RectangularCoil

MU0 = 4e-7 * pi
EPS0 = 8.8541878128e-12
COPPER_CONDUCTIVITY = 5.80e7


@dataclass(frozen=True)
class CoilElectrical:
    inductance_h: float
    resistance_dc_ohm: float
    resistance_ac_ohm: float
    q_air: float
    skin_depth_m: float
    self_resonance_estimate_hz: float | None
    method: str

    def to_dict(self) -> dict:
        return asdict(self)


def skin_depth(frequency_hz: float, conductivity_s_m: float, mu_r: float = 1.0) -> float:
    return sqrt(2.0 / (2.0 * pi * frequency_hz * MU0 * mu_r * conductivity_s_m))


def current_sheet_inductance(coil: RectangularCoil) -> float:
    """Mohan current-sheet estimate, using geometric-mean square dimensions.

    The Rev A coil is square.  Geometric-mean dimensions keep the function
    useful for modestly rectangular parameter sweeps but are not a replacement
    for a field solver for extreme aspect ratios.
    """
    d_out = sqrt((coil.outer_width_mm + coil.trace_width_mm) *
                 (coil.outer_height_mm + coil.trace_width_mm)) * 1e-3
    d_in = sqrt(coil.inner_width_mm * coil.inner_height_mm) * 1e-3
    d_avg = (d_out + d_in) / 2.0
    fill = (d_out - d_in) / (d_out + d_in)
    if fill <= 0:
        raise ValueError("coil fill ratio must be positive")
    c1, c2, c3, c4 = 1.27, 2.07, 0.18, 0.13
    return MU0 * coil.turns ** 2 * d_avg * c1 / 2.0 * (log(c2 / fill) + c3 * fill + c4 * fill ** 2)


def single_loop_inductance(coil: RectangularCoil) -> float:
    """Equivalent circular-loop estimate for a one-turn receiver model."""
    width, height = coil.turn_sizes_mm[0]
    radius = sqrt((width * height * 1e-6) / pi)
    conductor_radius = max(coil.trace_width_mm * 1e-3 / 2.0, 0.1e-3)
    return MU0 * radius * max(0.5, log(8.0 * radius / conductor_radius) - 2.0)


def estimate_inductance(coil: RectangularCoil) -> float:
    return single_loop_inductance(coil) if coil.turns == 1 else current_sheet_inductance(coil)


def estimate_parasitic_capacitance(coil: RectangularCoil, epsilon_r: float = 4.2) -> float:
    """Low-confidence adjacent-trace/substrate capacitance estimate.

    This is used only to flag whether self resonance is comfortably above the
    carrier.  Matching calculations never treat this value as measured data.
    """
    if coil.turns <= 1 or coil.spacing_mm <= 0:
        return 2.0e-12
    gap_length_m = sum(2.0 * (w + h) for w, h in coil.turn_sizes_mm[1:]) * 1e-3
    thickness_m = coil.copper_thickness_um * 1e-6
    spacing_m = coil.spacing_mm * 1e-3
    return (0.9 + 0.1 * epsilon_r) * EPS0 * thickness_m / spacing_m * gap_length_m


def estimate_electrical(
    coil: RectangularCoil,
    frequency_hz: float = 13.56e6,
    *,
    conductivity_s_m: float = COPPER_CONDUCTIVITY,
    proximity_factor: float = 1.22,
) -> CoilElectrical:
    inductance = estimate_inductance(coil)
    length_m = coil.conductor_length_mm * 1e-3
    width_m = coil.trace_width_mm * 1e-3
    thickness_m = coil.copper_thickness_um * 1e-6
    resistance_dc = length_m / (conductivity_s_m * width_m * thickness_m)
    delta = skin_depth(frequency_hz, conductivity_s_m)
    # Wide-strip surface-current approximation.  It approaches Rdc for t << δ
    # and uses both broad surfaces when t is several skin depths.
    effective_thickness = 2.0 * delta * (1.0 - exp(-thickness_m / (2.0 * delta)))
    skin_factor = max(1.0, thickness_m / effective_thickness)
    resistance_ac = resistance_dc * skin_factor * proximity_factor
    omega = 2.0 * pi * frequency_hz
    q = omega * inductance / resistance_ac
    cp = estimate_parasitic_capacitance(coil)
    self_resonance = 1.0 / (2.0 * pi * sqrt(inductance * cp)) if cp > 0 else None
    return CoilElectrical(inductance, resistance_dc, resistance_ac, q, delta, self_resonance,
                          "Mohan current-sheet L; wide-strip skin + uncertain proximity R")


def magnetic_field(
    coil: RectangularCoil,
    point_mm: tuple[float, float, float],
    *,
    current_a: float = 1.0,
    quadrature_order: int = 8,
) -> np.ndarray:
    """Numerically integrate Biot-Savart over every rectangular turn."""
    point = np.asarray(point_mm, dtype=float) * 1e-3
    nodes, weights = np.polynomial.legendre.leggauss(quadrature_order)
    ts = (nodes + 1.0) / 2.0
    ws = weights / 2.0
    field = np.zeros(3, dtype=float)
    for loop_mm in coil.loops():
        loop = loop_mm * 1e-3
        for start, end in zip(loop[:-1], loop[1:]):
            dl = end - start
            positions = start[None, :] + ts[:, None] * dl[None, :]
            r = point[None, :] - positions
            norms = np.linalg.norm(r, axis=1)
            if np.any(norms < 1e-9):
                raise ValueError("field evaluation point lies on the filament")
            field += np.sum(ws[:, None] * np.cross(dl[None, :], r) / norms[:, None] ** 3, axis=0)
    return MU0 * current_a / (4.0 * pi) * field * coil.turns_equivalent
