"""Mutual inductance and coupling of arbitrarily offset/tilted rectangular coils."""

from __future__ import annotations

from math import pi, sqrt

import numpy as np

from .coil import MU0, estimate_inductance
from .geometry import RectangularCoil


def _segment_pair_integral(a0, a1, b0, b1, order: int) -> float:
    nodes, weights = np.polynomial.legendre.leggauss(order)
    t = (nodes + 1.0) / 2.0
    w = weights / 2.0
    da = a1 - a0
    db = b1 - b0
    dot = float(np.dot(da, db))
    if abs(dot) < 1e-24:
        return 0.0
    pa = a0[None, :] + t[:, None] * da[None, :]
    pb = b0[None, :] + t[:, None] * db[None, :]
    distances = np.linalg.norm(pa[:, None, :] - pb[None, :, :], axis=2)
    if np.any(distances < 1e-10):
        raise ValueError("coincident filaments require a finite-conductor model")
    return dot * float(np.sum((w[:, None] * w[None, :]) / distances))


def mutual_inductance(
    transmitter: RectangularCoil,
    receiver: RectangularCoil,
    *,
    separation_mm: float,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
    tilt_x_deg: float = 0.0,
    tilt_y_deg: float = 0.0,
    rotation_deg: float = 0.0,
    quadrature_order: int = 5,
) -> float:
    """Neumann filament integral for mutual inductance in henries."""
    tx_loops = [loop * 1e-3 for loop in transmitter.loops()]
    rx_loops = [loop * 1e-3 for loop in receiver.loops(
        offset_mm=(offset_x_mm, offset_y_mm, separation_mm),
        tilt_deg=(tilt_x_deg, tilt_y_deg, rotation_deg),
    )]
    integral = 0.0
    for tx in tx_loops:
        for rx in rx_loops:
            for a0, a1 in zip(tx[:-1], tx[1:]):
                for b0, b1 in zip(rx[:-1], rx[1:]):
                    integral += _segment_pair_integral(a0, a1, b0, b1, quadrature_order)
    return abs(MU0 / (4.0 * pi) * integral * transmitter.turns_equivalent * receiver.turns_equivalent)


def coupling_coefficient(mutual_h: float, transmitter_l_h: float, receiver_l_h: float) -> float:
    return mutual_h / sqrt(transmitter_l_h * receiver_l_h)


def coupled_pair(
    transmitter: RectangularCoil,
    receiver: RectangularCoil,
    **geometry,
) -> tuple[float, float]:
    mutual = mutual_inductance(transmitter, receiver, **geometry)
    return mutual, coupling_coefficient(mutual, estimate_inductance(transmitter), estimate_inductance(receiver))
