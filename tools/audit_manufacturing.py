#!/usr/bin/env python3
"""Cross-check final fabrication outputs against the KiCad source board."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import numpy  # noqa: F401


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
WORKSPACE = ROOT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKSPACE / ".tools" / "py"))

from audit_board import reference_of  # noqa: E402
from design_data import BOARD_SIZE, PROJECT_NAME, parts  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.items.brditems import Via  # noqa: E402


GERBER_DIR = HARDWARE / "production" / "gerbers"
ASSEMBLY_DIR = HARDWARE / "production" / "assembly"
MANIFEST = ROOT / "reports" / "MANUFACTURING_EXPORT.json"
REPORT = ROOT / "reports" / "MANUFACTURING_AUDIT.txt"


def csv_refs(path, column):
    result = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            result.update(ref for ref in row[column].split(",") if ref)
    return result


def main():
    board = Board.from_file(str(BOARD_PATH))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    errors = []
    warnings = []

    required = {
        f"{PROJECT_NAME}.GTL", f"{PROJECT_NAME}.G2", f"{PROJECT_NAME}.G3", f"{PROJECT_NAME}.GBL",
        f"{PROJECT_NAME}.GTS", f"{PROJECT_NAME}.GBS", f"{PROJECT_NAME}.GTP", f"{PROJECT_NAME}.GBP",
        f"{PROJECT_NAME}.GTO", f"{PROJECT_NAME}.GBO", f"{PROJECT_NAME}.GKO",
        f"{PROJECT_NAME}-PTH.drl", f"{PROJECT_NAME}-NPTH.drl",
    }
    actual = {path.name for path in GERBER_DIR.iterdir() if path.is_file()}
    if actual != required:
        errors.append(f"Fabrication file set mismatch: missing={sorted(required-actual)} extra={sorted(actual-required)}")

    for name in sorted(required):
        path = GERBER_DIR / name
        if not path.exists() or path.stat().st_size < 40:
            errors.append(f"Missing/empty fabrication file: {name}")
            continue
        text = path.read_text(encoding="ascii")
        if name.endswith((".drl",)):
            if not text.rstrip().endswith("M30") or "METRIC" not in text:
                errors.append(f"Invalid Excellon framing/units: {name}")
        else:
            if not text.rstrip().endswith("M02*") or "%MOMM*%" not in text or "TF.FileFunction" not in text:
                errors.append(f"Invalid Gerber X2 framing/attributes: {name}")

    via_count = sum(isinstance(item, Via) for item in board.traceItems)
    plated_round = via_count
    plated_slots = 0
    nonplated_round = 0
    nonplated_slots = 0
    for footprint in board.footprints:
        for pad in footprint.pads:
            if pad.drill is None:
                continue
            is_slot = bool(getattr(pad.drill, "oval", False) and pad.drill.width)
            if pad.type == "np_thru_hole":
                nonplated_slots += int(is_slot)
                nonplated_round += int(not is_slot)
            else:
                plated_slots += int(is_slot)
                plated_round += int(not is_slot)
    exported = manifest["gerber_and_drill"]
    pth = exported[f"{PROJECT_NAME}-PTH.drl"]
    npth = exported[f"{PROJECT_NAME}-NPTH.drl"]
    if (pth["round_hits"], pth["slots"]) != (plated_round, plated_slots):
        errors.append(f"PTH count mismatch source={(plated_round, plated_slots)} export={(pth['round_hits'], pth['slots'])}")
    if (npth["round_hits"], npth["slots"]) != (nonplated_round, nonplated_slots):
        errors.append(f"NPTH count mismatch source={(nonplated_round, nonplated_slots)} export={(npth['round_hits'], npth['slots'])}")

    bom_refs = csv_refs(ASSEMBLY_DIR / "BOM_JLCPCB_DRAFT.csv", "Designator")
    cpl_refs = csv_refs(ASSEMBLY_DIR / "CPL_JLCPCB_DRAFT.csv", "Designator")
    expected_bom = {
        part.ref for part in parts
        if not part.dnp and part.fields.get("ExcludeFromBOM") != "yes"
    }
    if bom_refs != expected_bom:
        errors.append(f"BOM reference mismatch missing={sorted(expected_bom-bom_refs)} extra={sorted(bom_refs-expected_bom)}")
    if not cpl_refs <= bom_refs:
        errors.append(f"CPL contains refs absent from BOM: {sorted(cpl_refs-bom_refs)}")

    unresolved = manifest["assembly"]["unresolved_lcsc_refs"]
    if unresolved:
        warnings.append(f"JLC LCSC selection unresolved for {len(unresolved)} refs: {', '.join(unresolved)}")
    exporter = manifest.get("fabrication_exporter", "fallback")
    if exporter.startswith("KiCad official pcb export"):
        warnings.append("Official KiCad fabrication output still requires JLC online layer preview before payment")
    else:
        warnings.append("Fallback Gerber omits physical silkscreen text and legacy silk arcs; assembly PNG retains references")
        warnings.append("Deterministic fallback Gerbers require replacement by official KiCad plots before ordering")

    lines = [
        f"{PROJECT_NAME} MANUFACTURING EXPORT AUDIT",
        "=" * 72,
        f"Board source: {BOARD_PATH.relative_to(ROOT)}",
        f"Board outline: {BOARD_SIZE[0]:.1f} x {BOARD_SIZE[1]:.1f} mm",
        f"Fabrication files: {len(actual)} / {len(required)} required",
        f"PTH: {plated_round} round + {plated_slots} slots",
        f"NPTH: {nonplated_round} round + {nonplated_slots} slots",
        f"BOM active refs: {len(bom_refs)}",
        f"CPL top-side refs: {len(cpl_refs)}",
        f"Errors: {len(errors)}",
        f"Warnings: {len(warnings)}",
        "",
    ]
    if errors:
        lines.append("ERRORS")
        lines.extend(f" - {error}" for error in errors)
        lines.append("")
    lines.append("OPEN ITEMS / LIMITATIONS")
    lines.extend(f" - {warning}" for warning in warnings)
    lines.extend(["", "RESULT: PASS (export integrity)" if not errors else "RESULT: FAIL"])
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))
    if errors:
        raise SystemExit(2)
    print("Manufacturing export integrity: PASS")


if __name__ == "__main__":
    main()
