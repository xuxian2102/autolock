#!/usr/bin/env python3
"""Apply the reviewed KiCad child-angle correction to 58 passive instances."""

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
from design_data import PASSIVE_SYNC_REFS  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402


REPORT_JSON = ROOT / "reports" / "PASSIVE_ROTATION_FIX.json"
REPORT_TEXT = ROOT / "reports" / "PASSIVE_ROTATION_FIX.txt"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def board_angle(local_angle, footprint_angle):
    value = (float(local_angle or 0) + float(footprint_angle or 0)) % 360
    return 0 if abs(value) < 1e-9 else value


def main() -> None:
    board_sha_before = sha256_file(BOARD_PATH)
    board = Board.from_file(str(BOARD_PATH))
    changes = []
    seen = set()

    for footprint in board.footprints:
        reference = reference_of(footprint)
        if reference not in PASSIVE_SYNC_REFS:
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
        changes.append({
            "reference": reference,
            "footprint_angle": footprint_angle,
            "text_angles": text_changes,
            "pad_angles": pad_changes,
        })

    if seen != PASSIVE_SYNC_REFS:
        raise RuntimeError(
            f"Target mismatch: missing={sorted(PASSIVE_SYNC_REFS-seen)} "
            f"extra={sorted(seen-PASSIVE_SYNC_REFS)}"
        )
    if any(item["footprint_angle"] not in {90.0, 180.0} for item in changes):
        raise RuntimeError("Reviewed batch contains an unexpected footprint angle")

    board.to_file(str(BOARD_PATH), encoding="utf-8")
    payload = {
        "scope": "58 reviewed passive footprint instances only",
        "target_count": len(changes),
        "board_sha256_before": board_sha_before,
        "board_sha256_after": sha256_file(BOARD_PATH),
        "changes": sorted(changes, key=lambda item: item["reference"]),
    }
    REPORT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    text_count = sum(len(item["text_angles"]) for item in changes)
    pad_count = sum(len(item["pad_angles"]) for item in changes)
    lines = [
        "HOMEKEY LOCK REV A — PASSIVE ROTATION FIX",
        "=" * 72,
        f"Target instances: {len(changes)}",
        f"Text child angles updated: {text_count}",
        f"Pad child angles updated: {pad_count}",
        f"Board SHA256 before: {payload['board_sha256_before']}",
        f"Board SHA256 after:  {payload['board_sha256_after']}",
    ]
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
