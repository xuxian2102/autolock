#!/usr/bin/env python3
"""Verify J2 library/native-angle sync against the reviewed U4 checkpoint."""

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
from j2_native import footprint_span  # noqa: E402
from kiutils.board import Board  # noqa: E402


BASELINE = WORKSPACE / "HomeKey-Lock-RevA-PN7161-u4-sync-fixed.tar.gz"
EXPECTED_BASELINE_SHA256 = "47b1f8d25631ac45446c1b25889c99093baddc444f819a5e3900795e71051926"
PREFIX = "HomeKey-Lock-RevA-PN7161"
BOARD_REL = Path("kicad/HomeKey-Lock-RevA-PN7161.kicad_pcb")
REPORT_JSON = ROOT / "reports/J2_SYNC_DELTA_AUDIT.json"
REPORT_TEXT = ROOT / "reports/J2_SYNC_DELTA_AUDIT.txt"


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


def strip_j2(text: str):
    start, end = footprint_span(text)
    return text[:start] + "<J2_NATIVE_BLOCK>\n" + text[end:], text[start:end]


def physical_pad_signature(footprint, pad, native_angles):
    signature = normalized(pad, {"tstamp", "net", "locked", "pinFunction", "pinType"})
    signature["number"] = str(signature["number"])
    signature["net"] = {
        "number": int(pad.net.number if pad.net else 0),
        "name": pad.net.name if pad.net else "",
    }
    local = float(pad.position.angle or 0)
    parent = float(footprint.position.angle or 0)
    signature["physical_board_angle"] = round(local if native_angles else (parent + local) % 360, 6)
    # The raw child angle is only the representation being converted.
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


def normalize_gerber(data: bytes) -> bytes:
    return re.sub(rb"(TF\.CreationDate,)[^*\r\n]+", rb"\1<NORMALIZED>", data)


def add(checks, name, passed, detail):
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def run():
    checks = []
    add(checks, "reviewed U4 checkpoint identity", sha256_file(BASELINE) == EXPECTED_BASELINE_SHA256,
        {"actual": sha256_file(BASELINE), "expected": EXPECTED_BASELINE_SHA256})
    with tempfile.TemporaryDirectory(prefix="j2-delta-") as temp_dir:
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
        old_outside, old_block = strip_j2(old_text)
        new_outside, new_block = strip_j2(new_text)
        add(checks, "board text outside J2 is byte-identical", old_outside == new_outside, {
            "baseline_sha256": sha256_bytes(old_outside.encode()),
            "current_sha256": sha256_bytes(new_outside.encode()),
            "baseline_j2_bytes": len(old_block.encode()),
            "current_j2_bytes": len(new_block.encode()),
        })

        old_board = Board.from_file(str(old_board_path))
        new_board = Board.from_file(str(new_board_path))
        old_fps = {reference_of(fp): fp for fp in old_board.footprints}
        new_fps = {reference_of(fp): fp for fp in new_board.footprints}
        old_j2, new_j2 = old_fps["J2"], new_fps["J2"]
        add(checks, "J2 identity and placement", (
            old_j2.libraryNickname == new_j2.libraryNickname == "HomeKey_RevA"
            and old_j2.entryName == new_j2.entryName == "USB-C_SMD-TYPE-C-31-M-12_1"
            and normalized(old_j2.position) == normalized(new_j2.position)
            and old_j2.path == new_j2.path == "/generated/J2"
        ), {"placement": normalized(new_j2.position), "path": new_j2.path})

        old_pads = sorted(
            [physical_pad_signature(old_j2, pad, False) for pad in old_j2.pads], key=stable_json
        )
        new_pads = sorted(
            [physical_pad_signature(new_j2, pad, True) for pad in new_j2.pads], key=stable_json
        )
        add(checks, "J2 18-pad copper, drill, nets, and physical orientation",
            len(old_pads) == len(new_pads) == 18 and old_pads == new_pads, {
                "pad_count": len(new_pads),
                "baseline_sha256": sha256_bytes(stable_json(old_pads).encode()),
                "current_sha256": sha256_bytes(stable_json(new_pads).encode()),
            })
        npth = [pad for pad in new_j2.pads if pad.type == "np_thru_hole"]
        add(checks, "J2 two plastic positioning holes are NPTH", (
            len(npth) == 2
            and sorted((round(float(p.position.X), 2), round(float(p.position.Y), 2), round(float(p.drill.diameter), 2)) for p in npth)
            == [(-2.9, -1.21, 0.6), (2.9, -1.21, 0.6)]
        ), {"count": len(npth)})
        old_text_sig = physical_text_signatures(old_j2, False)
        new_text_sig = physical_text_signatures(new_j2, True)
        add(checks, "J2 text fields retain physical orientation", old_text_sig == new_text_sig, {
            "baseline_sha256": sha256_bytes(stable_json(old_text_sig).encode()),
            "current_sha256": sha256_bytes(stable_json(new_text_sig).encode()),
        })

        old_sch = {p.relative_to(old_root): sha256_file(p) for p in old_root.glob("kicad/schematics/*.kicad_sch")}
        new_sch = {p.relative_to(HARDWARE): sha256_file(p) for p in HARDWARE.glob("kicad/schematics/*.kicad_sch")}
        add(checks, "all schematic sheets are byte-identical", old_sch == new_sch, {"file_count": len(new_sch)})

        old_lib = {p.relative_to(old_root): sha256_file(p) for p in old_root.glob("kicad/HomeKey_RevA.pretty/*.kicad_mod")}
        new_lib = {p.relative_to(HARDWARE): sha256_file(p) for p in HARDWARE.glob("kicad/HomeKey_RevA.pretty/*.kicad_mod")}
        changed_lib = sorted(str(path) for path in set(old_lib) | set(new_lib) if old_lib.get(path) != new_lib.get(path))
        expected_lib = ["kicad/HomeKey_RevA.pretty/USB-C_SMD-TYPE-C-31-M-12_1.kicad_mod"]
        add(checks, "project-library change scope is J2 only", changed_lib == expected_lib,
            {"changed": changed_lib, "file_count": len(new_lib)})

        old_names = sorted(p.name for p in (old_root / "production/gerbers").iterdir() if p.is_file())
        new_names = sorted(p.name for p in (HARDWARE / "production/gerbers").iterdir() if p.is_file())
        geometry_equal = {}
        for name in old_names:
            old_data = (old_root / "production/gerbers" / name).read_bytes()
            new_data = (HARDWARE / "production/gerbers" / name).read_bytes()
            geometry_equal[name] = normalize_gerber(old_data) == normalize_gerber(new_data)
        add(checks, "all 13 manufacturing geometry files", (
            old_names == new_names and len(new_names) == 13 and all(geometry_equal.values())
        ), {"file_count": len(new_names), "normalized_equal": geometry_equal})

        assembly_names = ["BOM_FULL.csv", "BOM_JLCPCB_DRAFT.csv", "CPL_JLCPCB_DRAFT.csv", "PROCUREMENT_GAPS.md"]
        assembly_equal = {name: (old_root / "production/assembly" / name).read_bytes() ==
                          (HARDWARE / "production/assembly" / name).read_bytes() for name in assembly_names}
        add(checks, "assembly exports are byte-identical", all(assembly_equal.values()), assembly_equal)

    errors = [item["name"] for item in checks if not item["passed"]]
    return {
        "schema_version": 1,
        "mode": "read-only baseline delta audit",
        "baseline": str(BASELINE),
        "current_board": str(ROOT / BOARD_REL),
        "approved_representation_changes": [
            "J2 3 text child angles: legacy local 0 degrees to native board 180 degrees",
            "J2 18 pad child angles: legacy local 0 degrees to native board 180 degrees",
            "project-library positioning holes: THT spelling corrected to NPTH",
            "project-library Value field hidden to match placed copy",
            "custom-pad sub-micrometre coordinate spelling only",
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
    lines = ["HOMEKEY LOCK REV A — J2 SYNC PHYSICAL DELTA AUDIT", "=" * 76]
    lines += [f"[{'PASS' if item['passed'] else 'FAIL'}] {item['name']}" for item in payload["checks"]]
    lines += ["", "Only representation changes listed in the JSON report are waived.",
              "Copper, drill, net, placement, and manufacturing geometry remain gated.", "",
              f"RESULT: {payload['result']} ({payload['error_count']} errors)"]
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if payload["error_count"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
