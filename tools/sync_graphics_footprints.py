#!/usr/bin/env python3
"""Apply the reviewed 11-instance graphics-only footprint sync batch.

The rotated instances are converted to KiCad's board-coordinate child-angle
convention.  Q1 also receives the reviewed pin-1 silk-arc correction, and its
duplicate footprint-level LCSC property is removed.  No pads, nets, positions,
zones, copper primitives, or schematic data are otherwise changed.
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
from design_data import GRAPHICS_SYNC_REFS  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.items.common import Position  # noqa: E402


REPORT_JSON = ROOT / "reports" / "GRAPHICS_FOOTPRINT_SYNC.json"
REPORT_TEXT = ROOT / "reports" / "GRAPHICS_FOOTPRINT_SYNC.txt"
EXPECTED_BOARD_SHA256 = (
    "68fd605992097e5ed051526ba7873be3688777c197c7510c0f2ebb9e833b85b1"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def board_angle(local_angle, footprint_angle):
    value = (float(local_angle or 0) + float(footprint_angle or 0)) % 360
    return 0 if abs(value) < 1e-9 else value


def point_tuple(point) -> tuple[float, float]:
    return round(float(point.X), 6), round(float(point.Y), 6)


def main() -> None:
    board_sha_before = sha256_file(BOARD_PATH)
    if board_sha_before != EXPECTED_BOARD_SHA256:
        raise RuntimeError(
            "Refusing to apply graphics sync to an unexpected PCB revision: "
            f"{board_sha_before}"
        )

    board = Board.from_file(str(BOARD_PATH))
    seen = set()
    changes = []
    q1_arc_changed = False
    q1_property_removed = False

    for footprint in board.footprints:
        reference = reference_of(footprint)
        if reference not in GRAPHICS_SYNC_REFS:
            continue
        seen.add(reference)
        footprint_angle = float(footprint.position.angle or 0)
        text_changes = []
        pad_changes = []

        for item in footprint.graphicItems:
            if type(item).__name__ != "FpText":
                continue
            before = item.position.angle
            after = board_angle(before, footprint_angle)
            item.position.angle = after
            text_changes.append({
                "type": item.type,
                "before": before,
                "after": after,
            })

        for pad in footprint.pads:
            before = pad.position.angle
            after = board_angle(before, footprint_angle)
            pad.position.angle = after
            pad_changes.append({
                "number": str(pad.number),
                "before": before,
                "after": after,
            })

        if reference == "Q1":
            lcsc = footprint.properties.pop("LCSC Part", None)
            if lcsc != "C16072":
                raise RuntimeError(f"Q1: unexpected footprint LCSC property: {lcsc!r}")
            q1_property_removed = True

            matching_arcs = [
                item for item in footprint.graphicItems
                if (
                    type(item).__name__ == "FpArc"
                    and item.layer == "F.SilkS"
                    and point_tuple(item.start) == (-2.53, -0.01)
                    and point_tuple(item.mid) == (0.0, 0.0)
                    and point_tuple(item.end) == (-2.53, -0.45)
                )
            ]
            if len(matching_arcs) != 1:
                raise RuntimeError(
                    f"Q1: expected exactly one legacy silk arc, found {len(matching_arcs)}"
                )
            arc = matching_arcs[0]
            arc.start = Position(-2.53, -0.45)
            arc.mid = Position(-2.09, -0.009347)
            arc.end = Position(-2.531306, 0.429998)
            q1_arc_changed = True

        changes.append({
            "reference": reference,
            "footprint_angle": footprint_angle,
            "text_angles": text_changes,
            "pad_angles": pad_changes,
        })

    if seen != GRAPHICS_SYNC_REFS:
        raise RuntimeError(
            f"Target mismatch: missing={sorted(GRAPHICS_SYNC_REFS-seen)} "
            f"extra={sorted(seen-GRAPHICS_SYNC_REFS)}"
        )
    if not q1_arc_changed or not q1_property_removed:
        raise RuntimeError("Q1 reviewed corrections were not both applied")

    board.to_file(str(BOARD_PATH), encoding="utf-8")
    payload = {
        "scope": "11 reviewed graphics-only footprint instances",
        "target_count": len(changes),
        "references": sorted(GRAPHICS_SYNC_REFS),
        "board_sha256_before": board_sha_before,
        "board_sha256_after": sha256_file(BOARD_PATH),
        "q1_silk_arc_corrected": q1_arc_changed,
        "q1_duplicate_footprint_lcsc_property_removed": q1_property_removed,
        "changes": sorted(changes, key=lambda item: item["reference"]),
    }
    REPORT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    text_count = sum(len(item["text_angles"]) for item in changes)
    pad_count = sum(len(item["pad_angles"]) for item in changes)
    lines = [
        "HOMEKEY LOCK REV A — GRAPHICS FOOTPRINT SYNC",
        "=" * 72,
        f"Target instances: {len(changes)}",
        f"Text child angles normalized: {text_count}",
        f"Pad child angles normalized: {pad_count}",
        "Q1 silk arc corrected: yes",
        "Q1 duplicate footprint LCSC property removed: yes",
        f"Board SHA256 before: {payload['board_sha256_before']}",
        f"Board SHA256 after:  {payload['board_sha256_after']}",
    ]
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
