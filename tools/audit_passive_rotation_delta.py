#!/usr/bin/env python3
"""Audit the 58-instance child-angle fix against the passive-sync archive."""

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
from design_data import PASSIVE_SYNC_REFS  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402


BASELINE = WORKSPACE / "HomeKey-Lock-RevA-PN7161-passive-sync-batch1.tar.gz"
ARCHIVE_PREFIX = ROOT.name
REPORT_JSON = ROOT / "reports" / "PASSIVE_ROTATION_DELTA_AUDIT.json"
REPORT_TEXT = ROOT / "reports" / "PASSIVE_ROTATION_DELTA_AUDIT.txt"


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


def normalize_fabrication_bytes(data: bytes) -> bytes:
    return re.sub(br"TF\.CreationDate,[^*]+", b"TF.CreationDate,<normalized>", data)


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
    current_board = Board.from_file(str(BOARD_PATH))
    current_by_ref = {reference_of(item): item for item in current_board.footprints}

    with tempfile.TemporaryDirectory(prefix="passive-rotation-audit-") as temp_name:
        temp = Path(temp_name)
        with tarfile.open(BASELINE, "r:gz") as archive:
            baseline_board_path = temp / BOARD_PATH.name
            baseline_board_path.write_bytes(
                archive_bytes(archive, f"kicad/{BOARD_PATH.name}")
            )
            baseline_board = Board.from_file(str(baseline_board_path))
            baseline_by_ref = {reference_of(item): item for item in baseline_board.footprints}

            if set(current_by_ref) != set(baseline_by_ref):
                errors.append("PCB footprint reference set changed")

            for reference in sorted(set(current_by_ref) & set(baseline_by_ref)):
                current = current_by_ref[reference]
                previous = baseline_by_ref[reference]
                current_data = clean(current)
                previous_data = clean(previous)
                if reference not in PASSIVE_SYNC_REFS:
                    if current_data != previous_data:
                        errors.append(f"{reference}: non-target footprint changed")
                    continue

                footprint_angle = previous_data["position"]["angle"] or 0
                current_graphics = current_data["graphicItems"]
                previous_graphics = previous_data["graphicItems"]
                if len(current_graphics) != len(previous_graphics):
                    errors.append(f"{reference}: graphic item count changed")
                    continue
                for current_item, previous_item in zip(current_graphics, previous_graphics):
                    if current_item.get("tstamp") != previous_item.get("tstamp"):
                        errors.append(f"{reference}: graphic item identity/order changed")
                        continue
                    if previous_item.get("type") != "reference" and previous_item.get("type") != "value" and previous_item.get("type") != "user":
                        continue
                    expected = expected_board_angle(
                        previous_item["position"].get("angle"), footprint_angle
                    )
                    actual = current_item["position"].get("angle") or 0
                    if actual != expected:
                        errors.append(
                            f"{reference}:{previous_item.get('type')}: angle {actual} != {expected}"
                        )
                    current_item["position"]["angle"] = previous_item["position"].get("angle")
                    text_angle_count += 1

                current_pads = current_data["pads"]
                previous_pads = previous_data["pads"]
                if len(current_pads) != len(previous_pads):
                    errors.append(f"{reference}: pad count changed")
                    continue
                for current_pad, previous_pad in zip(current_pads, previous_pads):
                    if str(current_pad.get("number")) != str(previous_pad.get("number")):
                        errors.append(f"{reference}: pad identity/order changed")
                        continue
                    expected = expected_board_angle(
                        previous_pad["position"].get("angle"), footprint_angle
                    )
                    actual = current_pad["position"].get("angle") or 0
                    if actual != expected:
                        errors.append(
                            f"{reference}:pad {previous_pad.get('number')}: angle {actual} != {expected}"
                        )
                    current_pad["position"]["angle"] = previous_pad["position"].get("angle")
                    pad_angle_count += 1

                if current_data != previous_data:
                    errors.append(f"{reference}: data changed beyond child angles")

            current_board_data = clean(current_board)
            baseline_board_data = clean(baseline_board)
            current_board_data.pop("footprints", None)
            baseline_board_data.pop("footprints", None)
            if current_board_data != baseline_board_data:
                errors.append("PCB data outside footprints changed")

            schematic_results = []
            for current_path in sorted((HARDWARE / "kicad" / "schematics").glob("*.kicad_sch")):
                relative = f"kicad/schematics/{current_path.name}"
                identical = current_path.read_bytes() == archive_bytes(archive, relative)
                schematic_results.append({"file": current_path.name, "identical": identical})
                if not identical:
                    errors.append(f"Schematic changed: {current_path.name}")

            fabrication_results = []
            for current_path in sorted((HARDWARE / "production" / "gerbers").iterdir()):
                if not current_path.is_file():
                    continue
                relative = f"production/gerbers/{current_path.name}"
                identical = normalize_fabrication_bytes(current_path.read_bytes()) == normalize_fabrication_bytes(
                    archive_bytes(archive, relative)
                )
                fabrication_results.append({"file": current_path.name, "geometry_identical": identical})
                if not identical:
                    errors.append(f"Fabrication geometry changed: {current_path.name}")

    if text_angle_count != 174:
        errors.append(f"Text angle coverage {text_angle_count} != 174")
    if pad_angle_count != 116:
        errors.append(f"Pad angle coverage {pad_angle_count} != 116")

    payload = {
        "baseline_archive": str(BASELINE),
        "baseline_archive_sha256": sha256_file(BASELINE),
        "current_board_sha256": sha256_file(BOARD_PATH),
        "target_instances": len(PASSIVE_SYNC_REFS),
        "verified_text_angles": text_angle_count,
        "verified_pad_angles": pad_angle_count,
        "non_target_footprints_unchanged": not any("non-target footprint" in item for item in errors),
        "board_outside_footprints_unchanged": "PCB data outside footprints changed" not in errors,
        "schematics": schematic_results,
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
        "HOMEKEY LOCK REV A — PASSIVE ROTATION DELTA AUDIT",
        "=" * 72,
        f"Target instances: {len(PASSIVE_SYNC_REFS)}",
        f"Verified text child angles: {text_angle_count}",
        f"Verified pad child angles: {pad_angle_count}",
        f"Non-target footprints unchanged: {payload['non_target_footprints_unchanged']}",
        f"Board outside footprints unchanged: {payload['board_outside_footprints_unchanged']}",
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
