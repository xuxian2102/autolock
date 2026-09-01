#!/usr/bin/env python3
"""Independently prove that every component terminal reaches its net copper.

Copper on this board comes from three places: pads, routed tracks and vias,
and the two ground pours that pour_ground_planes.py adds.  The pours ship
unfilled -- kicad-cli fills them when it runs DRC and when it writes the
Gerbers -- so this audit models a pour as its outline minus every copper-pour
keep-out, which is the area the filler is allowed to cover.

That model is deliberately optimistic about the *inside* of a pour: it does
not subtract the clearance the filler leaves around foreign nets, so it cannot
prove that two points inside one pour really share copper.  It does not have
to.  run_official_drc.py runs KiCad's own filler with --refill-zones and
requires zero unconnected items, which is that proof.  What this audit adds,
and DRC does not, is a check independent of KiCad on everything else: that no
terminal is stranded off the copper of its own net entirely.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy  # noqa: F401

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT.parent / ".tools" / "py"))

from audit_board import COPPER_LAYERS, pad_geometry, reference_of  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.items.brditems import Segment, Via  # noqa: E402
from shapely.geometry import LineString, Point, Polygon  # noqa: E402
from shapely.ops import unary_union  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402


REPORT = ROOT / "reports" / "PHYSICAL_CONNECTIVITY_AUDIT.txt"


def zone_shape(zone):
    return Polygon([(point.X, point.Y) for point in zone.polygons[0].coordinates])


def pour_geometry(board):
    """Where each pour may put copper: outline minus the copper keep-outs."""
    blocked = defaultdict(list)
    for zone in board.zones:
        settings = zone.keepoutSettings
        if settings is None or settings.copperpour != "not_allowed":
            continue
        for layer in zone.layers:
            blocked[layer].append(zone_shape(zone))

    pours = []
    for zone in board.zones:
        if zone.keepoutSettings is not None or not zone.netName:
            continue
        for layer in zone.layers:
            shape = zone_shape(zone)
            if blocked[layer]:
                shape = shape.difference(unary_union(blocked[layer]))
            if not shape.is_empty:
                pours.append((layer, zone.netName, zone.name or "pour", shape))
    return pours


class UnionFind:
    def __init__(self, count):
        self.parent = list(range(count))

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, first, second):
        first, second = self.find(first), self.find(second)
        if first != second:
            self.parent[second] = first


def main():
    board = Board.from_file(str(BOARD_PATH))
    entries = []
    by_layer_net = defaultdict(list)
    pad_ids = defaultdict(list)
    terminal_ids = defaultdict(list)
    via_ids = defaultdict(list)

    def add(layer, net, label, geometry, kind, identity=None):
        index = len(entries)
        entries.append((layer, net, label, geometry, kind))
        by_layer_net[(layer, net)].append(index)
        if kind == "pad":
            pad_ids[net].append(index)
            terminal_ids[identity].append(index)
        elif kind == "via":
            via_ids[identity].append(index)

    for footprint in board.footprints:
        reference = reference_of(footprint)
        for pad_index, pad in enumerate(footprint.pads):
            if pad.type == "np_thru_hole" or pad.net is None or not pad.net.name:
                continue
            net = pad.net.name
            layers = (
                COPPER_LAYERS if "*.Cu" in pad.layers
                else tuple(layer for layer in pad.layers if layer in COPPER_LAYERS)
            )
            for layer in layers:
                add(
                    layer, net, f"{reference}.{pad.number}[{pad_index}]",
                    pad_geometry(footprint, pad), "pad",
                    (net, reference, str(pad.number)),
                )

    for trace_index, item in enumerate(board.traceItems):
        net = board.nets[item.net].name
        if isinstance(item, Segment):
            add(
                item.layer, net, f"track[{trace_index}]",
                LineString([(item.start.X, item.start.Y), (item.end.X, item.end.Y)]).buffer(
                    item.width / 2, cap_style=1, join_style=1
                ),
                "track",
            )
        elif isinstance(item, Via):
            geometry = Point(item.position.X, item.position.Y).buffer(item.size / 2, resolution=20)
            for layer in COPPER_LAYERS:
                add(layer, net, f"via[{trace_index}]", geometry, "via", trace_index)

    for layer, net, name, shape in pour_geometry(board):
        add(layer, net, f"zone[{name}]", shape, "zone")

    union = UnionFind(len(entries))
    # Same numbered pads in one footprint are one component terminal (for
    # example tactile-switch duplicated contacts and the antenna return).
    for group in terminal_ids.values():
        for item in group[1:]:
            union.union(group[0], item)
    # A plated via is one conductor through all four copper layers.
    for group in via_ids.values():
        for item in group[1:]:
            union.union(group[0], item)

    for indices in by_layer_net.values():
        geometries = [entries[index][3] for index in indices]
        tree = STRtree(geometries)
        geometry_to_locals = defaultdict(list)
        for local, geometry in enumerate(geometries):
            # Shapely 1.x may return cloned geometry wrappers from STRtree, so
            # object identity is not stable.  WKB is stable and duplicates are
            # retained rather than silently collapsed.
            geometry_to_locals[geometry.wkb].append(local)
        for local, geometry in enumerate(geometries):
            for candidate in tree.query(geometry):
                if hasattr(candidate, "wkb"):
                    candidate_locals = geometry_to_locals[candidate.wkb]
                    candidate_geometry = candidate
                else:
                    candidate_locals = [int(candidate)]
                    candidate_geometry = geometries[int(candidate)]
                for other_local in candidate_locals:
                    if other_local <= local:
                        continue
                    if geometry.intersects(candidate_geometry):
                        union.union(indices[local], indices[other_local])

    failures = []
    net_rows = []
    for net in sorted(pad_ids):
        # NC pads were never added, and one terminal can appear on several
        # layers; report unique physical component terminal labels only.
        roots = defaultdict(set)
        for index in pad_ids[net]:
            roots[union.find(index)].add(entries[index][2].split("[")[0])
        terminal_sets = [sorted(labels) for labels in roots.values()]
        net_rows.append((net, len(terminal_sets), terminal_sets))
        if len(terminal_sets) != 1:
            failures.append((net, terminal_sets))

    lines = [
        "PHYSICAL COPPER CONNECTIVITY AUDIT",
        "=" * 72,
        f"Electrical nets with pads checked: {len(net_rows)}",
        f"Disconnected nets: {len(failures)}",
        "",
    ]
    if failures:
        lines.append("DISCONNECTED NETS")
        for net, groups in failures:
            lines.append(f" - {net}: {len(groups)} islands")
            for group in groups:
                lines.append("    " + ", ".join(group))
        lines.append("")
        lines.append("RESULT: FAIL")
    else:
        lines.append("RESULT: PASS — every electrical net's component terminals share copper")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:5]))
    if failures:
        for net, groups in failures:
            print(f" - {net}: {len(groups)} islands")
        raise SystemExit(2)
    print("Physical connectivity: PASS")


if __name__ == "__main__":
    main()
