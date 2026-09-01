#!/usr/bin/env python3
"""Apply the reviewed NXP SOT618-1 stencil and native angles to U5 only."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MARKER = '(footprint "HomeKey_RevA:HVQFN-40_L6.0-W6.0-P0.50-BL-EP4.1"'
REPORT_JSON = ROOT / "reports/U5_NATIVE_SYNC.json"
REPORT_TEXT = ROOT / "reports/U5_NATIVE_SYNC.txt"
CHILD_AT = re.compile(
    r"(?m)^(\s+\((fp_text|pad)\b.*?\(at\s+)"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)(?:\s+([-+0-9.eE]+))?(\))"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expression_span(text: str, start: int) -> tuple[int, int]:
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
    raise RuntimeError("Unbalanced S-expression")


def footprint_span(text: str) -> tuple[int, int]:
    if text.count(MARKER) != 1:
        raise RuntimeError(f"Expected one U5 footprint marker, found {text.count(MARKER)}")
    marker_offset = text.index(MARKER)
    start = text.rfind("\n", 0, marker_offset) + 1
    return expression_span(text, start)


def official_paste_pads() -> str:
    lines = []
    for y in (-0.9, 0.0, 0.9):
        for x in (-0.9, 0.0, 0.9):
            stamp = uuid5(NAMESPACE_URL, f"homekey-lock-reva-u5-paste-{x:.1f}-{y:.1f}")
            lines.append(
                f'    (pad "" smd rect (at {x:g} {y:g}) (size 0.6 0.6) '
                f'(layers "F.Paste") (tstamp {stamp}))'
            )
    return "\n".join(lines)


def add_native_angle(match: re.Match) -> str:
    local = float(match.group(5) or 0)
    angle = (local + 180.0) % 360.0
    rendered = str(int(angle)) if angle.is_integer() else f"{angle:g}"
    return f"{match.group(1)}{match.group(3)} {match.group(4)} {rendered}{match.group(6)}"


def sync_u5(board_path: Path) -> dict:
    before_sha = sha256_file(board_path)
    text = board_path.read_text(encoding="utf-8")
    start, end = footprint_span(text)
    block = text[start:end]
    initial_pad_count = sum(match.group(2) == "pad" for match in CHILD_AT.finditer(block))

    if initial_pad_count == 41:
        pad41_marker = '(pad "41" smd rect'
        if block.count(pad41_marker) != 1:
            raise RuntimeError("Expected exactly one U5 exposed pad")
        pstart = block.index(pad41_marker)
        pstart = block.rfind("(", 0, pstart + 1)
        pstart, pend = expression_span(block, pstart)
        pad41 = block[pstart:pend]
        layers_before = '(layers "F.Cu" "F.Paste" "F.Mask")'
        if pad41.count(layers_before) != 1:
            raise RuntimeError("U5 pad 41 does not have the expected legacy full-paste layers")
        pad41 = pad41.replace(layers_before, '(layers "F.Cu" "F.Mask")')
        block = block[:pstart] + pad41 + "\n" + official_paste_pads() + block[pend:]
        stencil_changed = True
    elif initial_pad_count == 50:
        stencil_changed = False
    else:
        raise RuntimeError(f"Unexpected U5 pad count: {initial_pad_count}")

    courtyard = {
        '(start -3 3) (end -3 -3)': '(start -3.625 3.625) (end -3.625 -3.625)',
        '(start -3 -3) (end 3 -3)': '(start -3.625 -3.625) (end 3.625 -3.625)',
        '(start 3 -3) (end 3 3)': '(start 3.625 -3.625) (end 3.625 3.625)',
        '(start 3 3) (end -3 3)': '(start 3.625 3.625) (end -3.625 3.625)',
    }
    courtyard_changed = False
    for before, after in courtyard.items():
        count = block.count(before)
        if count == 1:
            block = block.replace(before, after)
            courtyard_changed = True
        elif count != 0:
            raise RuntimeError(f"Unexpected U5 courtyard edge count: {before} -> {count}")

    matches = list(CHILD_AT.finditer(block))
    text_angles = [float(match.group(5) or 0) % 360 for match in matches if match.group(2) == "fp_text"]
    if len(text_angles) != 3:
        raise RuntimeError(f"Unexpected U5 text field count: {len(text_angles)}")
    if all(abs(angle) < 1e-9 for angle in text_angles):
        block = CHILD_AT.sub(add_native_angle, block)
        angle_changed = True
    elif all(abs(angle - 180) < 1e-9 for angle in text_angles):
        angle_changed = False
    else:
        raise RuntimeError(f"Mixed U5 text angles: {sorted(set(text_angles))}")

    final_matches = list(CHILD_AT.finditer(block))
    counts = {
        "fp_text": sum(match.group(2) == "fp_text" for match in final_matches),
        "pad": sum(match.group(2) == "pad" for match in final_matches),
    }
    if counts != {"fp_text": 3, "pad": 50}:
        raise RuntimeError(f"Unexpected final U5 child counts: {counts}")
    required_fragments = [
        '(pad "41" smd rect (at 0 0 180) (size 4.1 4.1) (layers "F.Cu" "F.Mask")',
        '(size 0.6 0.6) (layers "F.Paste")',
        '(start -3.625 3.625) (end -3.625 -3.625)',
    ]
    if required_fragments[0] not in block:
        raise RuntimeError("U5 exposed pad is not in reviewed native form")
    if block.count(required_fragments[1]) != 9:
        raise RuntimeError("U5 does not contain nine official 0.60 mm paste apertures")
    if required_fragments[2] not in block:
        raise RuntimeError("U5 courtyard was not expanded to the reviewed extent")

    changed = stencil_changed or courtyard_changed or angle_changed
    if changed:
        board_path.write_text(text[:start] + block + text[end:], encoding="utf-8")
    return {
        "board": str(board_path),
        "board_sha256_before": before_sha,
        "board_sha256_after": sha256_file(board_path),
        "initial_pad_count": initial_pad_count,
        "final_child_counts": counts,
        "stencil_changed": stencil_changed,
        "courtyard_changed": courtyard_changed,
        "native_angles_changed": angle_changed,
        "changed": changed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", type=Path)
    args = parser.parse_args()
    result = sync_u5(args.board.resolve())
    REPORT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "HOMEKEY LOCK REV A — U5 NXP SOT618-1 / NATIVE SYNC",
        "=" * 76,
        f"Changed: {result['changed']}",
        f"Pads: {result['initial_pad_count']} -> {result['final_child_counts']['pad']}",
        f"Stencil changed: {result['stencil_changed']}",
        f"Courtyard changed: {result['courtyard_changed']}",
        f"Native angles changed: {result['native_angles_changed']}",
        f"Board SHA256 before: {result['board_sha256_before']}",
        f"Board SHA256 after:  {result['board_sha256_after']}",
    ]
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
