#!/usr/bin/env python3
"""Prove that the U4 native-footprint sync did not change board physics.

This audit deliberately compares the current project with the reviewed
``graphics-sync-fixed`` archive.  KiCad 10 stores native footprint properties,
zone coordinates, UUID defaults, and layer order differently from the older
kiutils writer, so a raw board-vs-library object comparison produces false
pad/zone differences.  Here those representation details are normalized while
all electrical, copper, drill, placement, and manufacturing data remain gated.
"""

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
from u4_native import footprint_span  # noqa: E402


BASELINE_ARCHIVE = WORKSPACE / "HomeKey-Lock-RevA-PN7161-graphics-sync-fixed.tar.gz"
EXPECTED_BASELINE_SHA256 = "676984543330b6741e51e61694bccc0d4fc29cea1f97c096502869168603d575"
ARCHIVE_PREFIX = "HomeKey-Lock-RevA-PN7161"
BOARD_REL = Path("kicad/HomeKey-Lock-RevA-PN7161.kicad_pcb")
REPORT_JSON = ROOT / "reports/U4_SYNC_DELTA_AUDIT.json"
REPORT_TEXT = ROOT / "reports/U4_SYNC_DELTA_AUDIT.txt"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def rounded(value):
    if value is None:
        return None
    return round(float(value), 6)


def position(value):
    if value is None:
        return None
    return [rounded(value.X), rounded(value.Y), rounded(value.angle or 0)]


def normalized(value, omit=frozenset()):
    """Stable object form with ordering-only KiCad serialization removed."""
    result = clean(value, omit | {"tstamp", "renderCache"})

    def walk(item, key=None):
        if isinstance(item, dict):
            mapped = {name: walk(child, name) for name, child in sorted(item.items())}
            # Older writers omit zero rotation while KiCad 10 emits it.
            if {"X", "Y", "angle"}.issubset(mapped) and mapped["angle"] is None:
                mapped["angle"] = 0
            return mapped
        if isinstance(item, list):
            children = [walk(child) for child in item]
            if key in {"layers", "privateLayers", "netTiePadGroups"}:
                return sorted(children, key=stable_json)
            return children
        return item

    return walk(result)


def pad_signature(pad):
    """Electrical and fabricated geometry of one pad, including custom shapes."""
    result = normalized(
        pad,
        {
            "tstamp",
            "locked",
            # KiCad 10 omits the explicit 45-degree default.  It has no effect
            # unless a thermal connection is actually generated for the pad.
            "thermalBridgeAngle",
        },
    )
    # KiCad 10 omits an unconnected pad's net clause; the prior writer stored
    # the equivalent explicit net 0 with an empty name.
    if result.get("net") is None:
        result["net"] = {"number": 0, "name": ""}
    return result


def graphics_signature(footprint):
    items = []
    for item in footprint.graphicItems:
        name = type(item).__name__
        if name == "FpText" and item.type in {"reference", "value"}:
            continue
        items.append(normalized(item, {"tstamp", "locked"}))
    return sorted(items, key=stable_json)


def zone_signature(zone, footprint_position, coordinates_are_local):
    points = []
    for polygon in zone.polygons:
        polygon_points = []
        for point in polygon.coordinates:
            x = point.X + footprint_position.X if coordinates_are_local else point.X
            y = point.Y + footprint_position.Y if coordinates_are_local else point.Y
            polygon_points.append([rounded(x), rounded(y)])
        points.append(polygon_points)
    return {
        "name": zone.name,
        "net": int(zone.net or 0),
        "layers": sorted(zone.layers),
        "hatch": normalized(zone.hatch),
        "clearance": rounded(zone.clearance),
        "min_thickness": rounded(zone.minThickness),
        "keepout": normalized(zone.keepoutSettings),
        "polygons_absolute": points,
    }


def strip_u4(text: str) -> tuple[str, str]:
    start, end = footprint_span(text)
    return text[:start] + "<U4_NATIVE_BLOCK>\n" + text[end:], text[start:end]


def normalize_gerber(data: bytes) -> bytes:
    return re.sub(rb"(TF\.CreationDate,)[^*\r\n]+", rb"\1<NORMALIZED>", data)


def compare_file_sets(old_root: Path, new_root: Path, pattern: str):
    old = {path.relative_to(old_root): sha256_file(path) for path in old_root.glob(pattern)}
    new = {path.relative_to(new_root): sha256_file(path) for path in new_root.glob(pattern)}
    changed = sorted(str(path) for path in set(old) | set(new) if old.get(path) != new.get(path))
    return old, new, changed


def add_check(checks, name, passed, detail):
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def run() -> dict:
    checks = []
    archive_sha = sha256_file(BASELINE_ARCHIVE)
    add_check(
        checks,
        "reviewed baseline archive identity",
        archive_sha == EXPECTED_BASELINE_SHA256,
        {"actual": archive_sha, "expected": EXPECTED_BASELINE_SHA256},
    )

    with tempfile.TemporaryDirectory(prefix="u4-sync-baseline-") as temp_dir:
        temp = Path(temp_dir)
        with tarfile.open(BASELINE_ARCHIVE, "r:gz") as archive:
            names = archive.getnames()
            safe = all(
                name == ARCHIVE_PREFIX or name.startswith(ARCHIVE_PREFIX + "/")
                for name in names
            )
            add_check(checks, "baseline archive path scope", safe, {"members": len(names)})
            if not safe:
                raise RuntimeError("Baseline archive contains an unexpected path")
            archive.extractall(temp, filter="data")

        old_root = temp / ARCHIVE_PREFIX
        old_board_path = old_root / BOARD_REL
        new_board_path = ROOT / BOARD_REL
        old_text = old_board_path.read_text(encoding="utf-8")
        new_text = new_board_path.read_text(encoding="utf-8")
        old_without_u4, old_u4_text = strip_u4(old_text)
        new_without_u4, new_u4_text = strip_u4(new_text)
        add_check(
            checks,
            "board text outside U4 is byte-identical",
            old_without_u4 == new_without_u4,
            {
                "baseline_sha256": sha256_bytes(old_without_u4.encode()),
                "current_sha256": sha256_bytes(new_without_u4.encode()),
                "baseline_u4_bytes": len(old_u4_text.encode()),
                "current_u4_bytes": len(new_u4_text.encode()),
            },
        )

        old_board = Board.from_file(str(old_board_path))
        new_board = Board.from_file(str(new_board_path))
        old_fps = {reference_of(fp): fp for fp in old_board.footprints}
        new_fps = {reference_of(fp): fp for fp in new_board.footprints}
        add_check(
            checks,
            "footprint reference set",
            set(old_fps) == set(new_fps) and len(new_fps) == 120,
            {"baseline": len(old_fps), "current": len(new_fps)},
        )

        old_u4 = old_fps["U4"]
        new_u4 = new_fps["U4"]
        add_check(
            checks,
            "U4 identity and placement",
            (
                old_u4.libraryNickname == new_u4.libraryNickname == "HomeKey_RevA"
                and old_u4.entryName == new_u4.entryName == "ESP32-C6-MINI-1"
                and position(old_u4.position) == position(new_u4.position) == [99.0, 8.5, 0.0]
                and old_u4.path == new_u4.path == "/generated/U4"
            ),
            {
                "library_id": f"{new_u4.libraryNickname}:{new_u4.entryName}",
                "placement": position(new_u4.position),
                "path": new_u4.path,
            },
        )

        old_pads = {str(pad.number): pad_signature(pad) for pad in old_u4.pads}
        new_pads = {str(pad.number): pad_signature(pad) for pad in new_u4.pads}
        pad_differences = sorted(number for number in set(old_pads) | set(new_pads) if old_pads.get(number) != new_pads.get(number))
        add_check(
            checks,
            "U4 pad copper, drill, custom geometry, and nets",
            len(old_u4.pads) == len(new_u4.pads) == 61 and not pad_differences,
            {"baseline_count": len(old_u4.pads), "current_count": len(new_u4.pads), "different_pad_numbers": pad_differences},
        )

        old_zone = zone_signature(old_u4.zones[0], old_u4.position, True)
        new_zone = zone_signature(new_u4.zones[0], new_u4.position, False)
        add_check(
            checks,
            "U4 four-layer antenna keepout geometry",
            len(old_u4.zones) == len(new_u4.zones) == 1 and old_zone == new_zone,
            {"baseline": old_zone, "current": new_zone},
        )

        old_graphics = graphics_signature(old_u4)
        new_graphics = graphics_signature(new_u4)
        add_check(
            checks,
            "U4 silk, Fab, courtyard, and user graphics",
            old_graphics == new_graphics,
            {
                "baseline_items": len(old_graphics),
                "current_items": len(new_graphics),
                "baseline_sha256": sha256_bytes(stable_json(old_graphics).encode()),
                "current_sha256": sha256_bytes(stable_json(new_graphics).encode()),
            },
        )

        for label, attr in (("models", "models"), ("groups", "groups")):
            old_value = normalized(getattr(old_u4, attr))
            new_value = normalized(getattr(new_u4, attr))
            add_check(
                checks,
                f"U4 {label}",
                old_value == new_value,
                {"baseline_sha256": sha256_bytes(stable_json(old_value).encode()), "current_sha256": sha256_bytes(stable_json(new_value).encode())},
            )

        old_sch, new_sch, changed_sch = compare_file_sets(
            old_root, ROOT, "kicad/schematics/*.kicad_sch"
        )
        add_check(
            checks,
            "all schematic sheets are byte-identical",
            old_sch == new_sch and not changed_sch,
            {"file_count": len(new_sch), "changed": changed_sch},
        )

        old_lib, new_lib, changed_lib = compare_file_sets(
            old_root, ROOT, "kicad/HomeKey_RevA.pretty/*.kicad_mod"
        )
        add_check(
            checks,
            "project-library change scope",
            set(old_lib) == set(new_lib) and changed_lib == ["kicad/HomeKey_RevA.pretty/ESP32-C6-MINI-1.kicad_mod"],
            {"file_count": len(new_lib), "changed": changed_lib},
        )

        gerber_names = sorted(path.name for path in (old_root / "production/gerbers").iterdir() if path.is_file())
        gerber_results = {}
        for name in gerber_names:
            old_data = (old_root / "production/gerbers" / name).read_bytes()
            new_path = HARDWARE / "production/gerbers" / name
            same = new_path.exists() and normalize_gerber(old_data) == normalize_gerber(new_path.read_bytes())
            gerber_results[name] = same
        current_names = sorted(path.name for path in (HARDWARE / "production/gerbers").iterdir() if path.is_file())
        add_check(
            checks,
            "all 13 manufacturing geometry files",
            gerber_names == current_names and len(current_names) == 13 and all(gerber_results.values()),
            {"file_count": len(current_names), "normalized_equal": gerber_results},
        )

        assembly_names = [
            "BOM_FULL.csv",
            "BOM_JLCPCB_DRAFT.csv",
            "CPL_JLCPCB_DRAFT.csv",
            "PROCUREMENT_GAPS.md",
        ]
        assembly_equal = {
            name: (old_root / "production/assembly" / name).read_bytes()
            == (HARDWARE / "production/assembly" / name).read_bytes()
            for name in assembly_names
        }
        add_check(
            checks,
            "assembly exports are byte-identical",
            all(assembly_equal.values()),
            assembly_equal,
        )

    errors = [item["name"] for item in checks if not item["passed"]]
    return {
        "schema_version": 1,
        "mode": "read-only baseline delta audit",
        "baseline_archive": str(BASELINE_ARCHIVE),
        "current_board": str(ROOT / BOARD_REL),
        "representation_normalization": [
            "child UUID/tstamp",
            "layer list ordering",
            "zero-degree angle omission",
            "KiCad 10 native Reference/Value properties",
            "footprint-zone local versus absolute coordinate storage",
            "Gerber TF.CreationDate",
            "redundant default thermal bridge angle",
        ],
        "checks": checks,
        "error_count": len(errors),
        "errors": errors,
        "result": "PASS" if not errors else "FAIL",
    }


def main() -> None:
    payload = run()
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "HOMEKEY LOCK REV A — U4 SYNC PHYSICAL DELTA AUDIT",
        "=" * 76,
        f"Baseline: {payload['baseline_archive']}",
        f"Current:  {payload['current_board']}",
        "",
    ]
    for item in payload["checks"]:
        lines.append(f"[{'PASS' if item['passed'] else 'FAIL'}] {item['name']}")
    lines.extend([
        "",
        "Normalization is limited to KiCad serialization-only differences listed in the JSON report.",
        "Copper, drills, pad nets, placement, keepout geometry, schematics, and production geometry are not waived.",
        "",
        f"RESULT: {payload['result']} ({payload['error_count']} errors)",
    ])
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if payload["error_count"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
