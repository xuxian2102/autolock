#!/usr/bin/env python3
"""Synchronize reviewed procurement fields into existing schematic instances.

This is intentionally metadata-only: it updates LCSC, MPN, and Manufacturer
properties for the reviewed Rev A batch without regenerating symbols, UUIDs,
wires, footprints, or placements.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from design_data import part_by_ref


ROOT = Path(__file__).resolve().parent.parent
HARDWARE = ROOT / "hardware"
SCHEMATIC_DIR = HARDWARE / "kicad" / "schematics"
REPORT = ROOT / "reports" / "PROCUREMENT_METADATA_SYNC.json"

TARGET_REFS = frozenset({
    "C3", "C7", "C11", "C27", "C28", "C29", "C30", "C39", "D5",
    "R4", "R5", "R9", "R11", "R18", "R19", "R20", "R21", "R30",
    "SW1", "SW2", "SW3",
})

FIELDS = {
    "LCSC": "lcsc",
    "MPN": "mpn",
    "Manufacturer": "manufacturer",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sync_file(path: Path):
    original = path.read_text(encoding="utf-8")
    chunks = re.split(r"(?=^  \(symbol \(lib_id )", original, flags=re.MULTILINE)
    changes = []
    seen = set()

    for index, chunk in enumerate(chunks):
        reference_match = re.search(r'^    \(property "Reference" "([^"]+)"', chunk, re.MULTILINE)
        if not reference_match:
            continue
        reference = reference_match.group(1)
        if reference not in TARGET_REFS:
            continue
        seen.add(reference)
        part = part_by_ref(reference)
        for property_name, attribute in FIELDS.items():
            expected = getattr(part, attribute)
            pattern = re.compile(
                rf'(^    \(property "{re.escape(property_name)}" ")[^"]*(" \(id \d+\) \(at [^\n]+$)',
                re.MULTILINE,
            )
            match = pattern.search(chunks[index])
            if not match:
                raise RuntimeError(f"{path.name}:{reference}: missing {property_name} property")
            previous = match.group(0)[len(match.group(1)):-len(match.group(2))]
            if previous == expected:
                continue
            chunks[index] = pattern.sub(rf'\g<1>{expected}\g<2>', chunks[index], count=1)
            changes.append({
                "reference": reference,
                "field": property_name,
                "before": previous,
                "after": expected,
            })

    updated = "".join(chunks)
    if changes:
        path.write_text(updated, encoding="utf-8")
    return {
        "file": str(path.relative_to(ROOT)),
        "before_sha256": sha256(original.encode()),
        "after_sha256": sha256(updated.encode()),
        "seen_target_refs": sorted(seen),
        "changes": changes,
    }


def main():
    for reference in TARGET_REFS:
        part = part_by_ref(reference)
        if not part.lcsc or not part.mpn or not part.manufacturer:
            raise RuntimeError(f"{reference}: incomplete reviewed procurement metadata")

    results = [sync_file(path) for path in sorted(SCHEMATIC_DIR.glob("*.kicad_sch"))]
    seen = {reference for result in results for reference in result["seen_target_refs"]}
    if seen != TARGET_REFS:
        raise RuntimeError(f"target instance mismatch: missing={sorted(TARGET_REFS-seen)} extra={sorted(seen-TARGET_REFS)}")

    report = {
        "scope": "metadata only: LCSC, MPN, Manufacturer",
        "target_refs": sorted(TARGET_REFS),
        "target_count": len(TARGET_REFS),
        "files": results,
        "field_change_count": sum(len(result["changes"]) for result in results),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Synchronized {len(TARGET_REFS)} refs; {report['field_change_count']} property values changed")


if __name__ == "__main__":
    main()
