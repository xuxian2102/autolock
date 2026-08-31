#!/usr/bin/env python3
"""Regenerate an isolated board and prove reviewed U4/J2/U5 syncs remain stable."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKSPACE / ".tools" / "py"))

import generate_board as gb  # noqa: E402
import generate_schematics as gs  # noqa: E402
from audit_board import reference_of  # noqa: E402
from kiutils.board import Board  # noqa: E402
from u4_native import MARKER, validate_template  # noqa: E402


REPORT_JSON = ROOT / "reports/U4_J2_U5_SYNC_GENERATION_REGRESSION.json"
REPORT_TEXT = ROOT / "reports/U4_J2_U5_SYNC_GENERATION_REGRESSION.txt"
KICAD_ROOT = WORKSPACE / ".tools/kicad10-full-root"
KICAD_CLI = KICAD_ROOT / "usr/bin/kicad-cli"


def mismatch_references(report):
    references = []
    violations = [item for item in report.get("violations", []) if item.get("type") == "lib_footprint_mismatch"]
    for violation in violations:
        for item in violation.get("items", []):
            description = item.get("description", "")
            if description.startswith("Footprint "):
                references.append(description.removeprefix("Footprint "))
    return sorted(set(references)), len(violations)


def run():
    if not KICAD_CLI.exists():
        raise RuntimeError(f"KiCad 10 CLI not found: {KICAD_CLI}")

    with tempfile.TemporaryDirectory(prefix="u4-generation-", dir=ROOT / "reports") as temp_dir:
        temp = Path(temp_dir)
        out = temp / "kicad"
        schematic_out = out / "schematics"
        library_out = out / "HomeKey_RevA.pretty"
        board_path = out / "HomeKey-Lock-RevA-PN7161.kicad_pcb"

        # Redirect only generated destinations.  Vendor and stock sources stay
        # read-only at their normal reviewed paths.
        gs.OUT = out
        gs.SCHEMATIC_OUT = schematic_out
        gs.LIB_OUT = library_out
        gb.OUT = out
        gb.LIB_OUT = library_out
        gb.BOARD_PATH = board_path
        gb.ANTENNA_FOOTPRINT = library_out / "NFC_Antenna_40x40_4T.kicad_mod"

        gs.copy_libraries()
        gs.write_project_file()
        gb.generate()

        text = board_path.read_text(encoding="utf-8")
        native_block_count = text.count(MARKER)
        validate_template((ROOT / "design/U4_NATIVE_BOARD_BLOCK.kicad_snippet").read_text(encoding="utf-8").rstrip("\n"))
        parsed = Board.from_file(str(board_path))
        references = {reference_of(fp): fp for fp in parsed.footprints}
        u4 = references["U4"]
        j2 = references["J2"]
        u5 = references["U5"]

        drc_path = temp / "isolated-u4-generation-drc.json"
        environment = os.environ.copy()
        library_paths = [
            KICAD_ROOT / "usr/lib",
            KICAD_ROOT / "usr/lib/x86_64-linux-gnu",
        ]
        environment["LD_LIBRARY_PATH"] = ":".join(str(path) for path in library_paths)
        process = subprocess.run(
            [str(KICAD_CLI), "pcb", "drc", "--format", "json", "--output", str(drc_path), str(board_path)],
            cwd=out,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode != 0 or not drc_path.exists():
            raise RuntimeError(
                f"Isolated KiCad DRC failed ({process.returncode}):\n{process.stdout}\n{process.stderr}"
            )
        drc = json.loads(drc_path.read_text(encoding="utf-8"))
        mismatch_refs, mismatch_count = mismatch_references(drc)
        unconnected_count = len(drc.get("unconnected_items", []))

        checks = {
            "board_parses_in_current_kiutils": len(references) == 120,
            "u4_native_marker_exactly_once": native_block_count == 1,
            "u4_has_61_pads": len(u4.pads) == 61,
            "u4_has_one_four_layer_keepout": (
                len(u4.zones) == 1
                and set(u4.zones[0].layers) == {"F.Cu", "In1.Cu", "In2.Cu", "B.Cu"}
            ),
            "u4_path_preserved": u4.path == "/generated/U4",
            "j2_has_18_pads": len(j2.pads) == 18,
            "j2_has_two_npth_positioning_holes": sum(pad.type == "np_thru_hole" for pad in j2.pads) == 2,
            "j2_children_use_native_180_degree_angles": (
                all(float(pad.position.angle or 0) == 180 for pad in j2.pads)
                and all(
                    float(item.position.angle or 0) == 180
                    for item in j2.graphicItems
                    if type(item).__name__ == "FpText"
                )
            ),
            "u5_has_40_perimeter_plus_exposed_and_9_paste_pads": len(u5.pads) == 50,
            "u5_exposed_pad_has_no_full_area_paste": (
                len([pad for pad in u5.pads if str(pad.number) == "41"]) == 1
                and "F.Paste" not in [pad for pad in u5.pads if str(pad.number) == "41"][0].layers
            ),
            "u5_has_nine_nxp_0p60mm_paste_apertures": (
                sum(
                    str(pad.number) == ""
                    and set(pad.layers) == {"F.Paste"}
                    and round(float(pad.size.X), 2) == 0.60
                    and round(float(pad.size.Y), 2) == 0.60
                    for pad in u5.pads
                ) == 9
            ),
            "u5_children_use_native_board_angles": (
                all(
                    float(item.position.angle or 0) == 180
                    for item in u5.graphicItems
                    if type(item).__name__ == "FpText"
                )
                and float([pad for pad in u5.pads if str(pad.number) == "41"][0].position.angle or 0) == 180
            ),
            "official_drc_has_zero_library_mismatches": mismatch_refs == [] and mismatch_count == 0,
        }
        payload = {
            "schema_version": 1,
            "mode": "isolated clean regeneration",
            "kicad_version": drc.get("kicad_version"),
            "generated_footprints": len(references),
            "generated_nets": len(parsed.nets) - 1,
            "u4_pad_count": len(u4.pads),
            "u4_zone_layers": sorted(u4.zones[0].layers),
            "j2_pad_count": len(j2.pads),
            "j2_npth_count": sum(pad.type == "np_thru_hole" for pad in j2.pads),
            "u5_pad_count": len(u5.pads),
            "official_drc_lib_footprint_mismatch_count": mismatch_count,
            "official_drc_lib_footprint_mismatch_references": mismatch_refs,
            "official_drc_unconnected_count_expected_before_routing": unconnected_count,
            "checks": checks,
            "result": "PASS" if all(checks.values()) else "FAIL",
            "note": "The isolated board is intentionally un-routed; unconnected items are expected. This gate checks regeneration and footprint-library synchronization only.",
        }

    return payload


def main():
    payload = run()
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "HOMEKEY LOCK REV A — U4/J2/U5 GENERATION-CHAIN REGRESSION",
        "=" * 76,
        f"KiCad: {payload['kicad_version']}",
        f"Generated: {payload['generated_footprints']} footprints / {payload['generated_nets']} nets",
        f"U4: {payload['u4_pad_count']} pads; keepout={','.join(payload['u4_zone_layers'])}",
        f"J2: {payload['j2_pad_count']} pads; NPTH={payload['j2_npth_count']}",
        f"U5: {payload['u5_pad_count']} pads including 9 stencil-only apertures",
        f"Official library mismatches: {payload['official_drc_lib_footprint_mismatch_count']} ({','.join(payload['official_drc_lib_footprint_mismatch_references'])})",
        f"Unconnected on intentionally un-routed board: {payload['official_drc_unconnected_count_expected_before_routing']}",
        "",
    ]
    lines.extend(f"[{'PASS' if passed else 'FAIL'}] {name}" for name, passed in payload["checks"].items())
    lines.extend(["", f"RESULT: {payload['result']}"])
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if payload["result"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
