"""Uncertainty propagation and rank-based global sensitivity analysis."""

from __future__ import annotations

from dataclasses import dataclass
from math import isnan
from pathlib import Path

import numpy as np

from .ferrite import FerriteSheet
from .metal import MetalObject
from .model import simulate
from .scenarios import Scenario


@dataclass(frozen=True)
class MonteCarloResult:
    summary: dict
    sensitivity: list[dict]
    samples: list[dict]

    def to_dict(self, include_samples: bool = False) -> dict:
        result = {"summary": self.summary, "sensitivity": self.sensitivity}
        if include_samples:
            result["samples"] = self.samples
        return result


def _rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    return ranks


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    xr, yr = _rank(x), _rank(y)
    if np.std(xr) == 0 or np.std(yr) == 0:
        return 0.0
    return float(np.corrcoef(xr, yr)[0, 1])


def _percentiles(values: np.ndarray) -> dict:
    return {
        "median": float(np.percentile(values, 50)),
        "P10": float(np.percentile(values, 10)),
        "P90": float(np.percentile(values, 90)),
        "worst_reasonable_P1": float(np.percentile(values, 1)),
        "minimum_observed": float(np.min(values)),
        "maximum_observed": float(np.max(values)),
    }


def run_monte_carlo(
    base: Scenario,
    *,
    n: int = 1000,
    seed: int = 7161,
    source: str | Path | None = None,
    include_samples: bool = False,
) -> MonteCarloResult:
    if n < 20:
        raise ValueError("Monte Carlo requires at least 20 samples")
    rng = np.random.default_rng(seed)
    records: list[dict] = []
    for _ in range(n):
        door_thickness = float(rng.triangular(30.0, 40.0, 50.0))
        lock_gap = float(rng.triangular(2.0, 10.0, 50.0))
        steel_area_scale = float(rng.triangular(0.55, 1.0, 1.6))
        ferrite_thickness = float(np.clip(rng.normal(max(base.ferrite.thickness_mm, 0.5), 0.10), 0.0, 1.0))
        ferrite_mu = float(np.clip(rng.normal(97.5, 25.0), 35.0, 180.0))
        offset_x = float(np.clip(rng.normal(base.offset_x_mm, 10.0), -30.0, 30.0))
        offset_y = float(np.clip(rng.normal(base.offset_y_mm, 10.0), -30.0, 30.0))
        angle = float(np.clip(abs(rng.normal(max(abs(base.tilt_y_deg), 8.0), 8.0)), 0.0, 35.0))
        l_tolerance = float(np.clip(rng.normal(0.0, 0.05), -0.12, 0.12))
        q_multiplier = float(np.clip(rng.normal(1.0, 0.15), 0.6, 1.4))
        cap_tolerance = float(np.clip(rng.normal(0.0, 0.02), -0.06, 0.06))
        coefficient_scale = float(rng.triangular(0.65, 1.0, 1.55))

        metals = []
        for metal in base.metals:
            distance = lock_gap if metal.position in {"behind", "partial_overlap", "beside"} else metal.distance_mm
            metals.append(MetalObject(**{**metal.to_dict(), "distance_mm": distance, "area_scale": steel_area_scale}))
        if not metals:
            metals = [MetalObject(distance_mm=lock_gap, area_scale=steel_area_scale)]
        ferrite = FerriteSheet(**{**base.ferrite.to_dict(), "thickness_mm": ferrite_thickness,
                                  "mu_prime_override": ferrite_mu,
                                  "mu_double_prime_override": ferrite_mu * 0.03})
        scenario = base.with_updates(
            door_thickness_mm=door_thickness,
            metals=tuple(metals),
            ferrite=ferrite,
            offset_x_mm=offset_x,
            offset_y_mm=offset_y,
            tilt_y_deg=angle,
            l_tolerance_fraction=l_tolerance,
            capacitance_tolerance_fraction=cap_tolerance,
            q_multiplier=q_multiplier,
            metal_coefficient_scale=coefficient_scale,
        )
        result = simulate(scenario, source=source, quadrature_order=3, include_field=False)
        records.append({
            "door_thickness_mm": door_thickness,
            "lock_distance_mm": lock_gap,
            "steel_area_scale": steel_area_scale,
            "ferrite_thickness_mm": ferrite_thickness,
            "ferrite_mu_prime": ferrite_mu,
            "phone_offset_mm": float((offset_x ** 2 + offset_y ** 2) ** 0.5),
            "phone_angle_deg": angle,
            "antenna_L_tolerance_fraction": l_tolerance,
            "Q_multiplier": q_multiplier,
            "matching_C_tolerance_fraction": cap_tolerance,
            "metal_prior_scale": coefficient_scale,
            "coupling_margin_db": result.margin["coupling_only_margin_db"],
            "nfc_margin_proxy_db": result.margin["nfc_margin_proxy_db"],
            "installed_L_uH": result.antenna_installed["L_uH"],
            "installed_Q_loaded": result.antenna_installed["Q_loaded"],
            "installed_f0_MHz": result.matching["f0_installed_hz"] / 1e6,
        })

    outputs = {key: np.asarray([row[key] for row in records], dtype=float)
               for key in ("coupling_margin_db", "nfc_margin_proxy_db", "installed_L_uH",
                           "installed_Q_loaded", "installed_f0_MHz")}
    inputs = [key for key in records[0] if key not in outputs]
    target = outputs["nfc_margin_proxy_db"]
    sensitivity = []
    for name in inputs:
        rho = _spearman(np.asarray([row[name] for row in records], dtype=float), target)
        sensitivity.append({"variable": name, "spearman_rho": rho, "importance_abs_rho": abs(rho)})
    sensitivity.sort(key=lambda item: item["importance_abs_rho"], reverse=True)
    summary = {name: _percentiles(values) for name, values in outputs.items()}
    summary.update({"samples": n, "seed": seed,
                    "worst_reasonable_definition": "P1 of the stated engineering-prior distribution, not an absolute bound"})
    return MonteCarloResult(summary, sensitivity, records if include_samples else [])
