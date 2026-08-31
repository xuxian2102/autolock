"""Versioned material database with explicit ranges and provenance."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from importlib.resources import files
from math import exp, pi, sqrt

from .coil import EPS0, MU0


@lru_cache(maxsize=1)
def load_database() -> dict:
    path = files("nfc_model").joinpath("data/materials.json")
    return json.loads(path.read_text(encoding="utf-8"))


def midpoint(bounds: list[float] | tuple[float, float]) -> float:
    return (float(bounds[0]) + float(bounds[1])) / 2.0


def material(category: str, name: str) -> dict:
    database = load_database()
    try:
        return database[category][name]
    except KeyError as exc:
        choices = ", ".join(database.get(category, {}))
        raise KeyError(f"unknown {category} material {name!r}; choose {choices}") from exc


@dataclass(frozen=True)
class DoorFactor:
    amplitude: float
    loss_db: float
    epsilon_r: float
    tan_delta: float
    mu_r: float
    equivalent_loss_conductivity_s_m: float
    method: str


def door_factor(name: str, thickness_mm: float, frequency_hz: float = 13.56e6) -> DoorFactor:
    """Conservative low-loss slab term for a magnetic near-field link.

    The actual magnetic quasi-static solution for ordinary non-magnetic
    dielectrics is even closer to unity.  This small plane-wave absorption term
    is retained as an upper-bound diagnostic, never as a Friis/path-loss model.
    Distance is handled independently by the mutual-inductance solver.
    """
    record = material("door_materials", name)
    epsilon_r = midpoint(record["epsilon_r"])
    loss_tangent = midpoint(record["tan_delta"])
    mu_r = midpoint(record["mu_r"])
    omega = 2.0 * pi * frequency_hz
    sigma_loss = omega * EPS0 * epsilon_r * loss_tangent
    alpha = 0.5 * omega * sqrt(MU0 * mu_r * EPS0 * epsilon_r) * loss_tangent
    absorption = exp(-alpha * thickness_mm * 1e-3)
    interface = 4.0 * mu_r / (1.0 + mu_r) ** 2
    amplitude = absorption * interface
    loss_db = 20.0 * __import__("math").log10(max(amplitude, 1e-15))
    return DoorFactor(amplitude, loss_db, epsilon_r, loss_tangent, mu_r, sigma_loss,
                      "quasi-static mu interface with conservative low-loss slab absorption bound")
