#!/usr/bin/env python3
"""Independent geometric audit for the generated Rev A KiCad PCB."""

from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path

# Load the runtime NumPy before adding the project dependency fallback path.
# The fallback directory is needed for Shapely/kiutils, but its bundled NumPy
# may not be ABI-compatible with the active Python runtime.
import numpy  # noqa: F401


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKSPACE_ROOT / ".tools" / "py"))

from design_data import BOARD_SIZE, NATIVE_CHILD_ANGLE_REFS, PROJECT_NAME  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.items.brditems import Segment, Via  # noqa: E402
from shapely.affinity import rotate as shape_rotate, translate  # noqa: E402
from shapely.geometry import LineString, Point, Polygon, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402


REPORT = PROJECT_ROOT / "reports" / "GEOMETRY_AUDIT.txt"
COPPER_LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")
MIN_CLEARANCE = 0.10
EDGE_CLEARANCE = 0.20


def reference_of(footprint):
    property_reference = getattr(footprint, "properties", {}).get("Reference")
    if property_reference:
        return property_reference
    return next(
        (item.text for item in footprint.graphicItems if getattr(item, "type", None) == "reference"),
        "",
    )


def rotate_point(x, y, angle):
    # KiCad's positive board rotation is clockwise in these numeric XY
    # coordinates, opposite to the Cartesian convention used by math/Shapely.
    theta = math.radians(-angle)
    return x * math.cos(theta) - y * math.sin(theta), x * math.sin(theta) + y * math.cos(theta)


def absolute_point(footprint, x, y):
    dx, dy = rotate_point(x, y, footprint.position.angle or 0)
    return footprint.position.X + dx, footprint.position.Y + dy


def pad_geometry(footprint, pad):
    if pad.shape == "custom":
        pieces = []
        # KiCad custom pads always have an anchor.  Most imported USB-C pads
        # use a practically zero-sized anchor, while the ESP32 exposed-pad
        # corner uses a real rectangular anchor that must be retained.
        anchor = getattr(getattr(pad, "customPadOptions", None), "anchor", None)
        if anchor == "rect" or (pad.size.X > 0.02 and pad.size.Y > 0.02):
            pieces.append(box(-pad.size.X / 2, -pad.size.Y / 2, pad.size.X / 2, pad.size.Y / 2))
        for primitive in getattr(pad, "customPadPrimitives", []) or []:
            coordinates = getattr(primitive, "coordinates", None)
            if coordinates:
                points = [(float(point.X), float(point.Y)) for point in coordinates]
                if len(points) >= 3:
                    polygon = Polygon(points)
                    if not polygon.is_valid:
                        polygon = polygon.buffer(0)
                    pieces.append(polygon)
        if not pieces:
            geometry = box(-pad.size.X / 2, -pad.size.Y / 2, pad.size.X / 2, pad.size.Y / 2)
        else:
            geometry = unary_union(pieces)
    elif pad.shape in ("circle", "oval"):
        if pad.shape == "circle" or abs(pad.size.X - pad.size.Y) < 1e-6:
            geometry = Point(0, 0).buffer(pad.size.X / 2, resolution=24)
        else:
            # Capsule along the pad's local x axis.
            radius = min(pad.size.X, pad.size.Y) / 2
            half_line = max(pad.size.X, pad.size.Y) / 2 - radius
            geometry = LineString([(-half_line, 0), (half_line, 0)]).buffer(radius, resolution=24)
            if pad.size.Y > pad.size.X:
                geometry = shape_rotate(geometry, 90, origin=(0, 0), use_radians=False)
    elif pad.shape in ("roundrect", "rounded_rectangle"):
        radius = min(pad.size.X, pad.size.Y) * (pad.roundrectRatio or 0.25)
        # A rounded rectangle is the inset rectangle expanded back by the
        # corner radius.  The previous implementation first built an already
        # full-size cross and then buffered it, making every pad too large by
        # one radius on all four sides and producing false short reports on
        # 0.50 mm-pitch packages.
        geometry = box(
            -pad.size.X / 2 + radius,
            -pad.size.Y / 2 + radius,
            pad.size.X / 2 - radius,
            pad.size.Y / 2 - radius,
        ).buffer(radius, resolution=16, join_style=1)
    else:
        geometry = box(-pad.size.X / 2, -pad.size.Y / 2, pad.size.X / 2, pad.size.Y / 2)
    if reference_of(footprint) in NATIVE_CHILD_ANGLE_REFS:
        total_angle = pad.position.angle or 0
    else:
        total_angle = (footprint.position.angle or 0) + (pad.position.angle or 0)
    geometry = shape_rotate(geometry, -total_angle, origin=(0, 0), use_radians=False)
    x, y = absolute_point(footprint, pad.position.X, pad.position.Y)
    return translate(geometry, xoff=x, yoff=y)


def build_copper(board):
    copper = defaultdict(list)
    source_counts = defaultdict(int)
    for footprint in board.footprints:
        reference = reference_of(footprint)
        for pad_index, pad in enumerate(footprint.pads):
            if pad.type == "np_thru_hole":
                continue
            net_name = (
                pad.net.name if pad.net is not None and pad.net.name
                else f"#NC:{reference}.{pad.number}:{pad_index}"
            )
            geometry = pad_geometry(footprint, pad)
            layers = COPPER_LAYERS if "*.Cu" in pad.layers else tuple(layer for layer in pad.layers if layer in COPPER_LAYERS)
            for layer in layers:
                copper[(layer, net_name)].append(geometry)
                source_counts[(layer, net_name)] += 1
        # The antenna coil is a deliberate net tie between ANT_P and ANT_N.
        # Audit it against every unrelated net as one copper object.
        if reference == "AE1":
            for item in footprint.graphicItems:
                layer = getattr(item, "layer", "")
                if layer not in ("F.Cu", "B.Cu") or not hasattr(item, "start"):
                    continue
                start = absolute_point(footprint, item.start.X, item.start.Y)
                end = absolute_point(footprint, item.end.X, item.end.Y)
                width = getattr(item, "width", None)
                if width is None and getattr(item, "stroke", None) is not None:
                    width = item.stroke.width
                width = width or 0.4
                copper[(layer, "#AE1_NET_TIE")].append(LineString([start, end]).buffer(width / 2, cap_style=1))
                source_counts[(layer, "#AE1_NET_TIE")] += 1

    for item in board.traceItems:
        if isinstance(item, Segment):
            geometry = LineString(
                [(item.start.X, item.start.Y), (item.end.X, item.end.Y)]
            ).buffer(item.width / 2, cap_style=1, join_style=1)
            net_name = board.nets[item.net].name
            copper[(item.layer, net_name)].append(geometry)
            source_counts[(item.layer, net_name)] += 1
        elif isinstance(item, Via):
            net_name = board.nets[item.net].name
            geometry = Point(item.position.X, item.position.Y).buffer(item.size / 2, resolution=20)
            for layer in COPPER_LAYERS:
                copper[(layer, net_name)].append(geometry)
                source_counts[(layer, net_name)] += 1
    return copper, source_counts


def main():
    board = Board.from_file(str(BOARD_PATH))
    copper, source_counts = build_copper(board)
    unions = {}
    for key, geometries in copper.items():
        unions[key] = unary_union(geometries)

    errors = []
    warnings = []
    board_inside = box(EDGE_CLEARANCE, EDGE_CLEARANCE, BOARD_SIZE[0] - EDGE_CLEARANCE, BOARD_SIZE[1] - EDGE_CLEARANCE)
    for (layer, net_name), geometry in unions.items():
        # USB-C edge pads and the antenna terminal are allowed to approach the
        # outline; actual copper must still remain inside the Edge.Cuts.
        if not box(0, 0, BOARD_SIZE[0], BOARD_SIZE[1]).covers(geometry):
            errors.append(f"COPPER OUTSIDE BOARD: {layer} {net_name} bounds={geometry.bounds}")
        elif not board_inside.covers(geometry) and net_name not in {"USB_5V", "USB_DN_CONN", "USB_DP_CONN", "GND"}:
            warnings.append(f"EDGE CLEARANCE < {EDGE_CLEARANCE:.2f} mm: {layer} {net_name}")

    for layer in COPPER_LAYERS:
        nets = [(name, unions[(layer, name)]) for (candidate_layer, name) in unions if candidate_layer == layer]
        for index, (first_name, first_geometry) in enumerate(nets):
            for second_name, second_geometry in nets[index + 1:]:
                if "#AE1_NET_TIE" in (first_name, second_name):
                    other = second_name if first_name == "#AE1_NET_TIE" else first_name
                    if other in {"ANT_P", "ANT_N"}:
                        continue
                distance = first_geometry.distance(second_geometry)
                if distance < 1e-5:
                    errors.append(f"COPPER SHORT/TOUCH: {layer} {first_name} <> {second_name}")
                elif distance + 1e-4 < MIN_CLEARANCE:
                    errors.append(
                        f"COPPER CLEARANCE {distance:.3f} mm < {MIN_CLEARANCE:.2f}: {layer} {first_name} <> {second_name}"
                    )

    # Keep-out audits use all copper, including the deterministic In1 mesh.
    nfc_keepout = box(4.0, 16.0, 47.1, 59.0)
    # Official ESP32-C6-MINI-1 footprint antenna keep-out transformed by the
    # fixed U4 placement (99.0, 8.5): x=92.4..105.6, y=0.2..5.6.  A small
    # A 0.1 mm process margin is included without swallowing the module's own
    # GND-via fanout, whose copper begins at y=5.75 mm.
    esp_keepout = box(92.1, 0.0, 105.9, 5.7)
    for (layer, net_name), geometry in unions.items():
        if geometry.intersects(nfc_keepout):
            # AE1 pad 2 is through-hole and its intentional B.Cu return crosses
            # the coil keep-out.  ANT_P/ANT_N are therefore permitted on every
            # layer only inside the antenna footprint; unrelated copper is not.
            if net_name == "#AE1_NET_TIE" or net_name in {"ANT_P", "ANT_N"}:
                pass
            else:
                errors.append(f"NFC KEEP-OUT VIOLATION: {layer} {net_name}")
        if geometry.intersects(esp_keepout):
            # The module's own pads are below the antenna region; no electrical
            # net is expected inside this rectangle.
            errors.append(f"ESP ANTENNA KEEP-OUT VIOLATION: {layer} {net_name}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{PROJECT_NAME} GEOMETRY AUDIT",
        "=" * 72,
        f"Copper layer/net unions: {len(unions)}",
        f"Copper primitives checked: {sum(source_counts.values())}",
        f"Minimum audited different-net clearance: {MIN_CLEARANCE:.2f} mm",
        f"Errors: {len(set(errors))}",
        f"Warnings: {len(set(warnings))}",
        "",
    ]
    if errors:
        lines.append("ERRORS")
        lines.extend(f" - {error}" for error in sorted(set(errors)))
        lines.append("")
    if warnings:
        lines.append("WARNINGS")
        lines.extend(f" - {warning}" for warning in sorted(set(warnings)))
        lines.append("")
    if not errors:
        lines.append("RESULT: PASS — no different-net copper touch or sub-0.10 mm clearance found")
    else:
        lines.append("RESULT: FAIL")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:8]))
    if errors:
        for error in sorted(set(errors))[:40]:
            print(" -", error)
        raise SystemExit(2)
    print("Geometry audit: PASS")


if __name__ == "__main__":
    main()
