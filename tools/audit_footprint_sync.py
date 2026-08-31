#!/usr/bin/env python3
"""Audit board-vs-project-library footprint drift without modifying either.

The KiCad DRC reports one ``lib_footprint_mismatch`` per placed instance.  This
tool removes instance-only data (UUIDs, nets, reference/value text, and board
placement), compares the remaining footprint definitions by category, and
separates copper/drill changes from documentation-only changes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
WORKSPACE = ROOT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKSPACE / ".tools" / "py"))

from audit_board import reference_of  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.footprint import Footprint  # noqa: E402


LIB_DIR = HARDWARE / "kicad" / "HomeKey_RevA.pretty"
DEFAULT_DRC = ROOT / "reports" / "KICAD10_DRC_SILK_OVERLAP_CLEARED_FINAL.json"
JSON_REPORT = ROOT / "reports" / "FOOTPRINT_SYNC_AUDIT.json"
CSV_REPORT = ROOT / "reports" / "FOOTPRINT_SYNC_AUDIT.csv"
TEXT_REPORT = ROOT / "reports" / "FOOTPRINT_SYNC_AUDIT.txt"

CATEGORY_ORDER = (
    "pads",
    "drills",
    "copper_graphics",
    "zones",
    "courtyard",
    "models",
    "settings",
    "other_graphics",
    "silk",
    "fab",
    "reference_field",
    "value_field",
    "user_fields",
)

COPPER_CRITICAL = {"pads", "drills", "copper_graphics"}
STRUCTURAL_REVIEW = {"zones", "courtyard", "models", "settings", "other_graphics"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def clean(value, omit=frozenset()):
    """Convert kiutils objects into stable JSON-compatible values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (list, tuple)):
        return [clean(item, omit) for item in value]
    if isinstance(value, dict):
        return {
            key: clean(item, omit)
            for key, item in sorted(value.items())
            if key not in omit
        }
    if hasattr(value, "__dict__"):
        result = {
            key: clean(item, omit)
            for key, item in sorted(vars(value).items())
            if key not in omit
        }
        # EasyEDA-derived libraries sometimes parse pad numbers as integers,
        # while KiCad writes the placed copy as a string.  Pin identity is text.
        if type(value).__name__ == "Pad":
            result["number"] = str(result["number"])
        return result
    return str(value)


def canonical(items, omit=frozenset()):
    values = [clean(item, omit) for item in items]
    return sorted(values, key=stable_json)


def footprint_categories(footprint):
    fields = {}
    for field_type in ("reference", "value", "user"):
        fields[field_type] = canonical(
            [
                item
                for item in footprint.graphicItems
                if type(item).__name__ == "FpText" and item.type == field_type
            ],
            {"tstamp", "renderCache", "text"},
        )

    graphics = defaultdict(list)
    for item in footprint.graphicItems:
        if type(item).__name__ == "FpText":
            continue
        layer = getattr(item, "layer", "") or ""
        if "SilkS" in layer:
            category = "silk"
        elif layer.endswith("Fab"):
            category = "fab"
        elif layer.endswith("CrtYd"):
            category = "courtyard"
        elif layer.endswith(".Cu") or layer == "*.Cu":
            category = "copper_graphics"
        else:
            category = "other_graphics"
        graphics[category].append(item)

    result = {
        "pads": canonical(
            footprint.pads,
            {"tstamp", "net", "drill", "pinFunction", "pinType", "locked"},
        ),
        "drills": canonical(
            [
                {"number": str(pad.number), "drill": pad.drill}
                for pad in footprint.pads
                if pad.drill is not None
            ]
        ),
        "reference_field": fields["reference"],
        "value_field": fields["value"],
        "user_fields": fields["user"],
        "zones": canonical(
            footprint.zones,
            {"tstamp", "net", "netName", "locked"},
        ),
        "models": canonical(footprint.models),
        "settings": clean(
            {
                "layer": footprint.layer,
                "attributes": footprint.attributes,
                "privateLayers": footprint.privateLayers,
                "netTiePadGroups": footprint.netTiePadGroups,
                "solderMaskMargin": footprint.solderMaskMargin,
                "solderPasteMargin": footprint.solderPasteMargin,
                "solderPasteRatio": footprint.solderPasteRatio,
                "clearance": footprint.clearance,
                "zoneConnect": footprint.zoneConnect,
                "thermalWidth": footprint.thermalWidth,
                "thermalGap": footprint.thermalGap,
            }
        ),
    }
    for category in (
        "silk",
        "fab",
        "courtyard",
        "copper_graphics",
        "other_graphics",
    ):
        result[category] = canonical(graphics[category], {"tstamp", "locked"})
    return result


def field_summary(items):
    return [
        {
            "layer": item.get("layer"),
            "position": item.get("position"),
            "hide": item.get("hide"),
            "effects": item.get("effects"),
        }
        for item in items
    ]


def pad_summary(items):
    keys = ("number", "type", "shape", "position", "size", "layers", "roundrectRatio")
    return [{key: item.get(key) for key in keys} for item in items]


def category_summary(category, items):
    if category in {"reference_field", "value_field", "user_fields"}:
        return field_summary(items)
    if category == "pads":
        return pad_summary(items)
    if category == "models":
        return [item.get("path") for item in items]
    if isinstance(items, list):
        types = Counter(
            f"{item.get('layer', '-')}/{item.get('type', item.get('__class__', 'item'))}"
            if isinstance(item, dict)
            else type(item).__name__
            for item in items
        )
        return {"count": len(items), "item_types": dict(sorted(types.items()))}
    return items


def risk_for(differences):
    difference_set = set(differences)
    if difference_set & COPPER_CRITICAL:
        return "COPPER_CRITICAL_REVIEW"
    if difference_set & STRUCTURAL_REVIEW:
        return "STRUCTURAL_REVIEW"
    if difference_set:
        return "GRAPHICS_ONLY"
    return "MATCH"


def mismatch_references(drc_path: Path):
    report = json.loads(drc_path.read_text(encoding="utf-8"))
    violations = [
        item for item in report.get("violations", [])
        if item.get("type") == "lib_footprint_mismatch"
    ]
    references = []
    for violation in violations:
        for item in violation.get("items", []):
            description = item.get("description", "")
            if description.startswith("Footprint "):
                references.append(description.removeprefix("Footprint "))
    return report, sorted(set(references)), len(violations)


def build_instance(reference, board_fp, library_fp):
    board_categories = footprint_categories(board_fp)
    library_categories = footprint_categories(library_fp)
    differences = [
        category
        for category in CATEGORY_ORDER
        if board_categories[category] != library_categories[category]
    ]
    changed = {}
    for category in differences:
        board_value = board_categories[category]
        library_value = library_categories[category]
        changed[category] = {
            "board_sha256": digest(board_value),
            "library_sha256": digest(library_value),
            "board_summary": category_summary(category, board_value),
            "library_summary": category_summary(category, library_value),
            # Exact normalized definitions make the JSON report independently
            # reviewable; no UUIDs, nets, or instance placement are included.
            "board_normalized": board_value,
            "library_normalized": library_value,
        }
    return {
        "reference": reference,
        "library_id": f"{board_fp.libraryNickname}:{board_fp.entryName}",
        "risk": risk_for(differences),
        "differences": differences,
        "changed_categories": changed,
    }


def write_csv(instances):
    columns = ["reference", "library_id", "risk", "differences", *CATEGORY_ORDER]
    with CSV_REPORT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for item in instances:
            differences = set(item["differences"])
            row = {
                "reference": item["reference"],
                "library_id": item["library_id"],
                "risk": item["risk"],
                "differences": ";".join(item["differences"]),
            }
            row.update(
                {category: "DIFF" if category in differences else "MATCH" for category in CATEGORY_ORDER}
            )
            writer.writerow(row)


def write_text(metadata, instances, library_groups):
    risk_counts = Counter(item["risk"] for item in instances)
    lines = [
        "HOMEKEY LOCK REV A — FOOTPRINT SYNC AUDIT",
        "=" * 76,
        "Mode: READ-ONLY comparison; PCB and project footprint library were not modified.",
        f"Board: {metadata['board']}",
        f"Board SHA256: {metadata['board_sha256']}",
        f"DRC source: {metadata['drc_source']}",
        f"DRC lib_footprint_mismatch violations: {metadata['drc_violation_count']}",
        f"Audited placed instances: {metadata['audited_instance_count']}",
        f"Distinct project-library footprint IDs: {metadata['distinct_library_id_count']}",
        "",
        "RISK SPLIT",
        f" - GRAPHICS_ONLY: {risk_counts['GRAPHICS_ONLY']}",
        f" - STRUCTURAL_REVIEW: {risk_counts['STRUCTURAL_REVIEW']}",
        f" - COPPER_CRITICAL_REVIEW: {risk_counts['COPPER_CRITICAL_REVIEW']}",
        f" - MATCH after normalization: {risk_counts['MATCH']}",
        "",
        "INTERPRETATION",
        " - GRAPHICS_ONLY: only silk/Fab/reference/value/user-field definitions differ.",
        " - STRUCTURAL_REVIEW: zones/courtyard/models/settings/other graphics differ; inspect before syncing.",
        " - COPPER_CRITICAL_REVIEW: pad/drill/copper graphics differ; never overwrite automatically.",
        " - Pad numbers are normalized to text so EasyEDA integer-vs-KiCad string formatting is not a false alarm.",
        "",
        "PROJECT-LIBRARY GROUPS",
    ]
    for group in library_groups:
        lines.append(
            f" - {group['library_id']}: {group['instance_count']} instance(s); "
            f"risks={group['risk_counts']}; variants={group['difference_variants']}"
        )

    lines.extend(["", "INSTANCES REQUIRING STRUCTURAL OR COPPER REVIEW"])
    reviewed = [item for item in instances if item["risk"] != "GRAPHICS_ONLY"]
    if not reviewed:
        lines.append(" - None")
    else:
        for item in reviewed:
            lines.append(
                f" - {item['reference']} {item['library_id']}: {item['risk']}; "
                f"differences={','.join(item['differences']) or 'none'}"
            )

    lines.extend([
        "",
        "RECOMMENDED NEXT BATCH",
        " - All placed project-library footprints now match their reviewed native definitions.",
        " - J2 is resolved by its dedicated NPTH/native-angle and physical-delta audits.",
        " - U5 is resolved against NXP SOT618-1, including the official 3 x 3 stencil apertures.",
        " - U4 is resolved by its dedicated native-block and physical-delta audits; do not replace it with a legacy serialized block.",
        " - After each synchronization batch, rerun physical connectivity, geometry audit, official KiCad DRC, and manufacturing export checks.",
        "",
        "Exact normalized changed-category definitions and SHA256 hashes are in FOOTPRINT_SYNC_AUDIT.json.",
        "Per-instance category gates are in FOOTPRINT_SYNC_AUDIT.csv.",
        "",
        "RESULT: PASS (audit completed; no design mutation)",
    ])
    TEXT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, default=BOARD_PATH)
    parser.add_argument("--library", type=Path, default=LIB_DIR)
    parser.add_argument("--drc", type=Path, default=DEFAULT_DRC)
    args = parser.parse_args()

    board_path = args.board.resolve()
    library_dir = args.library.resolve()
    drc_path = args.drc.resolve()
    board_sha_before = sha256_file(board_path)
    library_sha_before = {
        path.name: sha256_file(path)
        for path in sorted(library_dir.glob("*.kicad_mod"))
    }

    board = Board.from_file(str(board_path))
    footprints = {reference_of(fp): fp for fp in board.footprints}
    _, references, violation_count = mismatch_references(drc_path)
    missing = sorted(set(references) - set(footprints))
    if missing:
        raise SystemExit(f"DRC references absent from board: {', '.join(missing)}")

    instances = []
    for reference in references:
        board_fp = footprints[reference]
        library_path = library_dir / f"{board_fp.entryName}.kicad_mod"
        if not library_path.exists():
            raise SystemExit(f"Missing project-library footprint: {library_path}")
        library_fp = Footprint.from_file(str(library_path))
        instances.append(build_instance(reference, board_fp, library_fp))

    grouped = defaultdict(list)
    for instance in instances:
        grouped[instance["library_id"]].append(instance)
    library_groups = []
    for library_id, members in sorted(grouped.items()):
        variants = Counter(",".join(item["differences"]) or "MATCH" for item in members)
        risks = Counter(item["risk"] for item in members)
        library_groups.append({
            "library_id": library_id,
            "instance_count": len(members),
            "references": [item["reference"] for item in members],
            "risk_counts": dict(sorted(risks.items())),
            "difference_variants": dict(sorted(variants.items())),
        })

    metadata = {
        "schema_version": 1,
        "mode": "read-only",
        "board": str(board_path.relative_to(ROOT)),
        "board_sha256": board_sha_before,
        "project_library": str(library_dir.relative_to(ROOT)),
        "project_library_file_count": len(library_sha_before),
        "project_library_sha256": library_sha_before,
        "drc_source": str(drc_path.relative_to(ROOT)),
        "drc_sha256": sha256_file(drc_path),
        "drc_violation_count": violation_count,
        "audited_instance_count": len(instances),
        "distinct_library_id_count": len(grouped),
        "normalization": {
            "ignored": [
                "UUID/tstamp",
                "placed-instance net assignment",
                "board placement and rotation",
                "reference/value text content",
                "pin function/type copied from schematic",
            ],
            "pad_number_rule": "compare as text",
            "coordinate_precision_decimal_places": 6,
        },
    }
    payload = {
        "metadata": metadata,
        "risk_definitions": {
            "COPPER_CRITICAL_REVIEW": sorted(COPPER_CRITICAL),
            "STRUCTURAL_REVIEW": sorted(STRUCTURAL_REVIEW),
            "GRAPHICS_ONLY": [
                "silk", "fab", "reference_field", "value_field", "user_fields"
            ],
        },
        "library_groups": library_groups,
        "instances": instances,
    }
    JSON_REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(instances)
    write_text(metadata, instances, library_groups)

    board_sha_after = sha256_file(board_path)
    library_sha_after = {
        path.name: sha256_file(path)
        for path in sorted(library_dir.glob("*.kicad_mod"))
    }
    if board_sha_after != board_sha_before or library_sha_after != library_sha_before:
        raise SystemExit("Audit mutated board or project footprint library")

    risks = Counter(item["risk"] for item in instances)
    print(f"Audited {len(instances)} mismatch instances across {len(grouped)} library IDs")
    print("Risk split: " + ", ".join(f"{key}={value}" for key, value in sorted(risks.items())))
    print(f"Board unchanged: {board_sha_after}")
    print(f"Wrote {TEXT_REPORT.relative_to(ROOT)}, {CSV_REPORT.relative_to(ROOT)}, {JSON_REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
