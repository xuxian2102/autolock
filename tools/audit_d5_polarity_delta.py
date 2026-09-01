#!/usr/bin/env python3
"""Prove that the D5 repair is the single intended delta from 20/21 freeze."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HARDWARE = ROOT / "hardware"
REPORT_JSON = ROOT / "reports" / "D5_POLARITY_DELTA_AUDIT.json"
REPORT_MD = ROOT / "reports" / "D5_POLARITY_DELTA_AUDIT.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_exact(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise AssertionError(f"{label}: expected one baseline occurrence, found {count}")
    return text.replace(old, new, 1)


def transform_board(text: str) -> str:
    changes = (
        ('  (net 16 "GND")\n  (net 17 "LED_K")\n  (net 18 "PMOS_GATE")',
         '  (net 16 "GND")\n  (net 17 "LED_A")\n  (net 18 "PMOS_GATE")', "net 17 name"),
        ('    (at 145 21 90)\n    (descr "LED SMD 0603', '    (at 145 21 270)\n    (descr "LED SMD 0603', "D5 placement"),
        ('(fp_text reference "D5" (at 0 -1.43 90.0)', '(fp_text reference "D5" (at 0 -1.43 270.0)', "D5 reference angle"),
        ('(fp_text value "Green LED" (at 0 1.43 90.0)', '(fp_text value "Green LED" (at 0 1.43 270.0)', "D5 value angle"),
        ('(fp_text user "${REFERENCE}" (at 0 0 90.0) (layer "F.Fab")\n      (effects (font (size 0.4 0.4) (thickness 0.06)))\n      (tstamp 13d2eba6-7e4c-4875-a7c3-6bc6f64ee42b)',
         '(fp_text user "${REFERENCE}" (at 0 0 270.0) (layer "F.Fab")\n      (effects (font (size 0.4 0.4) (thickness 0.06)))\n      (tstamp 13d2eba6-7e4c-4875-a7c3-6bc6f64ee42b)', "D5 fab reference angle"),
        ('(pad "1" smd roundrect (at -0.7875 0 90.0)', '(pad "1" smd roundrect (at -0.7875 0 270.0)', "D5 pad 1 angle"),
        ('(net 17 "LED_K") (tstamp b2236c44-8676-4108-bcbb-4d0ed394446a)', '(net 16 "GND") (tstamp b2236c44-8676-4108-bcbb-4d0ed394446a)', "D5 pad 1 net"),
        ('(pad "2" smd roundrect (at 0.7875 0 90.0)', '(pad "2" smd roundrect (at 0.7875 0 270.0)', "D5 pad 2 angle"),
        ('(net 16 "GND") (tstamp f0085fa9-9413-4e7f-a578-d79e3f522592)', '(net 17 "LED_A") (tstamp f0085fa9-9413-4e7f-a578-d79e3f522592)', "D5 pad 2 net"),
        ('(net 17 "LED_K") (tstamp 0997623b-f7fb-417d-b2cf-98d0a862357e)', '(net 17 "LED_A") (tstamp 0997623b-f7fb-417d-b2cf-98d0a862357e)', "R9 pad 2 net name"),
    )
    for old, new, label in changes:
        text = replace_exact(text, old, new, label)
    return text


def transform_schematic(text: str) -> str:
    replacements = (
        ('(global_label "LED_K" (shape passive) (at 509.38000000000005 135 180)', '(global_label "LED_A" (shape passive) (at 509.38000000000005 135 180)', "D5 net label"),
        ('(global_label "LED_K" (shape passive) (at 55 194.38 270)', '(global_label "LED_A" (shape passive) (at 55 194.38 270)', "R9 net label"),
        ('(symbol (lib_id "Device:LED_Small") (at 517 135 0)', '(symbol (lib_id "Device:LED_Small") (at 517 135 180)', "D5 symbol rotation"),
        ('(property "LCSC" "" (id 4) (at 517 135 0)', '(property "LCSC" "C12624" (id 4) (at 517 135 0)', "D5 LCSC"),
        ('(property "MPN" "" (id 5) (at 517 135 0)', '(property "MPN" "KT-0603G" (id 5) (at 517 135 0)', "D5 MPN"),
        ('(property "Manufacturer" "" (id 6) (at 517 135 0)', '(property "Manufacturer" "Hubei KENTO Elec" (id 6) (at 517 135 0)', "D5 manufacturer"),
    )
    for old, new, label in replacements:
        text = replace_exact(text, old, new, label)
    return text


def load_design_data(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def pad_center(parent_x: float, parent_y: float, angle: float, local_x: float, local_y: float):
    radians = math.radians(angle)
    return (
        round(parent_x + local_x * math.cos(radians) - local_y * math.sin(radians), 6),
        round(parent_y + local_x * math.sin(radians) + local_y * math.cos(radians), 6),
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: audit_d5_polarity_delta.py BASELINE.tar.gz")
    archive = Path(sys.argv[1]).resolve()
    if not archive.is_file():
        raise FileNotFoundError(archive)

    errors: list[str] = []
    checks: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="rev-a-d5-baseline-") as temp_dir:
        temp = Path(temp_dir)
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if any(member.name.startswith("/") or ".." in Path(member.name).parts for member in members):
                raise RuntimeError("unsafe baseline archive path")
            bundle.extractall(temp, filter="data")
        baseline = temp / ROOT.name

        board_rel = Path("kicad/HomeKey-Lock-RevA-PN7161.kicad_pcb")
        old_board = (baseline / board_rel).read_text(encoding="utf-8")
        new_board = (HARDWARE / board_rel).read_text(encoding="utf-8")
        board_exact = transform_board(old_board) == new_board
        checks["pcb_exact_expected_transform"] = board_exact
        if not board_exact:
            errors.append("PCB differs outside the exact approved D5 transform")

        old_tracks = [line for line in old_board.splitlines() if line.lstrip().startswith(("(segment ", "(via "))]
        new_tracks = [line for line in new_board.splitlines() if line.lstrip().startswith(("(segment ", "(via "))]
        checks["all_segment_and_via_records_byte_identical"] = old_tracks == new_tracks
        checks["segment_and_via_record_count"] = len(new_tracks)
        if old_tracks != new_tracks:
            errors.append("segment or via record changed")

        schematic_rel = Path("kicad/schematics/02_MCU_IO.kicad_sch")
        old_schematic = (baseline / schematic_rel).read_text(encoding="utf-8")
        new_schematic = (HARDWARE / schematic_rel).read_text(encoding="utf-8")
        schematic_exact = transform_schematic(old_schematic) == new_schematic
        checks["mcu_schematic_exact_expected_transform"] = schematic_exact
        if not schematic_exact:
            errors.append("02_MCU_IO schematic differs outside the exact approved D5 transform")

        unchanged_schematics = []
        for current in sorted((HARDWARE / "kicad/schematics").glob("*.kicad_sch")):
            if current.name == schematic_rel.name:
                continue
            same = current.read_bytes() == (baseline / "kicad/schematics" / current.name).read_bytes()
            unchanged_schematics.append({"file": current.name, "byte_identical": same})
            if not same:
                errors.append(f"non-target schematic changed: {current.name}")
        checks["non_target_schematics"] = unchanged_schematics

        current_data = load_design_data(ROOT / "tools/design_data.py", "current_d5_design_data")
        d5 = current_data.part_by_ref("D5")
        r9 = current_data.part_by_ref("R9")
        contract = {
            "D5_pins": d5.pins,
            "D5_pcb_at": list(d5.pcb_at),
            "D5_LCSC": d5.lcsc,
            "D5_MPN": d5.mpn,
            "D5_manufacturer": d5.manufacturer,
            "R9_pins": r9.pins,
        }
        expected_contract = {
            "D5_pins": {"1": "GND", "2": "LED_A"},
            "D5_pcb_at": [133, 20, 270],
            "D5_LCSC": "C12624",
            "D5_MPN": "KT-0603G",
            "D5_manufacturer": "Hubei KENTO Elec",
            "R9_pins": {"1": "STATUS_LED", "2": "LED_A"},
        }
        checks["design_contract"] = contract
        checks["design_contract_matches"] = contract == expected_contract
        if contract != expected_contract:
            errors.append("design_data D5/R9 contract does not match approval")

        old_centers = {
            "pad1": pad_center(145, 21, 90, -0.7875, 0),
            "pad2": pad_center(145, 21, 90, 0.7875, 0),
        }
        new_centers = {
            "pad1": pad_center(145, 21, 270, -0.7875, 0),
            "pad2": pad_center(145, 21, 270, 0.7875, 0),
        }
        physical_swap = new_centers["pad1"] == old_centers["pad2"] and new_centers["pad2"] == old_centers["pad1"]
        checks["D5_pad_centers_before"] = old_centers
        checks["D5_pad_centers_after"] = new_centers
        checks["D5_pad_centers_swapped_in_place"] = physical_swap
        if not physical_swap:
            errors.append("D5 pad centers did not swap exactly in place")

    payload = {
        "mode": "read-only exact baseline delta audit",
        "baseline_archive": str(archive),
        "baseline_archive_sha256": sha256(archive),
        "allowed_delta": [
            "rename net 17 LED_K to LED_A",
            "rotate D5 90 to 270 degrees in place",
            "connect D5 pin 1/K to GND and pin 2/A to R9",
            "assign D5 C12624 / KT-0603G / Hubei KENTO Elec",
            "synchronize generator and procurement target list",
        ],
        "checks": checks,
        "errors": errors,
        "result": "PASS" if not errors else "FAIL",
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT_MD.write_text(
        "# D5 polarity delta audit\n\n"
        f"- Result: **{payload['result']}**\n"
        f"- Baseline SHA256: `{payload['baseline_archive_sha256']}`\n"
        f"- PCB exact approved transform: `{board_exact}`\n"
        f"- Segment/via records byte-identical: `{checks['all_segment_and_via_records_byte_identical']}`\n"
        f"- D5 pad centers swapped in place: `{physical_swap}`\n"
        f"- MCU schematic exact approved transform: `{schematic_exact}`\n"
        f"- Design contract matches: `{checks['design_contract_matches']}`\n"
        f"- Errors: `{len(errors)}`\n",
        encoding="utf-8",
    )
    print(f"D5 polarity delta audit: {payload['result']}")
    print(f"PCB exact transform: {board_exact}; tracks/vias identical: {old_tracks == new_tracks}")
    print(f"Pad centers swapped in place: {physical_swap}; errors: {len(errors)}")
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
