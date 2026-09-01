#!/usr/bin/env python3
"""Install the reviewed KiCad-10-native U4 footprint block into Rev A."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATE = ROOT / "design" / "U4_NATIVE_BOARD_BLOCK.kicad_snippet"
MARKER = '(footprint "HomeKey_RevA:ESP32-C6-MINI-1"'
REPORT_JSON = ROOT / "reports" / "U4_NATIVE_SYNC.json"
REPORT_TEXT = ROOT / "reports" / "U4_NATIVE_SYNC.txt"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def footprint_span(text: str, marker: str = MARKER) -> tuple[int, int]:
    if text.count(marker) != 1:
        raise RuntimeError(f"Expected exactly one U4 marker, found {text.count(marker)}")
    marker_offset = text.index(marker)
    start = text.rfind("\n", 0, marker_offset) + 1
    depth = 0
    started = False
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
            started = True
        elif char == ")":
            depth -= 1
            if started and depth == 0:
                return start, index + 1
    raise RuntimeError("Unbalanced U4 footprint block")


def validate_template(template: str) -> None:
    required = {
        MARKER: 1,
        '(property "Reference" "U4"': 1,
        '(property "Value" "ESP32-C6-MINI-1-N4"': 1,
        '(path "/generated/U4")': 1,
        '(zone\n': 1,
        '(layers "F.Cu" "B.Cu" "In1.Cu" "In2.Cu")': 1,
    }
    for token, count in required.items():
        actual = template.count(token)
        if actual != count:
            raise RuntimeError(
                f"U4 native template token count mismatch for {token!r}: {actual} != {count}"
            )
    if template.count("(pad ") != 61:
        raise RuntimeError("U4 native template pad count is not 61")
    if template.count("(net ") != 48:
        raise RuntimeError("U4 native template connected-pad net count is not 48")
    if '(net 0 "")' in template or '(net "' in template:
        raise RuntimeError("U4 native template contains non-canonical pad net syntax")


def install_u4_native_block(board_path: Path) -> dict:
    template = TEMPLATE.read_text(encoding="utf-8").rstrip("\n")
    validate_template(template)
    board_before = sha256_file(board_path)
    text = board_path.read_text(encoding="utf-8")
    start, end = footprint_span(text)
    previous = text[start:end]
    already_native = previous == template
    if not already_native:
        board_path.write_text(text[:start] + template + text[end:], encoding="utf-8")
    return {
        "board": str(board_path),
        "board_sha256_before": board_before,
        "board_sha256_after": sha256_file(board_path),
        "template_sha256": sha256_file(TEMPLATE),
        "previous_block_bytes": len(previous.encode("utf-8")),
        "native_block_bytes": len(template.encode("utf-8")),
        "already_native": already_native,
        "changed": not already_native,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", type=Path)
    args = parser.parse_args()
    result = install_u4_native_block(args.board.resolve())
    REPORT_JSON.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "HOMEKEY LOCK REV A — U4 NATIVE FOOTPRINT SYNC",
        "=" * 72,
        f"Changed: {result['changed']}",
        f"Previous block bytes: {result['previous_block_bytes']}",
        f"Native block bytes: {result['native_block_bytes']}",
        f"Board SHA256 before: {result['board_sha256_before']}",
        f"Board SHA256 after:  {result['board_sha256_after']}",
        f"Template SHA256:     {result['template_sha256']}",
    ]
    REPORT_TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
