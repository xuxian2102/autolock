"""Calibratable NFC ferrite sheet model and metal-interaction term."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, log10, sqrt

from .materials import material, midpoint


@dataclass(frozen=True)
class FerriteSheet:
    material: str = "medium"
    thickness_mm: float = 0.0
    coverage_fraction: float = 1.0
    mu_prime_override: float | None = None
    mu_double_prime_override: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FerriteEffect:
    metal_isolation: float
    front_field_factor: float
    inductance_ratio: float
    q_ratio: float
    mu_prime: float
    mu_double_prime: float
    magnetic_loss_tangent: float
    uncertainty_db: float
    method: str


def ferrite_effect(sheet: FerriteSheet, *, metal_position: str = "behind") -> FerriteEffect:
    if sheet.thickness_mm <= 0.0:
        return FerriteEffect(0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, "no ferrite")
    record = material("ferrites", sheet.material)
    mu_prime = sheet.mu_prime_override or midpoint(record["mu_prime"])
    mu_double = sheet.mu_double_prime_override or midpoint(record["mu_double_prime"])
    tan_mu = mu_double / mu_prime
    saturation = 1.0 - exp(-(sheet.thickness_mm / 0.25) * sqrt(mu_prime / 100.0))
    coverage = min(1.0, max(0.0, sheet.coverage_fraction))
    orientation = {"behind": 1.0, "partial_overlap": 0.75, "beside": 0.30, "steel_frame_side": 0.20}.get(
        metal_position, 0.6)
    isolation = min(0.82, 0.78 * saturation * coverage * orientation / (1.0 + 4.0 * tan_mu))
    front_field_factor = 1.0 + 0.08 * saturation * coverage
    inductance_ratio = 1.0 + 0.12 * saturation * coverage
    q_ratio = 1.0 / (1.0 + 0.8 * tan_mu * saturation * coverage)
    uncertainty_db = 0.3 + 0.8 * saturation
    return FerriteEffect(isolation, front_field_factor, inductance_ratio, q_ratio,
                         mu_prime, mu_double, tan_mu, uncertainty_db,
                         "finite-thickness saturation prior anchored to NFC-sheet mu'/mu''; calibrate by VNA")
