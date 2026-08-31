"""Top-level reduced-order NFC link simulation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from math import log10, pi, sqrt
from pathlib import Path

import numpy as np

from .coil import MU0, estimate_electrical, estimate_inductance, magnetic_field
from .coupling import coupling_coefficient, mutual_inductance
from .ferrite import FerriteEffect, ferrite_effect
from .geometry import phone_case
from .kicad_extract import ExtractedRevA, extract_rev_a
from .matching import MatchingResult, installed_matching
from .materials import DoorFactor, door_factor
from .metal import MetalEffect, metal_effect
from .scenarios import Scenario


def db20(value: float) -> float:
    return 20.0 * log10(max(abs(value), 1e-15))


@dataclass(frozen=True)
class SimulationResult:
    scenario: dict
    geometry: dict
    antenna_air: dict
    antenna_installed: dict
    receiver: dict
    environment: dict
    field: dict
    coupling: dict
    matching: dict
    margin: dict
    risks: list[str]
    limitations: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _aggregate_environment(
    scenario: Scenario,
    extracted: ExtractedRevA,
) -> tuple[float, float, float, list[dict], FerriteEffect, float]:
    coil = extracted.coil
    representative_position = scenario.metals[0].position if scenario.metals else "behind"
    ferrite = ferrite_effect(scenario.ferrite, metal_position=representative_position)
    total_field = ferrite.front_field_factor
    total_l = ferrite.inductance_ratio
    total_q = ferrite.q_ratio
    uncertainty_sq = ferrite.uncertainty_db ** 2
    effects = []
    for metal in scenario.metals:
        local_ferrite = ferrite_effect(scenario.ferrite, metal_position=metal.position)
        effect = metal_effect(
            metal,
            coil_width_mm=coil.outer_width_mm + coil.trace_width_mm,
            coil_height_mm=coil.outer_height_mm + coil.trace_width_mm,
            ferrite_isolation=local_ferrite.metal_isolation,
            coefficient_scale=scenario.metal_coefficient_scale,
        )
        total_field *= effect.field_factor
        total_l *= effect.inductance_ratio
        total_q *= effect.q_ratio
        uncertainty_sq += effect.uncertainty_db ** 2
        effects.append({"object": metal.to_dict(), "effect": asdict(effect),
                        "ferrite_isolation_for_object": local_ferrite.metal_isolation})
    return total_field, total_l, total_q, effects, ferrite, sqrt(uncertainty_sq)


def _risks(scenario: Scenario, matching: MatchingResult, metal_effects: list[dict],
           coupling_loss_db: float, uncertainty_db: float) -> list[str]:
    candidates: list[tuple[float, str]] = []
    candidates.append((abs(coupling_loss_db), f"total separation/alignment: {coupling_loss_db:.1f} dB coupling vs 2 mm free-air reference"))
    for item in metal_effects:
        effect = item["effect"]
        obj = item["object"]
        candidates.append((abs(effect["field_loss_db"]),
                           f"{obj['position']} {obj['material']} at {obj['distance_mm']:.1f} mm: prior field term {effect['field_loss_db']:.1f} dB"))
    candidates.append((abs(matching.detune_loss_db),
                       f"installed resonance detuning: {matching.detune_loss_db:.1f} dB first-order response penalty"))
    if scenario.ferrite.thickness_mm <= 0 and scenario.metals:
        candidates.append((2.0, "no ferrite between antenna and nearby metal"))
    if uncertainty_db > 2.0:
        candidates.append((uncertainty_db, f"uncalibrated metal/ferrite prior: approximately ±{uncertainty_db:.1f} dB model uncertainty"))
    return [text for _, text in sorted(candidates, reverse=True)[:5]]


@lru_cache(maxsize=24)
def _reference_values(source_key: str | None, receiver_name: str, quadrature_order: int) -> tuple[float, float]:
    extracted = extract_rev_a(source_key)
    tx = extracted.coil
    rx = phone_case(receiver_name)
    mutual = mutual_inductance(tx, rx, separation_mm=2.0, quadrature_order=quadrature_order)
    h = float(np.linalg.norm(magnetic_field(tx, (0.0, 0.0, 2.0),
                                              quadrature_order=max(5, quadrature_order))) / MU0)
    return mutual, h


def simulate(scenario: Scenario, *, source: str | Path | None = None, quadrature_order: int = 4,
             include_field: bool = True) -> SimulationResult:
    extracted = extract_rev_a(source)
    tx = extracted.coil
    rx = phone_case(scenario.phone_case)
    air = estimate_electrical(tx)
    receiver_l = estimate_inductance(rx)
    separation = scenario.total_separation_mm
    geometry_args = dict(
        separation_mm=max(separation, 0.2),
        offset_x_mm=scenario.offset_x_mm,
        offset_y_mm=scenario.offset_y_mm,
        tilt_x_deg=scenario.tilt_x_deg,
        tilt_y_deg=scenario.tilt_y_deg,
        quadrature_order=quadrature_order,
    )
    mutual_geometry = mutual_inductance(tx, rx, **geometry_args)
    source_key = str(source) if source is not None else None
    mutual_reference, h_reference = _reference_values(source_key, scenario.phone_case, quadrature_order)
    k_geometry = coupling_coefficient(mutual_geometry, air.inductance_h, receiver_l)
    k_reference = coupling_coefficient(mutual_reference, air.inductance_h, receiver_l)

    door = door_factor(scenario.door_material, scenario.door_thickness_mm)
    env_field, l_ratio, q_ratio, metal_effects, ferrite, uncertainty_db = _aggregate_environment(scenario, extracted)
    environmental_amplitude = door.amplitude * env_field
    mutual_installed = mutual_geometry * environmental_amplitude
    l_installed = air.inductance_h * l_ratio * (1.0 + scenario.l_tolerance_fraction)
    # Translate modeled Q degradation into an equivalent antenna series R.
    q_installed_bare = max(1.0, air.q_air * q_ratio * scenario.q_multiplier)
    r_installed = 2.0 * pi * 13.56e6 * l_installed / q_installed_bare
    k_installed = coupling_coefficient(mutual_installed, l_installed, receiver_l)
    matching = installed_matching(
        inductance_air_h=air.inductance_h,
        inductance_installed_h=l_installed,
        resistance_installed_ohm=r_installed,
        l_tolerance_fraction=0.0,
        capacitance_tolerance_fraction=scenario.capacitance_tolerance_fraction,
    )
    reference_loaded_q = 2.0 * pi * 13.56e6 * air.inductance_h / (air.resistance_ac_ohm + 6.08)
    q_energy_factor = sqrt(max(matching.installed_loaded_q, 1e-9) / reference_loaded_q)
    k_ratio = k_installed / k_reference
    link_ratio = k_ratio * matching.detune_amplitude * q_energy_factor

    phone_center = (scenario.offset_x_mm, scenario.offset_y_mm, max(separation, 0.2))
    if include_field:
        b_geometry = magnetic_field(tx, phone_center, quadrature_order=max(5, quadrature_order))
        h_geometry = float(np.linalg.norm(b_geometry) / MU0)
    else:
        h_geometry = float("nan")
    h_installed = h_geometry * environmental_amplitude

    coupling_loss = db20(k_ratio)
    limitations = [
        "Phone-S/M/L are generic equivalent loops, not measured iPhone antenna geometries.",
        "No absolute HomeKey receiver threshold or success probability is asserted.",
        "Metal/ferrite field, L, and Q terms are bounded priors until installed VNA/EM calibration.",
        "The real door core, moisture, hidden reinforcement, and lock geometry are unknown.",
        "The KiCad file omits copper stackup thickness; 35 um nominal with 18-35 um uncertainty is assumed.",
    ]
    return SimulationResult(
        scenario=scenario.to_dict(),
        geometry={
            "total_coil_separation_mm": separation,
            "phone_gap_mm": scenario.phone_gap_mm,
            "door_thickness_mm": scenario.door_thickness_mm,
            "offset_x_mm": scenario.offset_x_mm,
            "offset_y_mm": scenario.offset_y_mm,
            "tilt_x_deg": scenario.tilt_x_deg,
            "tilt_y_deg": scenario.tilt_y_deg,
        },
        antenna_air={
            "L_uH": air.inductance_h * 1e6,
            "R_dc_ohm": air.resistance_dc_ohm,
            "R_ac_ohm": air.resistance_ac_ohm,
            "Q_bare": air.q_air,
            "Q_loaded_reference": reference_loaded_q,
            "skin_depth_copper_um": air.skin_depth_m * 1e6,
            "self_resonance_estimate_MHz": air.self_resonance_estimate_hz / 1e6 if air.self_resonance_estimate_hz else None,
            "method": air.method,
        },
        antenna_installed={
            "L_uH": l_installed * 1e6,
            "L_ratio": l_installed / air.inductance_h,
            "R_ac_equivalent_ohm": r_installed,
            "Q_bare_equivalent": q_installed_bare,
            "Q_loaded": matching.installed_loaded_q,
        },
        receiver={
            "case": rx.name,
            "equivalent_dimensions_mm": list(rx.turn_sizes_mm[0]),
            "L_estimate_uH": receiver_l * 1e6,
            "status": rx.metadata["status"],
        },
        environment={
            "door": asdict(door),
            "ferrite": asdict(ferrite),
            "metal_effects": metal_effects,
            "combined_field_factor": environmental_amplitude,
            "combined_L_ratio": l_ratio,
            "combined_Q_ratio": q_ratio,
            "prior_uncertainty_db_1sigma_approx": uncertainty_db,
        },
        field={
            "H_geometry_A_per_m_per_A": h_geometry,
            "H_installed_A_per_m_per_A": h_installed,
            "H_reference_A_per_m_per_A": h_reference,
            "H_over_reference": h_installed / h_reference if include_field else None,
            "field_loss_db": db20(h_installed / h_reference) if include_field else None,
            "note": "magnetic near field per 1 A coil current; not far-field path loss",
        },
        coupling={
            "M_geometry_nH": mutual_geometry * 1e9,
            "M_installed_nH": mutual_installed * 1e9,
            "M_reference_nH": mutual_reference * 1e9,
            "k_geometry": k_geometry,
            "k_installed": k_installed,
            "k_reference": k_reference,
            "relative_coupling": k_ratio,
            "coupling_loss_db": coupling_loss,
            "note": "20log10(k/k_reference), not Friis or a far-field RF path loss",
        },
        matching=matching.to_dict(),
        margin={
            "reference": "same phone equivalent, centered parallel at 2 mm in free air",
            "coupling_only_margin_db": coupling_loss,
            "detune_penalty_db": matching.detune_loss_db,
            "loaded_Q_energy_term_db": db20(q_energy_factor),
            "nfc_margin_proxy_db": db20(link_ratio),
            "absolute_success_threshold": None,
            "success_probability": None,
            "interpretation": "relative engineering proxy only; calibrate threshold with repeated HomeKey trials",
        },
        risks=_risks(scenario, matching, metal_effects, coupling_loss, uncertainty_db),
        limitations=limitations,
    )
