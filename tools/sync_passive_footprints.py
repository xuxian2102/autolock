#!/usr/bin/env python3
"""Synchronize only the reviewed 58 passive footprint instances.

Creates six project-local documentation variants, updates the corresponding
PCB library links and schematic Footprint properties, and proves that placed
pad/net/position data is byte-for-byte equivalent after normalization.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKSPACE / ".tools" / "py"))

from audit_board import reference_of  # noqa: E402
from design_data import (  # noqa: E402
    PASSIVE_FAB_REF_REFS,
    PASSIVE_HIDDEN_VALUE_REFS,
    PASSIVE_SYNC_REFS,
    PASSIVE_VARIANT_DEFINITIONS,
    footprint_for,
    parts,
)
from generate_board import BOARD_PATH  # noqa: E402
from generate_schematics import LIB_OUT, SCHEMATIC_OUT  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.footprint import Footprint  # noqa: E402
from kiutils.schematic import Schematic  # noqa: E402


REPORT_JSON = ROOT / "reports" / "PASSIVE_FOOTPRINT_SYNC.json"
REPORT_TEXT = ROOT / "reports" / "PASSIVE_FOOTPRINT_SYNC.txt"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean(value, omit=frozenset()):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (list, tuple)):
        return [clean(item, omit) for item in value]
    if isinstance(value, dict):
        return {key: clean(item, omit) for key, item in sorted(value.items()) if key not in omit}
    if hasattr(value, "__dict__"):
        result = {
            key: clean(item, omit)
            for key, item in sorted(vars(value).items())
            if key not in omit
        }
        if type(value).__name__ == "Pad":
            result["number"] = str(result["number"])
        return result
    return str(value)


def placed_electrical_signature(footprint):
    """Everything that this batch is forbidden to alter on a placed part."""
    return clean({
        "position": footprint.position,
        "layer": footprint.layer,
        "pads": footprint.pads,
        "zones": footprint.zones,
        "attributes": footprint.attributes,
    }, {"tstamp", "path"})


def expected_reference_layer(reference: str) -> str:
    if reference in PASSIVE_FAB_REF_REFS:
        return "F.Fab"
    if reference in PASSIVE_HIDDEN_VALUE_REFS:
        return "F.SilkS"
    raise KeyError(reference)


def write_variants():
    created = []
    base_hashes_before = {}
    for variant_id, (base_id, reference_layer) in PASSIVE_VARIANT_DEFINITIONS.items():
        variant_name = variant_id.split(":", 1)[1]
        base_name = base_id.split(":", 1)[1]
        base_path = LIB_OUT / f"{base_name}.kicad_mod"
        base_hashes_before[base_name] = sha256_file(base_path)
        footprint = Footprint.from_file(str(base_path))
        footprint.entryName = variant_name
        for item in footprint.graphicItems:
            if getattr(item, "type", None) == "reference":
                item.layer = reference_layer
            elif getattr(item, "type", None) == "value":
                item.hide = True
        destination = LIB_OUT / f"{variant_name}.kicad_mod"
        footprint.to_file(str(destination))
        created.append({
            "variant_id": variant_id,
            "base_id": base_id,
            "reference_layer": reference_layer,
            "sha256": sha256_file(destination),
        })
    base_hashes_after = {
        name: sha256_file(LIB_OUT / f"{name}.kicad_mod")
        for name in base_hashes_before
    }
    if base_hashes_after != base_hashes_before:
        raise RuntimeError("Base footprint library changed while creating variants")
    return created, base_hashes_before


def update_board(part_by_ref):
    board_sha_before = sha256_file(BOARD_PATH)
    board = Board.from_file(str(BOARD_PATH))
    before = {}
    changed = []
    for footprint in board.footprints:
        reference = reference_of(footprint)
        if reference not in PASSIVE_SYNC_REFS:
            continue
        before[reference] = placed_electrical_signature(footprint)
        reference_field = next(
            item for item in footprint.graphicItems
            if type(item).__name__ == "FpText" and item.type == "reference"
        )
        value_field = next(
            item for item in footprint.graphicItems
            if type(item).__name__ == "FpText" and item.type == "value"
        )
        expected_layer = expected_reference_layer(reference)
        if reference_field.layer != expected_layer or not value_field.hide:
            raise RuntimeError(
                f"{reference}: placed fields differ from reviewed audit "
                f"(reference={reference_field.layer}, value_hide={value_field.hide})"
            )
        target_id = footprint_for(part_by_ref[reference])
        nickname, entry_name = target_id.split(":", 1)
        footprint.libraryNickname = nickname
        footprint.entryName = entry_name
        changed.append({"reference": reference, "library_id": target_id})

    if set(before) != PASSIVE_SYNC_REFS:
        missing = sorted(PASSIVE_SYNC_REFS - set(before))
        extra = sorted(set(before) - PASSIVE_SYNC_REFS)
        raise RuntimeError(f"PCB target mismatch: missing={missing} extra={extra}")

    board.to_file(str(BOARD_PATH))
    check = Board.from_file(str(BOARD_PATH))
    after = {
        reference_of(fp): placed_electrical_signature(fp)
        for fp in check.footprints
        if reference_of(fp) in PASSIVE_SYNC_REFS
    }
    if before != after:
        changed_refs = sorted(reference for reference in before if before[reference] != after.get(reference))
        raise RuntimeError(f"Placed electrical data changed: {changed_refs}")
    return board_sha_before, sha256_file(BOARD_PATH), sorted(changed, key=lambda item: item["reference"])


def update_schematics(part_by_ref):
    changed = []
    found = set()
    hashes_before = {}
    hashes_after = {}
    for path in sorted(SCHEMATIC_OUT.glob("*.kicad_sch")):
        hashes_before[path.name] = sha256_file(path)
        schematic = Schematic.from_file(str(path))
        file_changed = False
        for symbol in schematic.schematicSymbols:
            properties = {item.key: item for item in symbol.properties}
            reference = properties.get("Reference")
            if reference is None or reference.value not in PASSIVE_SYNC_REFS:
                continue
            ref = reference.value
            footprint_property = properties.get("Footprint")
            if footprint_property is None:
                raise RuntimeError(f"{path.name}:{ref} has no Footprint property")
            target_id = footprint_for(part_by_ref[ref])
            footprint_property.value = target_id
            found.add(ref)
            changed.append({"reference": ref, "schematic": path.name, "library_id": target_id})
            file_changed = True
        if file_changed:
            schematic.to_file(str(path))
        hashes_after[path.name] = sha256_file(path)
    if found != PASSIVE_SYNC_REFS:
        raise RuntimeError(
            f"Schematic target mismatch: missing={sorted(PASSIVE_SYNC_REFS-found)} "
            f"extra={sorted(found-PASSIVE_SYNC_REFS)}"
        )
    return hashes_before, hashes_after, sorted(changed, key=lambda item: item["reference"])


def main():
    if len(PASSIVE_SYNC_REFS) != 58:
        raise RuntimeError(f"Reviewed passive batch must contain 58 refs, got {len(PASSIVE_SYNC_REFS)}")
    part_by_ref = {part.ref: part for part in parts}
    if not PASSIVE_SYNC_REFS <= set(part_by_ref):
        raise RuntimeError("Passive sync manifest contains unknown references")

    variants, base_hashes = write_variants()
    board_before, board_after, board_changes = update_board(part_by_ref)
    schematic_before, schematic_after, schematic_changes = update_schematics(part_by_ref)
    payload = {
        "scope": {
            "instance_count": len(PASSIVE_SYNC_REFS),
            "fab_reference_count": len(PASSIVE_FAB_REF_REFS),
            "silk_reference_count": len(PASSIVE_HIDDEN_VALUE_REFS),
            "references": sorted(PASSIVE_SYNC_REFS),
        },
        "variant_libraries": variants,
        "base_library_sha256_unchanged": base_hashes,
        "board": {
            "sha256_before": board_before,
            "sha256_after": board_after,
            "changed_library_links": board_changes,
            "placed_electrical_signature_unchanged": True,
        },
        "schematics": {
            "sha256_before": schematic_before,
            "sha256_after": schematic_after,
            "changed_footprint_properties": schematic_changes,
        },
        "excluded": ["J2", "U4", "U5", "all non-listed footprint instances"],
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "HOMEKEY LOCK REV A — PASSIVE FOOTPRINT SYNC BATCH 1",
        "=" * 72,
        f"Reviewed instances: {len(PASSIVE_SYNC_REFS)}",
        f"Fab-reference variants: {len(PASSIVE_FAB_REF_REFS)}",
        f"Silk-reference/hidden-Value variants: {len(PASSIVE_HIDDEN_VALUE_REFS)}",
        f"Project-local variant footprints created: {len(variants)}",
        f"PCB SHA256 before: {board_before}",
        f"PCB SHA256 after:  {board_after}",
        "Placed pad/net/position/zone/attribute signatures unchanged: YES",
        f"PCB library links changed: {len(board_changes)}",
        f"Schematic Footprint properties changed: {len(schematic_changes)}",
        "Excluded and untouched: J2, U4, U5, and every unlisted instance",
        "",
        "RESULT: PASS (scope and electrical-invariance gates)",
    ]
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
