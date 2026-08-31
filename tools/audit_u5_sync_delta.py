#!/usr/bin/env python3
"""Verify the reviewed U5 SOT618-1 change against the J2 checkpoint."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tarfile
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
WORKSPACE = ROOT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKSPACE / ".tools" / "py"))

from audit_board import reference_of  # noqa: E402
from audit_footprint_sync import clean, stable_json  # noqa: E402
from kiutils.board import Board  # noqa: E402
from u5_native import footprint_span  # noqa: E402


BASELINE = WORKSPACE / "HomeKey-Lock-RevA-PN7161-j2-sync-fixed.tar.gz"
EXPECTED_BASELINE_SHA256 = "3517f563f246f41c7e56ce6e442565d93c79b77ea65fa7ddcd5c39dab2c14c77"
PREFIX = "HomeKey-Lock-RevA-PN7161"
BOARD_REL = Path("kicad/HomeKey-Lock-RevA-PN7161.kicad_pcb")
REPORT_JSON = ROOT / "reports/U5_SYNC_DELTA_AUDIT.json"
REPORT_TEXT = ROOT / "reports/U5_SYNC_DELTA_AUDIT.txt"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def normalized(value, omit=frozenset()):
    result = clean(value, omit | {"tstamp", "renderCache"})

    def walk(item, key=None):
        if isinstance(item, dict):
            return {name: walk(child, name) for name, child in sorted(item.items())}
        if isinstance(item, list):
            values = [walk(child) for child in item]
            return sorted(values, key=stable_json) if key == "layers" else values
        return item

    return walk(result)


def strip_u5(text: str):
    start, end = footprint_span(text)
    return text[:start] + "<U5_REVIEWED_BLOCK>\n" + text[end:], text[start:end]


def copper_pad_signature(footprint, pad, native_angles):
    signature = normalized(pad, {"tstamp", "net", "locked", "pinFunction", "pinType", "layers"})
    signature["number"] = str(signature["number"])
    signature["copper_layers"] = sorted(layer for layer in pad.layers if layer.endswith(".Cu") or layer == "*.Cu")
    signature["net"] = {
        "number": int(pad.net.number if pad.net else 0),
        "name": pad.net.name if pad.net else "",
    }
    local = float(pad.position.angle or 0)
    parent = float(footprint.position.angle or 0)
    signature["physical_board_angle"] = round(local if native_angles else (parent + local) % 360, 6)
    signature["position"]["angle"] = None
    return signature


def physical_text_signatures(footprint, native_angles):
    result = []
    parent = float(footprint.position.angle or 0)
    for item in footprint.graphicItems:
        if type(item).__name__ != "FpText":
            continue
        value = normalized(item, {"tstamp"})
        local = float(item.position.angle or 0)
        value["physical_board_angle"] = round(local if native_angles else (parent + local) % 360, 6)
        value["position"]["angle"] = None
        result.append(value)
    return sorted(result, key=stable_json)


def paste_signature(footprint, pad, native_angles):
    parent = float(footprint.position.angle or 0)
    local = float(pad.position.angle or 0)
    return {
        "number": str(pad.number),
        "position": [round(float(pad.position.X), 3), round(float(pad.position.Y), 3)],
        "size": [round(float(pad.size.X), 3), round(float(pad.size.Y), 3)],
        "physical_board_angle": round(local if native_angles else (parent + local) % 360, 6),
    }


def normalize_gerber(data: bytes) -> bytes:
    data = re.sub(rb"(TF\.CreationDate,)[^*\r\n]+", rb"\1<NORMALIZED>", data)
    # Native child-angle conversion canonicalizes an equivalent 360-degree
    # X2 aperture metadata field to 0 degrees; flashed geometry is identical.
    return data.replace(b",360.0*", b",0.0*")


def add(checks, name, passed, detail):
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def run():
    checks = []
    actual_baseline_sha = sha256_file(BASELINE)
    add(checks, "reviewed J2 checkpoint identity", actual_baseline_sha == EXPECTED_BASELINE_SHA256,
        {"actual": actual_baseline_sha, "expected": EXPECTED_BASELINE_SHA256})

    with tempfile.TemporaryDirectory(prefix="u5-delta-") as temp_dir:
        temp = Path(temp_dir)
        with tarfile.open(BASELINE, "r:gz") as archive:
            names = archive.getnames()
            safe = all(name == PREFIX or name.startswith(PREFIX + "/") for name in names)
            add(checks, "baseline archive path scope", safe, {"members": len(names)})
            if not safe:
                raise RuntimeError("Unexpected baseline archive path")
            archive.extractall(temp, filter="data")

        old_root = temp / PREFIX
        old_board_path = old_root / BOARD_REL
        new_board_path = ROOT / BOARD_REL
        old_text = old_board_path.read_text(encoding="utf-8")
        new_text = new_board_path.read_text(encoding="utf-8")
        old_outside, old_block = strip_u5(old_text)
        new_outside, new_block = strip_u5(new_text)
        add(checks, "board text outside U5 is byte-identical", old_outside == new_outside, {
            "baseline_sha256": sha256_bytes(old_outside.encode()),
            "current_sha256": sha256_bytes(new_outside.encode()),
            "baseline_u5_bytes": len(old_block.encode()),
            "current_u5_bytes": len(new_block.encode()),
        })

        old_board = Board.from_file(str(old_board_path))
        new_board = Board.from_file(str(new_board_path))
        old_fps = {reference_of(fp): fp for fp in old_board.footprints}
        new_fps = {reference_of(fp): fp for fp in new_board.footprints}
        old_u5, new_u5 = old_fps["U5"], new_fps["U5"]
        add(checks, "U5 identity and placement", (
            old_u5.libraryNickname == new_u5.libraryNickname == "HomeKey_RevA"
            and old_u5.entryName == new_u5.entryName == "HVQFN-40_L6.0-W6.0-P0.50-BL-EP4.1"
            and normalized(old_u5.position) == normalized(new_u5.position)
            and old_u5.path == new_u5.path == "/generated/U5"
        ), {"placement": normalized(new_u5.position), "path": new_u5.path})

        old_copper = sorted(
            [copper_pad_signature(old_u5, pad, False) for pad in old_u5.pads if "F.Cu" in pad.layers],
            key=stable_json,
        )
        new_copper = sorted(
            [copper_pad_signature(new_u5, pad, True) for pad in new_u5.pads if "F.Cu" in pad.layers],
            key=stable_json,
        )
        add(checks, "U5 41 copper pads, nets, and physical orientation unchanged", (
            len(old_copper) == len(new_copper) == 41 and old_copper == new_copper
        ), {
            "pad_count": len(new_copper),
            "baseline_sha256": sha256_bytes(stable_json(old_copper).encode()),
            "current_sha256": sha256_bytes(stable_json(new_copper).encode()),
        })

        old_paste = sorted(
            [paste_signature(old_u5, pad, False) for pad in old_u5.pads if "F.Paste" in pad.layers],
            key=stable_json,
        )
        new_paste = sorted(
            [paste_signature(new_u5, pad, True) for pad in new_u5.pads if "F.Paste" in pad.layers],
            key=stable_json,
        )
        old_ep = [item for item in old_paste if item["number"] == "41"]
        new_windows = [item for item in new_paste if item["number"] == ""]
        expected_positions = sorted([[x, y] for y in (-0.9, 0.0, 0.9) for x in (-0.9, 0.0, 0.9)])
        add(checks, "legacy full-area exposed-pad paste identified", (
            len(old_ep) == 1 and old_ep[0]["size"] == [4.1, 4.1]
        ), {"legacy_exposed_pad_paste": old_ep})
        add(checks, "NXP 3 x 3 stencil is exact", (
            not any(item["number"] == "41" for item in new_paste)
            and len(new_windows) == 9
            and all(item["size"] == [0.6, 0.6] for item in new_windows)
            and sorted(item["position"] for item in new_windows) == expected_positions
        ), {"window_count": len(new_windows), "windows": new_windows})

        add(checks, "U5 text fields retain physical orientation", (
            physical_text_signatures(old_u5, False) == physical_text_signatures(new_u5, True)
        ), {})

        old_sch = {p.relative_to(old_root): sha256_file(p) for p in old_root.glob("kicad/schematics/*.kicad_sch")}
        new_sch = {p.relative_to(HARDWARE): sha256_file(p) for p in HARDWARE.glob("kicad/schematics/*.kicad_sch")}
        add(checks, "all schematic sheets are byte-identical", old_sch == new_sch, {"file_count": len(new_sch)})

        old_lib = {p.relative_to(old_root): sha256_file(p) for p in old_root.glob("kicad/HomeKey_RevA.pretty/*.kicad_mod")}
        new_lib = {p.relative_to(HARDWARE): sha256_file(p) for p in HARDWARE.glob("kicad/HomeKey_RevA.pretty/*.kicad_mod")}
        changed_lib = sorted(str(path) for path in set(old_lib) | set(new_lib) if old_lib.get(path) != new_lib.get(path))
        expected_lib = ["kicad/HomeKey_RevA.pretty/HVQFN-40_L6.0-W6.0-P0.50-BL-EP4.1.kicad_mod"]
        add(checks, "project-library change scope is U5 only", changed_lib == expected_lib,
            {"changed": changed_lib, "file_count": len(new_lib)})

        old_names = sorted(p.name for p in (old_root / "production/gerbers").iterdir() if p.is_file())
        new_names = sorted(p.name for p in (HARDWARE / "production/gerbers").iterdir() if p.is_file())
        geometry_equal = {}
        for name in old_names:
            old_data = normalize_gerber((old_root / "production/gerbers" / name).read_bytes())
            new_data = normalize_gerber((HARDWARE / "production/gerbers" / name).read_bytes())
            geometry_equal[name] = old_data == new_data
        changed_geometry = sorted(name for name, equal in geometry_equal.items() if not equal)
        expected_changed = ["HomeKey-Lock-RevA-PN7161.GTP"]
        add(checks, "only top-paste manufacturing geometry changed", (
            old_names == new_names and len(new_names) == 13 and changed_geometry == expected_changed
        ), {"file_count": len(new_names), "changed": changed_geometry})

        assembly_names = ["BOM_FULL.csv", "BOM_JLCPCB_DRAFT.csv", "CPL_JLCPCB_DRAFT.csv", "PROCUREMENT_GAPS.md"]
        assembly_equal = {
            name: (old_root / "production/assembly" / name).read_bytes()
            == (HARDWARE / "production/assembly" / name).read_bytes()
            for name in assembly_names
        }
        add(checks, "assembly exports are byte-identical", all(assembly_equal.values()), assembly_equal)

    errors = [item["name"] for item in checks if not item["passed"]]
    return {
        "schema_version": 1,
        "mode": "read-only baseline delta audit",
        "baseline": str(BASELINE),
        "current_board": str(ROOT / BOARD_REL),
        "approved_changes": [
            "U5 exposed-pad paste: full 4.10 x 4.10 mm aperture to NXP 3 x 3, 0.60 mm apertures",
            "U5 courtyard: 6.00 x 6.00 mm to NXP Hx/Hy 7.25 x 7.25 mm",
            "U5 child angles: legacy local representation to native board-coordinate representation",
            "U5 project-library field visibility and removal of the invalid near-full-circle silkscreen arc",
            "Gerber TF.CreationDate",
        ],
        "checks": checks,
        "errors": errors,
        "error_count": len(errors),
        "result": "PASS" if not errors else "FAIL",
    }


def main():
    payload = run()
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["HOMEKEY LOCK REV A — U5 SOT618-1 PHYSICAL DELTA AUDIT", "=" * 76]
    lines += [f"[{'PASS' if item['passed'] else 'FAIL'}] {item['name']}" for item in payload["checks"]]
    lines += ["", "Only the NXP-backed stencil/courtyard and native representation changes are waived.",
              "Copper, drill, nets, placement, other footprints, and assembly data remain gated.", "",
              f"RESULT: {payload['result']} ({payload['error_count']} errors)"]
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if payload["error_count"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
