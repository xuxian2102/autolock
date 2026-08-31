"""Command-line entry point for the Autolock NFC model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .calibration import calibrate_vna, fit_homekey_threshold
from .ferrite import FerriteSheet
from .kicad_extract import extract_rev_a
from .metal import MetalObject
from .model import simulate
from .monte_carlo import run_monte_carlo
from .scenarios import get_scenario, load_scenario, standard_scenario_names


DOOR_ALIASES = {
    "zetland-solid-core": "fire_rated_composite",
    "solid-core": "fire_rated_composite",
    "timber": "solid_timber",
}


def _json(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _scenario_from_args(args) -> object:
    scenario = load_scenario(args.scenario_file) if args.scenario_file else get_scenario(args.scenario)
    updates = {}
    if args.door:
        updates["door_material"] = DOOR_ALIASES.get(args.door, args.door)
    if args.door_thickness is not None:
        updates["door_thickness_mm"] = args.door_thickness
    if args.phone_case:
        updates["phone_case"] = args.phone_case
    if args.phone_gap is not None:
        updates["phone_gap_mm"] = args.phone_gap
    if args.phone_offset is not None:
        updates["offset_x_mm"] = args.phone_offset
    if args.phone_angle is not None:
        updates["tilt_y_deg"] = args.phone_angle
    metals = list(scenario.metals)
    if args.steel_lock or args.lock_distance is not None:
        lock_distance = args.lock_distance if args.lock_distance is not None else 10.0
        lock = MetalObject(distance_mm=lock_distance, position=args.metal_position)
        metals = [metal for metal in metals if metal.position == "steel_frame_side"] + [lock]
        updates["metals"] = tuple(metals)
    if args.no_metal:
        updates["metals"] = ()
    if args.ferrite is not None:
        updates["ferrite"] = FerriteSheet(material=args.ferrite_material,
                                           thickness_mm=args.ferrite,
                                           coverage_fraction=1.0)
    return scenario.with_updates(**updates)


def _add_scenario_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scenario", default="ZETLAND_BASELINE", choices=standard_scenario_names())
    parser.add_argument("--scenario-file", help="JSON or YAML scenario file")
    parser.add_argument("--source", help="review ZIP or .kicad_pcb; auto-detected by default")
    parser.add_argument("--door")
    parser.add_argument("--door-thickness", type=float)
    parser.add_argument("--phone-case", choices=["Phone-S", "Phone-M", "Phone-L"])
    parser.add_argument("--phone-gap", type=float)
    parser.add_argument("--phone-offset", type=float, help="lateral x offset in mm")
    parser.add_argument("--phone-angle", type=float, help="tilt about y in degrees")
    parser.add_argument("--steel-lock", action="store_true")
    parser.add_argument("--lock-distance", type=float, help="shortest antenna-to-lock metal gap in mm; implies a lock")
    parser.add_argument("--metal-position", default="partial_overlap",
                        choices=["behind", "partial_overlap", "beside", "steel_frame_side"])
    parser.add_argument("--no-metal", action="store_true")
    parser.add_argument("--ferrite", type=float, help="ferrite thickness in mm; 0 disables it")
    parser.add_argument("--ferrite-material", default="medium", choices=["low", "medium", "high"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nfc_model",
                                     description="Autolock 13.56 MHz reduced-order NFC model")
    sub = parser.add_subparsers(dest="command", required=True)
    simulate_parser = sub.add_parser("simulate", help="simulate one scenario")
    _add_scenario_args(simulate_parser)
    mc_parser = sub.add_parser("monte-carlo", help="propagate installation/manufacturing uncertainty")
    _add_scenario_args(mc_parser)
    mc_parser.add_argument("--samples", type=int, default=1000)
    mc_parser.add_argument("--seed", type=int, default=7161)
    mc_parser.add_argument("--include-samples", action="store_true")
    extract_parser = sub.add_parser("extract-antenna", help="extract Rev A geometry from KiCad")
    extract_parser.add_argument("--source")
    vna = sub.add_parser("calibrate-vna", help="normalize VNA summary measurements")
    vna.add_argument("input")
    homekey = sub.add_parser("calibrate-homekey", help="fit a success threshold from measured trials")
    homekey.add_argument("input")
    report = sub.add_parser("generate-report", help="regenerate report, tables, and plots")
    report.add_argument("--source")
    report.add_argument("--samples", type=int, default=1200)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "extract-antenna":
        _json(extract_rev_a(args.source).to_dict())
    elif args.command == "simulate":
        _json(simulate(_scenario_from_args(args), source=args.source).to_dict())
    elif args.command == "monte-carlo":
        result = run_monte_carlo(_scenario_from_args(args), n=args.samples, seed=args.seed,
                                 source=args.source, include_samples=args.include_samples)
        _json(result.to_dict(include_samples=args.include_samples))
    elif args.command == "calibrate-vna":
        _json(calibrate_vna(args.input))
    elif args.command == "calibrate-homekey":
        _json(fit_homekey_threshold(args.input))
    elif args.command == "generate-report":
        from .report import generate_report
        _json(generate_report(source=args.source, monte_carlo_samples=args.samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
