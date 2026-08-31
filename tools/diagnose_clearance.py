#!/usr/bin/env python3
"""Name the two concrete copper primitives behind every geometry violation."""

from __future__ import annotations

import math
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT.parent / ".tools" / "py"))

from audit_board import COPPER_LAYERS, pad_geometry, reference_of  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.items.brditems import Segment, Via  # noqa: E402
from shapely.geometry import LineString, Point  # noqa: E402


PAIR_RE = re.compile(
    r"(?:COPPER (?:SHORT/TOUCH|CLEARANCE [^:]+): )"
    r"(?P<layer>\S+) (?P<first>\S+) <> (?P<second>\S+)"
)


def primitives(board):
    result = defaultdict(list)
    for footprint in board.footprints:
        reference = reference_of(footprint)
        for index, pad in enumerate(footprint.pads):
            if pad.type == "np_thru_hole":
                continue
            net = (
                pad.net.name if pad.net is not None and pad.net.name
                else f"#NC:{reference}.{pad.number}:{index}"
            )
            layers = (
                COPPER_LAYERS if "*.Cu" in pad.layers
                else tuple(layer for layer in pad.layers if layer in COPPER_LAYERS)
            )
            geometry = pad_geometry(footprint, pad)
            for layer in layers:
                result[(layer, net)].append((f"pad {reference}.{pad.number}[{index}]", geometry))

    for index, item in enumerate(board.traceItems):
        net = board.nets[item.net].name
        if isinstance(item, Segment):
            geometry = LineString(
                [(item.start.X, item.start.Y), (item.end.X, item.end.Y)]
            ).buffer(item.width / 2, cap_style=1, join_style=1)
            result[(item.layer, net)].append(
                (f"track[{index}] ({item.start.X:.3f},{item.start.Y:.3f})"
                 f"-({item.end.X:.3f},{item.end.Y:.3f}) w={item.width:.3f}", geometry)
            )
        elif isinstance(item, Via):
            geometry = Point(item.position.X, item.position.Y).buffer(item.size / 2, resolution=20)
            for layer in COPPER_LAYERS:
                result[(layer, net)].append(
                    (f"via[{index}] ({item.position.X:.3f},{item.position.Y:.3f})"
                     f" d={item.size:.3f}/{item.drill:.3f}", geometry)
                )
    return result


def main():
    board = Board.from_file(str(BOARD_PATH))
    items = primitives(board)
    audit = (ROOT / "reports" / "GEOMETRY_AUDIT.txt").read_text(encoding="utf-8")
    for line in audit.splitlines():
        match = PAIR_RE.search(line)
        if not match:
            continue
        layer, first, second = match.group("layer", "first", "second")
        best = None
        for first_label, first_geometry in items[(layer, first)]:
            for second_label, second_geometry in items[(layer, second)]:
                distance = first_geometry.distance(second_geometry)
                if best is None or distance < best[0]:
                    best = (distance, first_label, second_label)
        if best is None:
            continue
        print(f"{layer:6s} {first:18s} <> {second:18s} {best[0]:.4f} mm")
        print(f"  {best[1]}")
        print(f"  {best[2]}")


if __name__ == "__main__":
    main()
