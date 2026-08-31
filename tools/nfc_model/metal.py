"""Calibratable reduced-order metal interaction prior.

This is intentionally not a replacement for 3D EM.  It maps geometry and skin
depth to bounded changes in field, inductance, and Q, and exposes every fitted
coefficient so NanoVNA or openEMS data can replace the priors later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, log10, sqrt

from .coil import COPPER_CONDUCTIVITY, skin_depth
from .materials import material, midpoint


@dataclass(frozen=True)
class MetalObject:
    material: str = "mild_steel"
    width_mm: float = 80.0
    height_mm: float = 25.0
    thickness_mm: float = 1.5
    distance_mm: float = 10.0
    position: str = "partial_overlap"
    area_scale: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MetalEffect:
    field_factor: float
    field_loss_db: float
    inductance_ratio: float
    q_ratio: float
    skin_depth_um: float
    shielding_strength: float
    uncertainty_db: float
    method: str


DEFAULT_PRIORS = {
    "shield_gain": 1.35,
    "distance_scale_fraction": 0.35,
    "eddy_inductance_fraction": 0.22,
    "magnetic_inductance_fraction": 0.08,
    "q_loss_gain": 3.0,
}


def _position_coupling(position: str, object_area: float, coil_area: float) -> float:
    area_ratio = min(1.0, max(0.0, object_area / coil_area))
    factors = {
        "behind": 1.0,
        "partial_overlap": 0.55,
        "beside": 0.24,
        "steel_frame_side": 0.18,
    }
    if position not in factors:
        raise ValueError(f"unknown metal position {position!r}")
    return factors[position] * sqrt(area_ratio)


def metal_effect(
    metal: MetalObject,
    *,
    coil_width_mm: float,
    coil_height_mm: float,
    frequency_hz: float = 13.56e6,
    ferrite_isolation: float = 0.0,
    coefficient_scale: float = 1.0,
    priors: dict | None = None,
) -> MetalEffect:
    p = dict(DEFAULT_PRIORS)
    if priors:
        p.update(priors)
    record = material("metals", metal.material)
    conductivity = midpoint(record["conductivity_s_m"])
    mu_r = midpoint(record["mu_r"])
    delta = skin_depth(frequency_hz, conductivity, mu_r)
    thickness_factor = 1.0 - exp(-metal.thickness_mm * 1e-3 / max(2.0 * delta, 1e-12))
    area = metal.width_mm * metal.height_mm * metal.area_scale
    coverage = _position_coupling(metal.position, area, coil_width_mm * coil_height_mm)
    scale_mm = p["distance_scale_fraction"] * sqrt(coil_width_mm * coil_height_mm)
    near = exp(-max(metal.distance_mm, 0.0) / max(scale_mm, 1e-6))
    conductivity_factor = 0.35 + 0.65 * sqrt(min(1.0, conductivity / COPPER_CONDUCTIVITY))
    raw_strength = (p["shield_gain"] * coefficient_scale * coverage * near *
                    thickness_factor * conductivity_factor)
    # Ferrite is an explicit metal×ferrite interaction, not an independent loss.
    strength = min(0.95, max(0.0, raw_strength * (1.0 - ferrite_isolation)))
    field_factor = max(0.05, 1.0 - strength)
    magnetic_term = p["magnetic_inductance_fraction"] * coverage * near * min(1.0, log10(max(mu_r, 1.0)) / 2.3)
    inductance_ratio = max(0.65, 1.0 - p["eddy_inductance_fraction"] * strength + magnetic_term)
    q_ratio = 1.0 / (1.0 + p["q_loss_gain"] * strength)
    uncertainty_db = 0.8 + 5.0 * strength
    return MetalEffect(field_factor, 20.0 * log10(field_factor), inductance_ratio, q_ratio,
                       delta * 1e6, strength, uncertainty_db,
                       "bounded eddy-current/image-field prior; calibrate for actual metal geometry")
