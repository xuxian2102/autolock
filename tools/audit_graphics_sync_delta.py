#!/usr/bin/env python3
"""Audit the 11-instance graphics sync against the prior release archive."""

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
from design_data import GRAPHICS_SYNC_REFS  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402


BASELINE = WORKSPACE / "HomeKey-Lock-RevA-PN7161-passive-rotation-fixed.tar.gz"
ARCHIVE_PREFIX = ROOT.name
REPORT_JSON = ROOT / "reports" / "GRAPHICS_SYNC_DELTA_AUDIT.json"
REPORT_TEXT = ROOT / "reports" / "GRAPHICS_SYNC_DELTA_AUDIT.txt"
EXPECTED_LIBRARY_FILES = {
    "C_1210_3225Metric.kicad_mod",
    "F1812.kicad_mod",
    "LED_0603_1608Metric.kicad_mod",
    "L_0603_1608Metric.kicad_mod",
    "PinHeader_1x02_P2.54mm_Vertical.kicad_mod",
    "PinHeader_1x03_P2.54mm_Vertical.kicad_mod",
    "SMB_L4.6-W3.6-LS5.3-RD.kicad_mod",
    "SOD-123FL_L2.6-W1.6-LS3.5-R-FD.kicad_mod",
    "SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL.kicad_mod",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {key: clean(item) for key, item in sorted(value.items())}
    if hasattr(value, "__dict__"):
        return {
            key: clean(item)
            for key, item in sorted(vars(value).items())
            if key != "filePath"
        }
    return str(value)


def expected_board_angle(local_angle, footprint_angle):
    value = (float(local_angle or 0) + float(footprint_angle or 0)) % 360
    return 0 if abs(value) < 1e-9 else value


def point_tuple(point) -> tuple[float, float]:
    return round(float(point.X), 6), round(float(point.Y), 6)


def normalize_fabrication_bytes(data: bytes) -> bytes:
    return re.sub(
        br"TF\.CreationDate,[^*]+",
        b"TF.CreationDate,<normalized>",
        data,
    )


def archive_bytes(archive: tarfile.TarFile, relative: str) -> bytes:
    source = archive.extractfile(f"{ARCHIVE_PREFIX}/{relative}")
    if source is None:
        raise FileNotFoundError(relative)
    return source.read()


def main() -> None:
    if not BASELINE.exists():
        raise SystemExit(f"Missing baseline: {BASELINE}")

    errors = []
    text_angle_count = 0
    pad_angle_count = 0
    q1_arc_count = 0
    q1_property_count = 0
    current_board = Board.from_file(str(BOARD_PATH))
    current_by_ref = {reference_of(item): item for item in current_board.footprints}

    with tempfile.TemporaryDirectory(prefix="graphics-sync-audit-") as temp_name:
        temp = Path(temp_name)
        with tarfile.open(BASELINE, "r:gz") as archive:
            baseline_board_path = temp / BOARD_PATH.name
            baseline_board_path.write_bytes(
                archive_bytes(archive, f"kicad/{BOARD_PATH.name}")
            )
            baseline_board = Board.from_file(str(baseline_board_path))
            baseline_by_ref = {
                reference_of(item): item for item in baseline_board.footprints
            }

            if set(current_by_ref) != set(baseline_by_ref):
                errors.append("PCB footprint reference set changed")

            for reference in sorted(set(current_by_ref) & set(baseline_by_ref)):
                current = current_by_ref[reference]
                previous = baseline_by_ref[reference]
                current_data = clean(current)
                previous_data = clean(previous)
                if reference not in GRAPHICS_SYNC_REFS:
                    if current_data != previous_data:
                        errors.append(f"{reference}: non-target footprint changed")
                    continue

                footprint_angle = float(previous.position.angle or 0)
                if len(current.graphicItems) != len(previous.graphicItems):
                    errors.append(f"{reference}: graphic item count changed")
                    continue
                for index, (current_item, previous_item) in enumerate(
                    zip(current.graphicItems, previous.graphicItems)
                ):
                    if type(current_item).__name__ != type(previous_item).__name__:
                        errors.append(f"{reference}: graphic item type/order changed")
                        continue
                    if type(previous_item).__name__ == "FpText":
                        expected = expected_board_angle(
                            previous_item.position.angle, footprint_angle
                        )
                        actual = float(current_item.position.angle or 0)
                        if actual != expected:
                            errors.append(
                                f"{reference}:{previous_item.type}: angle {actual} != {expected}"
                            )
                        current_data["graphicItems"][index]["position"]["angle"] = (
                            previous_data["graphicItems"][index]["position"].get("angle")
                        )
                        text_angle_count += 1

                if len(current.pads) != len(previous.pads):
                    errors.append(f"{reference}: pad count changed")
                    continue
                for index, (current_pad, previous_pad) in enumerate(
                    zip(current.pads, previous.pads)
                ):
                    if str(current_pad.number) != str(previous_pad.number):
                        errors.append(f"{reference}: pad identity/order changed")
                        continue
                    expected = expected_board_angle(
                        previous_pad.position.angle, footprint_angle
                    )
                    actual = float(current_pad.position.angle or 0)
                    if actual != expected:
                        errors.append(
                            f"{reference}:pad {previous_pad.number}: angle {actual} != {expected}"
                        )
                    current_data["pads"][index]["position"]["angle"] = (
                        previous_data["pads"][index]["position"].get("angle")
                    )
                    pad_angle_count += 1

                if reference == "Q1":
                    if previous.properties.get("LCSC Part") != "C16072":
                        errors.append("Q1: baseline footprint LCSC property is unexpected")
                    if "LCSC Part" in current.properties:
                        errors.append("Q1: duplicate footprint LCSC property still present")
                    current_data["properties"] = previous_data["properties"]
                    q1_property_count = 1

                    arc_indexes = [
                        index for index, item in enumerate(current.graphicItems)
                        if (
                            type(item).__name__ == "FpArc"
                            and item.layer == "F.SilkS"
                            and point_tuple(item.start) == (-2.53, -0.45)
                            and point_tuple(item.mid) == (-2.09, -0.009347)
                            and point_tuple(item.end) == (-2.531306, 0.429998)
                        )
                    ]
                    if len(arc_indexes) != 1:
                        errors.append(
                            f"Q1: expected one corrected silk arc, found {len(arc_indexes)}"
                        )
                    else:
                        index = arc_indexes[0]
                        if type(previous.graphicItems[index]).__name__ != "FpArc":
                            errors.append("Q1: corrected arc identity/order changed")
                        else:
                            for key in ("start", "mid", "end"):
                                current_data["graphicItems"][index][key] = (
                                    previous_data["graphicItems"][index][key]
                                )
                            q1_arc_count = 1

                if current_data != previous_data:
                    errors.append(f"{reference}: data changed beyond reviewed fields")

            current_board_data = clean(current_board)
            baseline_board_data = clean(baseline_board)
            current_board_data.pop("footprints", None)
            baseline_board_data.pop("footprints", None)
            if current_board_data != baseline_board_data:
                errors.append("PCB data outside footprints changed")

            schematic_results = []
            for current_path in sorted(
                (HARDWARE / "kicad" / "schematics").glob("*.kicad_sch")
            ):
                relative = f"kicad/schematics/{current_path.name}"
                identical = current_path.read_bytes() == archive_bytes(archive, relative)
                schematic_results.append({
                    "file": current_path.name,
                    "identical": identical,
                })
                if not identical:
                    errors.append(f"Schematic changed: {current_path.name}")

            library_dir = HARDWARE / "kicad" / "HomeKey_RevA.pretty"
            changed_library_files = set()
            for current_path in sorted(library_dir.glob("*.kicad_mod")):
                relative = f"kicad/HomeKey_RevA.pretty/{current_path.name}"
                if current_path.read_bytes() != archive_bytes(archive, relative):
                    changed_library_files.add(current_path.name)
            if changed_library_files != EXPECTED_LIBRARY_FILES:
                errors.append(
                    "Project library change set mismatch: "
                    f"missing={sorted(EXPECTED_LIBRARY_FILES-changed_library_files)} "
                    f"extra={sorted(changed_library_files-EXPECTED_LIBRARY_FILES)}"
                )

            fabrication_results = []
            for current_path in sorted((HARDWARE / "production" / "gerbers").iterdir()):
                if not current_path.is_file():
                    continue
                relative = f"production/gerbers/{current_path.name}"
                identical = normalize_fabrication_bytes(
                    current_path.read_bytes()
                ) == normalize_fabrication_bytes(archive_bytes(archive, relative))
                fabrication_results.append({
                    "file": current_path.name,
                    "geometry_identical": identical,
                })
                if not identical:
                    errors.append(f"Fabrication geometry changed: {current_path.name}")

    if text_angle_count != 33:
        errors.append(f"Text angle coverage {text_angle_count} != 33")
    if pad_angle_count != 29:
        errors.append(f"Pad angle coverage {pad_angle_count} != 29")
    if q1_arc_count != 1:
        errors.append(f"Q1 corrected arc coverage {q1_arc_count} != 1")
    if q1_property_count != 1:
        errors.append(f"Q1 property coverage {q1_property_count} != 1")

    payload = {
        "baseline_archive": str(BASELINE),
        "baseline_archive_sha256": sha256_file(BASELINE),
        "current_board_sha256": sha256_file(BOARD_PATH),
        "target_instances": len(GRAPHICS_SYNC_REFS),
        "verified_text_angles": text_angle_count,
        "verified_pad_angles": pad_angle_count,
        "verified_q1_silk_arc_correction": q1_arc_count == 1,
        "verified_q1_duplicate_property_removal": q1_property_count == 1,
        "non_target_footprints_unchanged": not any(
            "non-target footprint" in item for item in errors
        ),
        "board_outside_footprints_unchanged": (
            "PCB data outside footprints changed" not in errors
        ),
        "schematics": schematic_results,
        "changed_project_library_files": sorted(changed_library_files),
        "fabrication_files": fabrication_results,
        "fabrication_geometry_identical": bool(fabrication_results) and all(
            item["geometry_identical"] for item in fabrication_results
        ),
        "errors": errors,
        "result": "PASS" if not errors else "FAIL",
    }
    REPORT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "HOMEKEY LOCK REV A — GRAPHICS SYNC DELTA AUDIT",
        "=" * 72,
        f"Target instances: {len(GRAPHICS_SYNC_REFS)}",
        f"Verified text child angles: {text_angle_count}",
        f"Verified pad child angles: {pad_angle_count}",
        f"Q1 silk arc correction verified: {q1_arc_count == 1}",
        f"Q1 duplicate property removal verified: {q1_property_count == 1}",
        f"Non-target footprints unchanged: {payload['non_target_footprints_unchanged']}",
        f"Board outside footprints unchanged: {payload['board_outside_footprints_unchanged']}",
        f"Changed project library files: {len(changed_library_files)} / {len(EXPECTED_LIBRARY_FILES)} expected",
        f"Schematics unchanged: {all(item['identical'] for item in schematic_results)}",
        f"Fabrication geometry identical: {payload['fabrication_geometry_identical']} ({len(fabrication_results)} files)",
        f"Errors: {len(errors)}",
        "",
        f"RESULT: {payload['result']}",
    ]
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if errors:
        for error in errors:
            print(f" - {error}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
