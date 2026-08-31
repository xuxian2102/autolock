#!/usr/bin/env python3
"""Convert only J2 child angles to KiCad's native board-coordinate form."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MARKER = '(footprint "HomeKey_RevA:USB-C_SMD-TYPE-C-31-M-12_1"'
REPORT_JSON = ROOT / "reports/J2_NATIVE_SYNC.json"
REPORT_TEXT = ROOT / "reports/J2_NATIVE_SYNC.txt"
CHILD_AT = re.compile(
    r"(?m)^(\s+\((fp_text|pad)\b.*?\(at\s+)"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)(?:\s+([-+0-9.eE]+))?(\))"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def footprint_span(text: str) -> tuple[int, int]:
    if text.count(MARKER) != 1:
        raise RuntimeError(f"Expected one J2 footprint marker, found {text.count(MARKER)}")
    marker_offset = text.index(MARKER)
    start = text.rfind("\n", 0, marker_offset) + 1
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return start, index + 1
    raise RuntimeError("Unbalanced J2 footprint block")


def sync_j2_native_angles(board_path: Path) -> dict:
    before_sha = sha256_file(board_path)
    text = board_path.read_text(encoding="utf-8")
    start, end = footprint_span(text)
    block = text[start:end]
    matches = list(CHILD_AT.finditer(block))
    counts = {
        "fp_text": sum(match.group(2) == "fp_text" for match in matches),
        "pad": sum(match.group(2) == "pad" for match in matches),
    }
    if counts != {"fp_text": 3, "pad": 18}:
        raise RuntimeError(f"Unexpected J2 child counts: {counts}")
    angles = [float(match.group(5) or 0) % 360 for match in matches]
    if all(abs(angle - 180) < 1e-9 for angle in angles):
        changed = False
    elif all(abs(angle) < 1e-9 for angle in angles):
        def replacement(match):
            return f"{match.group(1)}{match.group(3)} {match.group(4)} 180{match.group(6)}"

        native_block = CHILD_AT.sub(replacement, block)
        board_path.write_text(text[:start] + native_block + text[end:], encoding="utf-8")
        changed = True
    else:
        raise RuntimeError(f"J2 contains mixed or unexpected child angles: {sorted(set(angles))}")
    return {
        "board": str(board_path),
        "board_sha256_before": before_sha,
        "board_sha256_after": sha256_file(board_path),
        "child_counts": counts,
        "native_angle_degrees": 180,
        "changed": changed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", type=Path)
    args = parser.parse_args()
    result = sync_j2_native_angles(args.board.resolve())
    REPORT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "HOMEKEY LOCK REV A — J2 NATIVE CHILD-ANGLE SYNC",
        "=" * 76,
        f"Changed: {result['changed']}",
        f"Children: {result['child_counts']['fp_text']} text / {result['child_counts']['pad']} pads",
        f"Native angle: {result['native_angle_degrees']} degrees",
        f"Board SHA256 before: {result['board_sha256_before']}",
        f"Board SHA256 after:  {result['board_sha256_after']}",
    ]
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
