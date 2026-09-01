#!/usr/bin/env python3
"""Prove the passive sync delta against the archived pre-sync baseline."""

from __future__ import annotations

import argparse
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
from design_data import PASSIVE_SYNC_REFS, footprint_for, parts  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from generate_schematics import SCHEMATIC_OUT  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.schematic import Schematic  # noqa: E402


DEFAULT_BASELINE = WORKSPACE / "restore" / "HomeKey-Lock-RevA-PN7161-footprint-sync-audited.tar.gz"
REPORT = ROOT / "reports" / "PASSIVE_SYNC_DELTA_AUDIT.json"
TEXT_REPORT = ROOT / "reports" / "PASSIVE_SYNC_DELTA_AUDIT.txt"
ARCHIVE_PREFIX = ROOT.name


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


def without_footprint_link(footprint):
    value = clean(footprint)
    value.pop("libraryNickname", None)
    value.pop("entryName", None)
    return value


def property_map(symbol):
    return {item.key: item for item in symbol.properties}


def read_archive_member(archive: tarfile.TarFile, member: str, destination: Path) -> Path:
    source = archive.extractfile(member)
    if source is None:
        raise FileNotFoundError(member)
    destination.write_bytes(source.read())
    return destination


def normalize_fabrication_bytes(data: bytes) -> bytes:
    """Ignore the exporter timestamp while comparing physical output bytes."""
    return re.sub(
        br"TF\.CreationDate,[^*]+",
        b"TF.CreationDate,<normalized>",
        data,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args()
    baseline = args.baseline.resolve()
    if not baseline.exists():
        raise SystemExit(f"Missing baseline archive: {baseline}")

    errors = []
    part_by_ref = {part.ref: part for part in parts}
    current_board = Board.from_file(str(BOARD_PATH))
    current_footprints = {reference_of(fp): fp for fp in current_board.footprints}

    with tempfile.TemporaryDirectory(prefix="passive-sync-delta-") as temp_name:
        temp = Path(temp_name)
        with tarfile.open(baseline, "r:gz") as archive:
            baseline_board_path = read_archive_member(
                archive,
                f"{ARCHIVE_PREFIX}/kicad/{BOARD_PATH.name}",
                temp / BOARD_PATH.name,
            )
            baseline_board = Board.from_file(str(baseline_board_path))
            baseline_footprints = {reference_of(fp): fp for fp in baseline_board.footprints}

            if set(current_footprints) != set(baseline_footprints):
                errors.append("PCB footprint reference set changed")
            for reference in sorted(set(current_footprints) & set(baseline_footprints)):
                current = current_footprints[reference]
                previous = baseline_footprints[reference]
                if reference in PASSIVE_SYNC_REFS:
                    if without_footprint_link(current) != without_footprint_link(previous):
                        errors.append(f"{reference}: PCB data changed beyond library link")
                    expected = footprint_for(part_by_ref[reference])
                    actual = f"{current.libraryNickname}:{current.entryName}"
                    if actual != expected:
                        errors.append(f"{reference}: library link {actual} != {expected}")
                elif clean(current) != clean(previous):
                    errors.append(f"{reference}: non-target PCB footprint changed")

            current_board_other = clean(current_board)
            baseline_board_other = clean(baseline_board)
            current_board_other.pop("footprints", None)
            baseline_board_other.pop("footprints", None)
            if current_board_other != baseline_board_other:
                errors.append("PCB data outside footprints changed")

            schematic_changes = []
            seen_targets = set()
            for current_path in sorted(SCHEMATIC_OUT.glob("*.kicad_sch")):
                baseline_path = read_archive_member(
                    archive,
                    f"{ARCHIVE_PREFIX}/kicad/schematics/{current_path.name}",
                    temp / f"baseline-{current_path.name}",
                )
                current_schematic = Schematic.from_file(str(current_path))
                baseline_schematic = Schematic.from_file(str(baseline_path))
                current_symbols = {
                    property_map(symbol)["Reference"].value: symbol
                    for symbol in current_schematic.schematicSymbols
                }
                baseline_symbols = {
                    property_map(symbol)["Reference"].value: symbol
                    for symbol in baseline_schematic.schematicSymbols
                }
                if set(current_symbols) != set(baseline_symbols):
                    errors.append(f"{current_path.name}: symbol reference set changed")
                    continue
                for reference in sorted(current_symbols):
                    current_symbol = current_symbols[reference]
                    baseline_symbol = baseline_symbols[reference]
                    if reference not in PASSIVE_SYNC_REFS:
                        if clean(current_symbol) != clean(baseline_symbol):
                            errors.append(f"{current_path.name}:{reference}: non-target symbol changed")
                        continue
                    current_properties = property_map(current_symbol)
                    baseline_properties = property_map(baseline_symbol)
                    current_value = current_properties["Footprint"].value
                    baseline_value = baseline_properties["Footprint"].value
                    expected = footprint_for(part_by_ref[reference])
                    if current_value != expected:
                        errors.append(f"{current_path.name}:{reference}: Footprint {current_value} != {expected}")
                    current_properties["Footprint"].value = baseline_value
                    if clean(current_symbol) != clean(baseline_symbol):
                        errors.append(f"{current_path.name}:{reference}: symbol changed beyond Footprint property")
                    schematic_changes.append({
                        "reference": reference,
                        "schematic": current_path.name,
                        "before": baseline_value,
                        "after": current_value,
                    })
                    seen_targets.add(reference)

                current_other = clean(current_schematic)
                baseline_other = clean(baseline_schematic)
                current_other.pop("schematicSymbols", None)
                baseline_other.pop("schematicSymbols", None)
                if current_other != baseline_other:
                    errors.append(f"{current_path.name}: schematic data outside symbols changed")

            fabrication_results = []
            gerber_dir = HARDWARE / "production" / "gerbers"
            for current_path in sorted(gerber_dir.iterdir()):
                if not current_path.is_file():
                    continue
                member = f"{ARCHIVE_PREFIX}/production/gerbers/{current_path.name}"
                source = archive.extractfile(member)
                if source is None:
                    errors.append(f"Baseline fabrication file missing: {current_path.name}")
                    continue
                previous = source.read()
                current = current_path.read_bytes()
                identical = normalize_fabrication_bytes(previous) == normalize_fabrication_bytes(current)
                fabrication_results.append({
                    "file": current_path.name,
                    "physical_bytes_identical_after_creation_date_normalization": identical,
                })
                if not identical:
                    errors.append(f"Fabrication geometry changed: {current_path.name}")

    if seen_targets != PASSIVE_SYNC_REFS:
        errors.append(
            f"Schematic target coverage mismatch: missing={sorted(PASSIVE_SYNC_REFS-seen_targets)} "
            f"extra={sorted(seen_targets-PASSIVE_SYNC_REFS)}"
        )

    payload = {
        "baseline_archive": str(baseline),
        "baseline_archive_sha256": sha256_file(baseline),
        "current_board_sha256": sha256_file(BOARD_PATH),
        "target_count": len(PASSIVE_SYNC_REFS),
        "verified_board_library_link_only_changes": len(PASSIVE_SYNC_REFS),
        "verified_schematic_footprint_property_only_changes": len(schematic_changes),
        "non_target_board_footprints_unchanged": not any("non-target PCB footprint" in item for item in errors),
        "board_tracks_zones_graphics_nets_unchanged": "PCB data outside footprints changed" not in errors,
        "non_target_schematic_symbols_unchanged": not any("non-target symbol" in item for item in errors),
        "schematic_non_symbol_data_unchanged": not any("schematic data outside symbols" in item for item in errors),
        "fabrication_files": fabrication_results,
        "fabrication_geometry_identical": bool(fabrication_results) and all(
            item["physical_bytes_identical_after_creation_date_normalization"]
            for item in fabrication_results
        ),
        "changes": schematic_changes,
        "errors": errors,
        "result": "PASS" if not errors else "FAIL",
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "HOMEKEY LOCK REV A — PASSIVE SYNC ARCHIVE DELTA AUDIT",
        "=" * 72,
        f"Baseline archive SHA256: {payload['baseline_archive_sha256']}",
        f"Reviewed target instances: {len(PASSIVE_SYNC_REFS)}",
        f"PCB link-only changes verified: {payload['verified_board_library_link_only_changes']}",
        f"Schematic property-only changes verified: {payload['verified_schematic_footprint_property_only_changes']}",
        f"Non-target PCB footprints unchanged: {payload['non_target_board_footprints_unchanged']}",
        f"Tracks/zones/graphics/nets unchanged: {payload['board_tracks_zones_graphics_nets_unchanged']}",
        f"Non-target schematic symbols unchanged: {payload['non_target_schematic_symbols_unchanged']}",
        f"Schematic non-symbol data unchanged: {payload['schematic_non_symbol_data_unchanged']}",
        f"Fabrication geometry identical: {payload['fabrication_geometry_identical']} ({len(fabrication_results)} files)",
        f"Errors: {len(errors)}",
        "",
        f"RESULT: {payload['result']}",
    ]
    TEXT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if errors:
        for error in errors:
            print(f" - {error}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
