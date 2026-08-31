#!/usr/bin/env python3
"""Audit JLC placement rows against the authoritative KiCad board."""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy  # noqa: F401


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
WORKSPACE = ROOT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKSPACE / ".tools" / "py"))

from audit_board import absolute_point, reference_of  # noqa: E402
from design_data import footprint_for, parts  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402


CPL = HARDWARE / "production" / "assembly" / "CPL_JLCPCB_DRAFT.csv"
BOM = HARDWARE / "production" / "assembly" / "BOM_JLCPCB_DRAFT.csv"
REPORT_MD = ROOT / "reports" / "CPL_PLACEMENT_AUDIT.md"
REPORT_JSON = ROOT / "reports" / "CPL_PLACEMENT_AUDIT.json"
CRITICAL = (
    "D1", "D2", "D3", "D4", "D5", "Q1", "C44",
    "U1", "U2", "U3", "U4", "U5", "U6", "J2", "X1",
    "SW1", "SW2", "SW3",
)


def parse_mm(value: str):
    return float(value.removesuffix("mm"))


def angular_difference(first: float, second: float):
    return abs((first - second + 180.0) % 360.0 - 180.0)


def net_name(pad):
    return getattr(getattr(pad, "net", None), "name", "") or ""


def pad_summary(footprint, number: str):
    pads = [pad for pad in footprint.pads if str(pad.number) == number]
    if not pads:
        return None
    coordinates = [absolute_point(footprint, float(pad.position.X), float(pad.position.Y)) for pad in pads]
    nets = sorted({net_name(pad) for pad in pads if net_name(pad)})
    return {
        "count": len(pads),
        "coordinates_mm": [[round(x, 4), round(y, 4)] for x, y in coordinates],
        "nets": nets,
    }


def main():
    board = Board.from_file(str(BOARD_PATH))
    footprints = {reference_of(footprint): footprint for footprint in board.footprints}
    part_map = {part.ref: part for part in parts}

    with CPL.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    cpl_by_ref = {}
    errors = []
    for row in rows:
        reference = row["Designator"]
        if reference in cpl_by_ref:
            errors.append(f"Duplicate CPL row: {reference}")
        cpl_by_ref[reference] = row

    expected = set()
    for part in parts:
        if part.dnp or part.fields.get("ExcludeFromBOM") == "yes":
            continue
        footprint = footprints[part.ref]
        if any(pad.type == "smd" and "F.Cu" in pad.layers for pad in footprint.pads):
            expected.add(part.ref)
    actual = set(cpl_by_ref)
    if expected != actual:
        errors.append(f"CPL reference set mismatch missing={sorted(expected-actual)} extra={sorted(actual-expected)}")

    coordinate_failures = []
    rotation_failures = []
    layer_failures = []
    for reference in sorted(expected & actual):
        row = cpl_by_ref[reference]
        footprint = footprints[reference]
        cpl_xy = (parse_mm(row["Mid X"]), parse_mm(row["Mid Y"]))
        board_xy = (float(footprint.position.X), float(footprint.position.Y))
        if math.dist(cpl_xy, board_xy) > 0.0006:
            coordinate_failures.append(reference)
        cpl_rotation = float(row["Rotation"]) % 360.0
        board_rotation = float(footprint.position.angle or 0.0) % 360.0
        if angular_difference(cpl_rotation, board_rotation) > 0.01:
            rotation_failures.append(reference)
        if row["Layer"] != "Top" or footprint.layer != "F.Cu":
            layer_failures.append(reference)
    if coordinate_failures:
        errors.append(f"Centroid mismatch: {coordinate_failures}")
    if rotation_failures:
        errors.append(f"Rotation mismatch: {rotation_failures}")
    if layer_failures:
        errors.append(f"Layer mismatch: {layer_failures}")

    d5 = footprints["D5"]
    d5_row = cpl_by_ref["D5"]
    d5_pin1 = pad_summary(d5, "1")
    d5_pin2 = pad_summary(d5, "2")
    if float(d5_row["Rotation"]) != 270.0:
        errors.append("D5 CPL rotation is not 270 degrees")
    if d5_pin1 is None or d5_pin1["nets"] != ["GND"]:
        errors.append(f"D5 pin 1 is not cathode/GND: {d5_pin1}")
    if d5_pin2 is None or d5_pin2["nets"] != ["LED_A"]:
        errors.append(f"D5 pin 2 is not anode/LED_A: {d5_pin2}")

    critical = []
    for reference in CRITICAL:
        footprint = footprints[reference]
        row = cpl_by_ref[reference]
        part = part_map[reference]
        critical.append(
            {
                "reference": reference,
                "value": part.value,
                "lcsc": part.lcsc,
                "footprint": footprint_for(part).split(":")[-1],
                "x_mm": parse_mm(row["Mid X"]),
                "y_mm": parse_mm(row["Mid Y"]),
                "rotation_deg": float(row["Rotation"]),
                "pin1": pad_summary(footprint, "1"),
                "pin2": pad_summary(footprint, "2"),
            }
        )

    result = {
        "result": "PASS" if not errors else "FAIL",
        "board_footprints": len(footprints),
        "cpl_rows": len(rows),
        "expected_top_smt": len(expected),
        "coordinates_exact": not coordinate_failures,
        "rotations_exact": not rotation_failures,
        "all_layers_top": not layer_failures,
        "d5": {"rotation_deg": float(d5_row["Rotation"]), "pin1": d5_pin1, "pin2": d5_pin2},
        "critical": critical,
        "errors": errors,
        "limitation": "Local audit proves source/CPL consistency. JLC's library-model zero-degree orientation still requires online placement preview.",
    }
    REPORT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# CPL placement audit",
        "",
        f"- Result: **{result['result']}**",
        f"- CPL rows: `{len(rows)}/{len(expected)}` expected top-side SMT instances",
        f"- Centroids vs PCB footprint anchors: `{'PASS' if not coordinate_failures else 'FAIL'}`",
        f"- Rotations vs PCB footprint rotations: `{'PASS' if not rotation_failures else 'FAIL'}`",
        f"- Layer: `{'PASS - all Top' if not layer_failures else 'FAIL'}`",
        "- D5: `270 deg`, pin 1/K = `GND`, pin 2/A = `LED_A`",
        "",
        "## Critical placements",
        "",
        "| Ref | LCSC | X mm | Y mm | Rotation | Pin 1 net(s) |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in critical:
        pin1 = item["pin1"]
        pin1_nets = ", ".join(pin1["nets"]) if pin1 and pin1["nets"] else "n/a"
        lines.append(
            f"| {item['reference']} | {item['lcsc']} | {item['x_mm']:.3f} | {item['y_mm']:.3f} | "
            f"{item['rotation_deg']:.1f} deg | {pin1_nets} |"
        )
    lines.extend(
        [
            "",
            "## Remaining limitation",
            "",
            "This audit proves that the CPL exactly represents the reviewed KiCad footprint anchors and rotations. It cannot prove how JLCPCB's selected library model defines 0 degrees. The online PCBA preview must therefore be checked for the critical placements above before payment.",
        ]
    )
    if errors:
        lines.extend(["", "## Errors", "", *[f"- {error}" for error in errors]])
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"CPL placement audit: {result['result']} ({len(rows)}/{len(expected)} rows)")
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
