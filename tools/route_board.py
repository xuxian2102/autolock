#!/usr/bin/env python3
"""Route HomeKey Lock Rev A and create a deterministic connectivity audit.

The router is intentionally conservative and specialized for this board:

* RF, crystal, USB and power-converter nets stay on F.Cu.
* Low-current control nets use B.Cu/In2.Cu with top-side dogbones.
* In1.Cu ground is routed as a dense mesh of copper tracks with explicit
  clearance around every non-ground through connection, because the router has
  no zone filler of its own.  pour_ground_planes.py, which this script calls
  once the board is otherwise final, replaces that mesh with real ground pours
  on In1.Cu and B.Cu; kicad-cli fills them deterministically on the way to DRC
  and to the Gerbers.
* Both the NFC loop and the ESP32 module antenna have all-layer keep-outs.

This is not a general-purpose autorouter.  It is a reproducible engineering
tool for the fixed Rev A placement.
"""

from __future__ import annotations

import csv
import heapq
import math
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
KIUTILS = WORKSPACE_ROOT / ".tools" / "py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(KIUTILS))

from design_data import ANTENNA, BOARD_SIZE, NATIVE_CHILD_ANGLE_REFS, PROJECT_NAME, parts  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from u4_native import install_u4_native_block  # noqa: E402
import pour_ground_planes  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.items.brditems import Segment, Via  # noqa: E402
from kiutils.items.common import Position  # noqa: E402
from shapely.geometry import LineString, Point, box  # noqa: E402
from shapely.ops import nearest_points, unary_union  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402


REPORT_DIR = PROJECT_ROOT / "reports"
ROUTE_REPORT = REPORT_DIR / "ROUTING_AUDIT.txt"
CONNECTIVITY_CSV = REPORT_DIR / "PCB_CONNECTIVITY.csv"

GRID = 0.125
LAYERS = ("F.Cu", "B.Cu", "In2.Cu")
# Every copper layer, including the In1.Cu plane the router does not route on.
COPPER_LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")
ROUTER_LAYERS = {name: index for index, name in enumerate(LAYERS)}
DEFAULT_CLEARANCE = 0.18
# Radius reserved around every routing endpoint so a net routed later still has
# a way out of its own fanout.  The failure this prevents had a foreign trace
# 0.311 mm from an escape.
ESCAPE_KEEPALIVE = 0.5

# Where pour_ground_planes.py is not allowed to fill, so a ground via standing
# inside one has no In1.Cu plane to reach.  Kept in step with the same
# rectangles in pour_ground_planes.py.
POUR_KEEPOUTS = (
    box(*ANTENNA.keepout),               # NFC loop
    box(91.87, 0.00, 106.13, 5.91),      # ESP32 module edge antenna
)
HOLE_CLEARANCE = 0.25
VIA_SIZE = 0.50
VIA_DRILL = 0.20
SIGNAL_HALF = 0.10
MAX_ASTAR_VISITS = 30_000


RF_NETS = {
    "ANT_P", "ANT_N", "PN_TX1", "PN_TX2", "PN_RXP", "PN_RXN",
    "RXP_AC", "RXN_AC", "RF_P0", "RF_N0", "RF_P1", "RF_N1",
    "RF_P2", "RF_N2", "EXT_ANT_P", "EXT_ANT_N", "PN_VMID",
}
CRYSTAL_NETS = {"XTAL1", "XTAL2"}
USB_NETS = {
    "USB_5V", "USB_CC1", "USB_CC2", "USB_DN_CONN", "USB_DP_CONN",
    "USB_DM", "USB_DP",
}
POWER_NETS = {
    "BAT_RAW", "BAT_FUSED", "BAT_SYS", "PMOS_GATE",
    "BST_5V", "SW_5V", "5V_BAT", "SYS_5V",
    "BST_3V3", "SW_3V3", "3V3", "PN_TVDD", "PN_VDD",
    "BST_SERVO", "SW_SERVO", "SERVO_6V", "SERVO_6V_OUT",
    "SERVO_FB", "SERVO_EN_IC",
}

# These electrically local loops are shorter and more deterministic as direct
# top-layer connections than as general maze routes.
DIRECT_NETS = set()

# Only high-current and RF nets are forced to remain on the component side.
# Low-current rails and USB/control signals use the two signal-routing layers;
# this avoids artificial top-layer congestion without putting servo current
# through a single via.
FRONT_NETS = RF_NETS | {
    "BAT_RAW", "BAT_FUSED",
    "SW_SERVO", "SERVO_6V", "SERVO_6V_OUT",
    "PN_SCK_IC", "SERVO_EN", "UART_RX",
    "XTAL1", "XTAL2", "BST_3V3", "BST_5V", "BST_SERVO",
    "SW_3V3", "SW_5V", "PMOS_GATE", "SERVO_EN_IC",
    "PN_TVDD", "PN_VDD",
    "PN_NSS_IC", "PN_MOSI_IC", "PN_MISO_IC",
    "PN_DWL_REQ", "PN_IRQ", "PN_VEN", "3V3", "SYS_5V",
    "PN_NSS", "PN_MOSI", "PN_MISO", "PN_SCK",
    "BAT_ADC", "ESP_EN", "SERVICE_BTN", "BOOT", "STATUS_LED",
    "SERVO_PWM", "UART_TX", "USB_DM", "USB_DP",
    # Keep connector VBUS/CC fan-out on top.  Their real custom-pad outlines
    # leave safe vertical lanes, but not enough room for a 0.5 mm via between
    # the interleaved USB-C contacts.
    "USB_5V", "USB_CC1", "USB_CC2",
    "USB_DN_CONN", "USB_DP_CONN",
}

# These loops must not change layers.  Other front-fanned nets may use a via
# after leaving the IC/connector and continue on B.Cu/In2.Cu.
STRICT_FRONT_NETS = RF_NETS | CRYSTAL_NETS | {
    "BAT_RAW", "BAT_FUSED", "SW_SERVO", "SERVO_6V", "SERVO_6V_OUT",
    "BST_3V3", "BST_5V", "BST_SERVO", "SW_3V3", "SW_5V",
}


WIDTH_BY_NET = defaultdict(lambda: 0.20)
for net in RF_NETS:
    WIDTH_BY_NET[net] = 0.30
for net in CRYSTAL_NETS | USB_NETS:
    WIDTH_BY_NET[net] = 0.20
for net in {"3V3", "SYS_5V", "PN_TVDD", "PN_VDD"}:
    WIDTH_BY_NET[net] = 0.50
WIDTH_BY_NET["5V_BAT"] = 0.65
for net in {"BAT_RAW", "BAT_FUSED", "BAT_SYS"}:
    WIDTH_BY_NET[net] = 1.25
for net in {"SW_SERVO", "SERVO_6V", "SERVO_6V_OUT"}:
    WIDTH_BY_NET[net] = 1.50
for net in {"SW_5V", "SW_3V3"}:
    WIDTH_BY_NET[net] = 0.80
WIDTH_BY_NET["SW_3V3"] = 0.60


# Deterministic parallel escape channel for the ESP32-C6 east-side pads.  The
# endpoints sit just outside the no-via region; long routes may change layer
# from there without crowding the 0.8 mm-pitch castellations.
FIXED_ESCAPES = {
    ("U5", "1"): (80.75, 30.50),
    ("U5", "2"): (80.25, 30.50),
    ("U5", "3"): (79.75, 30.50),
    ("U5", "4"): (79.25, 29.25),
    ("U5", "5"): (78.75, 30.50),
    # Carry 3V3 through the parallel top fanout, then drop its via above the
    # SPI endpoint row so it cannot trap U5.5/U5.7.
    ("U5", "6"): (78.25, 29.00),
    ("U5", "7"): (77.75, 30.50),
    ("U5", "8"): (77.25, 30.50),
    ("U5", "9"): (76.75, 29.25),
    ("U5", "10"): (76.25, 30.50),
    ("U5", "12"): (74.25, 35.75),
    ("U5", "13"): (74.25, 36.25),
    ("U5", "14"): (74.25, 36.75),
    # VMID is the centre pin of the five-pad RF edge.  Escape it straight
    # left before the neighbouring RX/TX pins occupy that only legal channel.
    ("U5", "15"): (74.25, 37.25),
    ("U5", "16"): (74.25, 37.75),
    ("U5", "17"): (72.00, 38.25),
    ("U5", "18"): (74.25, 38.75),
    ("U5", "19"): (74.25, 39.25),
    ("U5", "21"): (74.75, 40.55),
    ("U5", "22"): (76.75, 42.00),
    ("U5", "26"): (78.75, 42.00),
    ("U5", "27"): (79.25, 42.00),
    ("U5", "28"): (79.75, 42.00),
    ("U5", "29"): (80.25, 44.00),
    ("U5", "30"): (80.75, 44.00),
    ("U5", "31"): (82.75, 39.75),
    ("U4", "5"): (90.50, 10.50),
    ("U4", "6"): (90.50, 11.25),
    ("U4", "19"): (99.75, 19.50),
    ("U4", "23"): (103.00, 19.00),
    ("U4", "25"): (109.0, 15.25),
    # Leave UART_TX's narrow east-side escape corridor clear before MOSI
    # changes layer.
    ("U4", "26"): (111.0, 14.50),
    ("U4", "27"): (109.0, 13.75),
    ("U4", "28"): (109.0, 13.00),
    ("U4", "29"): (109.0, 12.25),
    ("U4", "30"): (109.0, 11.50),
    ("U4", "31"): (109.0, 10.75),
    # USB-C USB2 contacts are interleaved.  Fan all four D+/D- pads straight
    # inward as a 0.50 mm-pitch bus; the router may cross the paired nets only
    # after changing layer beyond the connector courtyard.
    ("J2", "B7"): (134.75, 9.25),
    ("J2", "A6"): (134.25, 9.25),
    ("J2", "A7"): (133.75, 9.25),
    ("J2", "B6"): (133.25, 9.25),
    # Let switching nodes leave the small regulator packages on a narrow
    # dogbone before the nominal high-current width begins.
    ("U1", "5"): (100.00, 50.50),
    ("U6", "2"): (131.25, 50.50),
    ("U6", "6"): (124.75, 51.45),
}

FIXED_ESCAPE_PATHS = {
    ("U4", "24"): [(104.0, 17.5), (109.0, 17.5)],
    ("U5", "14"): [(74.25, 36.75), (72.50, 36.25)],
    ("U5", "18"): [(74.25, 38.75)],
    ("U5", "3"): [(79.75, 23.00)],
    ("U5", "5"): [(78.75, 25.00)],
    ("U5", "7"): [(77.75, 24.00)],
    ("U5", "28"): [(79.75, 43.00)],
    ("U5", "29"): [(80.25, 44.00)],
    ("U5", "30"): [(80.75, 44.00)],
    ("J2", "A5"): [(135.25, 9.25)],
    ("J2", "B5"): [(132.25, 9.25)],
}

# These few 0.50 mm-pitch lanes have 0.15 mm exact copper clearance but look
# blocked to the intentionally conservative raster router.  They are emitted
# deterministically and remain subject to the independent Shapely audit.
REVIEWED_DENSE_PATHS = {
    ("U5", "3"), ("U5", "4"), ("U5", "5"), ("U5", "7"),
    ("U5", "8"), ("U5", "9"), ("U5", "18"),
    ("U5", "28"), ("U5", "29"), ("U5", "30"),
}

# Selected supply pins need an immediate, individually spaced via.  Leaving
# them as top-layer endpoints traps them behind the PN7161 fanout bus even
# though the lower routing layers are open.
FORCE_VIA_PADS = {
    # U3 input pins are locally folded together.  Drop the folded endpoint to
    # a lower routing layer immediately; the nearby 3V3 rail blocks the
    # original top-only endpoint from reaching the rest of SYS_5V.
    ("U3", "2"),
    ("U5", "6"),
    ("U5", "12"),
    ("U5", "14"),
    ("U5", "18"),
    ("U5", "22"),
    ("U5", "3"),
    ("U5", "5"),
    ("U5", "7"),
    ("U5", "8"),
    ("U5", "28"),
    ("C18", "1"),
    ("TP3", "1"),
    ("U4", "8"),
    ("U4", "12"),
    # Drop PN_VEN below the top layer beside U4.  Keeping its long run on
    # F.Cu lets the raster path graze the rotated C11 ground pad.
    ("U4", "13"),
    ("U4", "17"),
    ("U4", "18"),
    # The external PN_MOSI run leaves the ESP32 fan-out at x=109 mm, then
    # changes layer instead of fighting through the crowded top-side test and
    # decoupling area.
    ("U4", "26"),
    ("U4", "28"),
    ("R15", "2"),
    ("R16", "2"),
    ("R16", "1"),
    ("R17", "2"),
    ("R30", "1"),
    ("R5", "2"),
}


# SERVO_PWM is routed early by the normal occupancy-aware router.  Its former
# fixed bridge crossed three already-routed supply/ground features.
REVIEWED_BRIDGES = {
    # XTAL1's automatically routed branch forms a top-layer wall between X1
    # and C16.  Keep XTAL2 on F.Cu as required, but take the short reviewed
    # path around the left side of the crystal/load-capacitor cluster.
    "XTAL2": (
        "F.Cu", 0.20,
        [
            (80.250, 44.000),
            (80.250, 45.750),
            (80.750, 46.625),
            (80.750, 46.250),
            (74.750, 46.250),
            (74.750, 54.125),
            (82.000, 54.125),
            (82.000, 53.625),
        ],
    ),
}

@dataclass(frozen=True)
class PadPoint:
    reference: str
    number: str
    net: str
    x: float
    y: float
    fp_x: float
    fp_y: float
    size_x: float
    size_y: float
    angle: float
    through: bool


@dataclass(frozen=True)
class Endpoint:
    x: float
    y: float
    layer: str
    source: str


def reference_of(footprint) -> str:
    property_reference = getattr(footprint, "properties", {}).get("Reference")
    if property_reference:
        return property_reference
    return next(
        (item.text for item in footprint.graphicItems if getattr(item, "type", None) == "reference"),
        "",
    )


def rotate(x: float, y: float, angle: float):
    # KiCad's board Y axis points down: positive footprint rotation therefore
    # has the opposite sign to the Cartesian rotation used here.
    theta = math.radians(-angle)
    return x * math.cos(theta) - y * math.sin(theta), x * math.sin(theta) + y * math.cos(theta)


def absolute_pad(footprint, pad) -> tuple[float, float]:
    dx, dy = rotate(pad.position.X, pad.position.Y, footprint.position.angle or 0)
    return footprint.position.X + dx, footprint.position.Y + dy


def all_pad_points(board: Board):
    points = []
    for footprint in board.footprints:
        reference = reference_of(footprint)
        if not reference or reference.startswith("H"):
            continue
        for pad_index, pad in enumerate(footprint.pads):
            if pad.type == "np_thru_hole":
                continue
            if "*.Cu" not in pad.layers and not any(layer in LAYERS for layer in pad.layers):
                # Stencil-only apertures (for example the PN7161 exposed-pad
                # paste window) are not copper routing obstacles/endpoints.
                continue
            net_name = (
                pad.net.name if pad.net is not None and pad.net.name
                else f"#NC:{reference}.{pad.number}:{pad_index}"
            )
            x, y = absolute_pad(footprint, pad)
            through = "*.Cu" in pad.layers or pad.type in ("thru_hole", "np_thru_hole")
            size_x, size_y = pad.size.X, pad.size.Y
            if pad.shape == "custom":
                # Imported USB-C VBUS/GND pads encode their real 0.6 x 1.3 mm
                # outline as a polygon around a nominal 0.005 mm anchor.  The
                # router must block the polygon, not the nominal anchor, or a
                # neighbouring escape/via can be placed through the pad.
                xs, ys = [], []
                for primitive in getattr(pad, "customPadPrimitives", []) or []:
                    for point in getattr(primitive, "coordinates", []) or []:
                        xs.append(float(point.X))
                        ys.append(float(point.Y))
                if xs and ys:
                    size_x = max(size_x, max(xs) - min(xs))
                    size_y = max(size_y, max(ys) - min(ys))
            points.append(
                PadPoint(
                    reference=reference,
                    number=str(pad.number),
                    net=net_name,
                    x=x,
                    y=y,
                    fp_x=footprint.position.X,
                    fp_y=footprint.position.Y,
                    size_x=size_x,
                    size_y=size_y,
                    # generate_board.py bakes the footprint's rotation into
                    # the children of everything in NATIVE_CHILD_ANGLE_REFS, so
                    # for those the pad angle is already the absolute one and
                    # adding the footprint angle counts it twice.  Getting this
                    # wrong transposes the pad rectangle: 92 of 364 pads were
                    # blocked at the wrong aspect, C1 by 1.55 mm, and the
                    # router was free to lay a 0.5 mm SYS_5V trace 0.10 mm from
                    # a foreign pad.  audit_board.pad_geometry has always had
                    # the right rule; this is the same one.
                    angle=-(
                        (pad.position.angle or 0)
                        if reference in NATIVE_CHILD_ANGLE_REFS
                        else (footprint.position.angle or 0) + (pad.position.angle or 0)
                    ),
                    through=through,
                )
            )
    return points


def all_npth_holes(board: Board):
    """Return absolute centres and drill sizes for mechanical NPTH holes.

    NPTH pads are deliberately excluded from ``all_pad_points`` because they
    are not electrical endpoints.  They still have to be present in every
    routing/plane obstacle set, however; otherwise a trace can legally route
    through a connector's plastic locating peg.
    """
    holes = []
    for footprint in board.footprints:
        for pad in footprint.pads:
            if pad.type != "np_thru_hole":
                continue
            x, y = absolute_pad(footprint, pad)
            drill_x = float(pad.drill.diameter)
            drill_y = float(pad.drill.width or pad.drill.diameter)
            holes.append((x, y, drill_x, drill_y))
    return holes


def cell(x: float, y: float):
    return int(round(x / GRID)), int(round(y / GRID))


def xy(grid_cell):
    return grid_cell[0] * GRID, grid_cell[1] * GRID


def disk_cells(center, radius):
    count = int(math.ceil(radius / GRID))
    cx, cy = center
    for dx in range(-count, count + 1):
        for dy in range(-count, count + 1):
            if (dx * GRID) ** 2 + (dy * GRID) ** 2 <= radius ** 2 + 1e-9:
                yield cx + dx, cy + dy


class Occupancy:
    def __init__(self):
        self.cells = {layer: defaultdict(set) for layer in LAYERS}
        self.width, self.height = BOARD_SIZE

    def add_disk(self, layer, x, y, radius, token):
        for item in disk_cells(cell(x, y), radius):
            self.cells[layer][item].add(token)

    def add_line(self, layer, start, end, radius, token):
        x0, y0 = start
        x1, y1 = end
        distance = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(math.ceil(distance / (GRID / 2))))
        for index in range(steps + 1):
            fraction = index / steps
            self.add_disk(
                layer,
                x0 + (x1 - x0) * fraction,
                y0 + (y1 - y0) * fraction,
                radius,
                token,
            )

    def allowed(self, layer, grid_cell, net, extra_radius=0.0):
        x, y = xy(grid_cell)
        if x < 0.45 or x > self.width - 0.45 or y < 0.45 or y > self.height - 0.45:
            return False
        for candidate in disk_cells(grid_cell, extra_radius):
            tokens = self.cells[layer].get(candidate, set())
            if tokens and not tokens <= {net}:
                return False
        return True


def segment(board, net_by_name, net_name, start, end, width, layer):
    if math.dist(start, end) < 1e-6:
        return None
    item = Segment(
        start=Position(*start), end=Position(*end), width=width,
        layer=layer, net=net_by_name[net_name].number, tstamp=str(uuid4()),
    )
    board.traceItems.append(item)
    return item


def add_via(board, net_by_name, net_name, point, *, size=VIA_SIZE, drill=VIA_DRILL):
    item = Via(
        position=Position(*point), size=size, drill=drill,
        layers=["F.Cu", "B.Cu"], net=net_by_name[net_name].number,
        tstamp=str(uuid4()),
    )
    board.traceItems.append(item)
    return item


def consolidate_close_same_net_vias(board):
    """Replace overlapping same-net drill pairs with one manufacturable via.

    Endpoint fan-out and the later MST route can independently request a via
    at nearly the same transition.  Leaving both holes is electrically
    redundant and can produce merged/slot-like drills.  Keep one via and join
    every removed centre to it on both outer layers; those short bridges stay
    inside the union of the original annular pads and preserve connectivity
    for tracks that terminated at either centre.
    """
    vias = [item for item in board.traceItems if isinstance(item, Via)]
    parent = list(range(len(vias)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first, second):
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    for first_index, first in enumerate(vias):
        for second_index in range(first_index + 1, len(vias)):
            second = vias[second_index]
            if first.net != second.net:
                continue
            minimum_centres = first.drill / 2 + second.drill / 2 + HOLE_CLEARANCE
            if math.dist(
                (first.position.X, first.position.Y),
                (second.position.X, second.position.Y),
            ) < minimum_centres - 1e-6:
                union(first_index, second_index)

    groups = defaultdict(list)
    for index, via in enumerate(vias):
        groups[find(index)].append(via)

    removed = set()
    merged_groups = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        merged_groups += 1
        # Prefer the largest annular pad as the surviving transition.
        keep = max(group, key=lambda item: (item.size, item.drill))
        net_name = board.nets[keep.net].name
        for other in group:
            if other is keep:
                continue
            start = (other.position.X, other.position.Y)
            end = (keep.position.X, keep.position.Y)
            if math.dist(start, end) > 1e-6:
                bridge_width = min(
                    keep.size, other.size,
                    max(0.20, WIDTH_BY_NET.get(net_name, 0.20)),
                )
                segment(board, {net_name: board.nets[keep.net]}, net_name, start, end, bridge_width, "F.Cu")
                segment(board, {net_name: board.nets[keep.net]}, net_name, start, end, bridge_width, "B.Cu")
            removed.add(id(other))

    if removed:
        board.traceItems = [
            item for item in board.traceItems
            if not (isinstance(item, Via) and id(item) in removed)
        ]
    return merged_groups, len(removed)


def remove_redundant_vias(board):
    """Drop vias that provide no layer transition.

    While solving the surrounding nets the router reserves transitions it may
    not end up needing: if the final path reaches every endpoint on one layer,
    the via it reserved is electrically inert and KiCad reports it as dangling.

    This used to be a list of three (net, x, y) triples read off one run, which
    silently stopped matching the moment anything moved.  State the condition
    instead: a via earns its place only if copper of its own net actually
    touches it on more than one layer.

    "Touches" has to mean touches, not shares an endpoint.  A ground via sits
    in the middle of an In1.Cu mesh stripe, not at its end, so an endpoint-only
    test throws away 30 perfectly good plane connections.

    In1.Cu carries no routed ground copper for a via to touch -- the pour
    supplies that whole plane a few lines later -- so count the plane as
    present for a ground via, unless the via stands inside a keep-out where the
    pour is not allowed to fill.
    """
    copper = defaultdict(list)          # (net, layer) -> shapes
    for item in board.traceItems:
        if isinstance(item, Segment):
            net = board.nets[item.net].name
            shape = LineString(
                [(item.start.X, item.start.Y), (item.end.X, item.end.Y)]
            ).buffer(item.width / 2, cap_style=1, join_style=1)
            copper[(net, item.layer)].append(shape)
    for footprint in board.footprints:
        for pad in footprint.pads:
            if pad.net is None or not pad.net.name or pad.type == "np_thru_hole":
                continue
            x, y = absolute_pad(footprint, pad)
            shape = Point(x, y).buffer(max(pad.size.X, pad.size.Y) / 2, resolution=8)
            layers = (
                COPPER_LAYERS if "*.Cu" in pad.layers
                else tuple(l for l in pad.layers if l in COPPER_LAYERS)
            )
            for layer in layers:
                copper[(pad.net.name, layer)].append(shape)

    trees = {key: STRtree(shapes) for key, shapes in copper.items()}

    removed = 0
    kept = []
    for item in board.traceItems:
        if isinstance(item, Via):
            net = board.nets[item.net].name
            here = Point(item.position.X, item.position.Y).buffer(
                item.size / 2, resolution=8
            )
            layers = {
                layer for layer in COPPER_LAYERS
                if (net, layer) in trees
                and any(
                    copper[(net, layer)][index].intersects(here)
                    for index in trees[(net, layer)].query(here)
                )
            }
            # Test the via's centre, not its pad.  U4's thirteen ground fanout
            # vias sit at y = 6.0-6.125, just below the ESP32 antenna keep-out
            # that ends at y = 5.91; their 0.4 mm pads reach into it, but their
            # centres are outside and the pour fills right up to them.  Asking
            # whether the pad touches the keep-out deleted all thirteen.
            centre = Point(item.position.X, item.position.Y)
            if net == "GND" and not any(
                area.covers(centre) for area in POUR_KEEPOUTS
            ):
                layers.add("In1.Cu")
            if len(layers) < 2:
                removed += 1
                continue
        kept.append(item)
    board.traceItems = kept
    return removed


def add_reviewed_bridge(board, net_by_name, occupancy, net_name):
    """Emit one fixed, independently clearance-audited lower-layer bridge."""
    layer, width, points = REVIEWED_BRIDGES[net_name]
    for start, end in zip(points, points[1:]):
        segment(board, net_by_name, net_name, start, end, width, layer)
        occupancy.add_line(
            layer, start, end,
            width / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
            net_name,
        )
    return len(points) - 1


def add_explicit_path(
    board, net_by_name, occupancy, net_name, layer, width, points,
):
    """Emit a reviewed polyline and reserve its real routing corridor."""
    for start, end in zip(points, points[1:]):
        segment(board, net_by_name, net_name, start, end, width, layer)
        occupancy.add_line(
            layer, start, end,
            width / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
            net_name,
        )


def add_bat_sys_tree(board, net_by_name, occupancy, pads_by_net):
    """Route BAT_SYS with a doubled-via 1.25 mm current trunk.

    The full servo-input current runs on In2.Cu between four paired-via
    stations.  F.Cu carries only short component fan-outs.  The divider and
    test point share the net electrically but use a deliberately narrow
    Kelvin-style branch, so they cannot become an accidental current path.
    """
    def pad_xy(reference, number):
        matches = [
            pad for pad in pads_by_net["BAT_SYS"]
            if pad.reference == reference and pad.number == number
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected one BAT_SYS pad for {reference}.{number}, found {len(matches)}"
            )
        return matches[0].x, matches[0].y

    power_vias = [
        (106.500, 72.500), (107.500, 72.500),  # Q1 output station
        (113.000, 64.500), (114.000, 64.500),  # branch/bulk station
        (100.000, 56.500), (101.000, 56.500),  # 5 V buck input
        (127.750, 49.750), (128.500, 49.750),  # servo buck input
    ]
    auxiliary_vias = [
        (110.000, 62.000),  # D2 clamp branch
        (92.500, 52.000),   # C3 input capacitor
        (123.000, 48.500),  # C39
        (123.000, 43.000),  # C40
        (90.000, 23.000),   # battery divider
        (82.000, 22.000),   # BAT_SYS test point
    ]
    for point in power_vias:
        add_via(board, net_by_name, "BAT_SYS", point, size=0.80, drill=0.40)
        for layer in LAYERS:
            occupancy.add_disk(
                layer, *point,
                0.80 / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
                "BAT_SYS",
            )
    for point in auxiliary_vias:
        add_via(board, net_by_name, "BAT_SYS", point, size=0.60, drill=0.30)
        for layer in LAYERS:
            occupancy.add_disk(
                layer, *point,
                0.60 / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
                "BAT_SYS",
            )

    # Main current tree.  The centreline at every station overlaps both vias,
    # so current sharing is copper-defined rather than depending on assembly.
    trunk_paths = [
        [(107.000, 72.500), (113.500, 66.000), (113.500, 64.500)],
        [(113.500, 64.500), (105.500, 56.500), (100.500, 56.500)],
        [
            (113.500, 64.500), (118.500, 59.500), (119.500, 59.500),
            (120.000, 59.000), (120.250, 59.000), (126.750, 52.500),
            (126.750, 52.250), (128.000, 51.000), (128.000, 49.750),
        ],
    ]
    for path in trunk_paths:
        add_explicit_path(
            board, net_by_name, occupancy, "BAT_SYS", "In2.Cu", 1.25, path,
        )

    # Short top-layer component fan-outs.
    c1_bat = pad_xy("C1", "1")
    top_paths = [
        (1.25, [pad_xy("Q1", "1"), pad_xy("Q1", "3")]),
        (1.25, [pad_xy("Q1", "2"), (107.000, 72.500)]),
        (0.40, [pad_xy("U1", "2"), (100.000, 56.500)]),
        (0.40, [pad_xy("U1", "3"), (101.000, 56.500)]),
        (0.50, [pad_xy("U6", "3"), (128.500, 49.750)]),
        # C1 is rotated 90 degrees, with its GND pad directly above BAT_SYS.
        # Leave the BAT_SYS pad sideways before rising to the trunk station so
        # the 0.60 mm branch cannot pass through C1.2.
        (0.60, [
            c1_bat, (111.500, c1_bat[1]),
            (111.500, 64.500), (114.000, 64.500),
        ]),
        (0.60, [pad_xy("C2", "1"), (114.000, 64.500)]),
        (0.50, [pad_xy("D2", "1"), (110.000, 62.000)]),
        (0.50, [pad_xy("C3", "1"), (92.500, 52.000)]),
        (0.50, [pad_xy("C39", "1"), (123.000, 48.500)]),
        (0.50, [pad_xy("C40", "1"), (123.000, 43.000)]),
        (0.25, [pad_xy("R10", "1"), (90.000, 23.000)]),
        (0.25, [pad_xy("TP1", "1"), (82.000, 22.000)]),
    ]
    for width, path in top_paths:
        add_explicit_path(
            board, net_by_name, occupancy, "BAT_SYS", "F.Cu", width, path,
        )

    # Local capacitor/clamp branches into the current tree.
    branch_paths = [
        (0.50, [(110.000, 62.000), (113.500, 64.500)]),
        (0.50, [
            (92.500, 52.000), (97.000, 56.500), (100.500, 56.500),
        ]),
        (0.50, [
            (123.000, 48.500), (125.000, 50.500),
            (125.000, 50.750), (126.750, 52.500),
        ]),
        (0.50, [
            (123.000, 43.000), (123.000, 44.750), (122.500, 45.250),
            (122.500, 45.750), (123.000, 46.250), (123.000, 48.500),
        ]),
        (0.25, [
            (82.000, 22.000), (83.000, 23.000), (85.250, 23.000),
            (85.750, 23.500), (86.250, 23.500), (86.750, 23.000),
            (88.250, 23.000), (88.750, 23.500), (89.250, 23.500),
            (89.750, 23.000), (90.000, 23.000),
        ]),
        (0.25, [
            (90.000, 23.000), (89.000, 24.000), (88.750, 24.000),
            (87.750, 25.000), (87.000, 24.250), (86.500, 24.250),
            (85.500, 25.250), (84.500, 25.250), (83.750, 24.500),
            (81.500, 24.500), (80.750, 23.750), (80.000, 23.750),
            (79.500, 24.250), (78.500, 24.250), (78.000, 24.750),
            (78.000, 25.250), (82.750, 30.000), (82.750, 30.500),
            (85.500, 33.250), (85.250, 33.500), (85.250, 33.750),
            (100.500, 49.000), (100.500, 56.500),
        ]),
    ]
    for width, path in branch_paths:
        add_explicit_path(
            board, net_by_name, occupancy, "BAT_SYS", "In2.Cu", width, path,
        )

    return len(trunk_paths), power_vias + auxiliary_vias


def line_is_free(occupancy, layer, start, end, radius, net):
    x0, y0 = start
    x1, y1 = end
    steps = max(1, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / (GRID / 2))))
    for index in range(steps + 1):
        fraction = index / steps
        px = x0 + (x1 - x0) * fraction
        py = y0 + (y1 - y0) * fraction
        for candidate in disk_cells(cell(px, py), radius):
            if not occupancy.allowed(layer, candidate, net):
                return False
    return True


def candidate_escape(pad: PadPoint, occupancy: Occupancy, net: str, via: bool):
    fixed = FIXED_ESCAPES.get((pad.reference, pad.number))
    if fixed is not None:
        if (pad.reference, pad.number) in REVIEWED_DENSE_PATHS:
            return fixed
        if not line_is_free(
            occupancy, "F.Cu", (pad.x, pad.y), fixed,
            max(0.0, min(WIDTH_BY_NET[net], 0.30) / 2 - SIGNAL_HALF),
            net,
        ):
            raise RuntimeError(
                f"Fixed escape obstructed for {pad.reference}.{pad.number} net={net}"
            )
        if via:
            extra = VIA_SIZE / 2 - SIGNAL_HALF + 0.03
            if any(
                not occupancy.allowed(layer, cell(*fixed), net, extra)
                for layer in LAYERS
            ):
                raise RuntimeError(
                    f"Fixed via escape obstructed for {pad.reference}.{pad.number} net={net}"
                )
        return fixed
    dx, dy = pad.x - pad.fp_x, pad.y - pad.fp_y
    length = math.hypot(dx, dy)
    if length < 0.1:
        base_angle = math.pi / 2
    else:
        base_angle = math.atan2(dy, dx)
    angles = [
        0, math.pi / 8, -math.pi / 8, math.pi / 4, -math.pi / 4,
        3 * math.pi / 8, -3 * math.pi / 8, math.pi / 2, -math.pi / 2,
        3 * math.pi / 4, -3 * math.pi / 4, math.pi,
    ]
    distances = [0.8, 1.05, 1.30, 1.60, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
    # Occupancy already includes the standard signal half-width.  Only the
    # excess width of this particular fan-out has to be queried here.
    track_radius = max(0.0, min(WIDTH_BY_NET[net], 0.30) / 2 - SIGNAL_HALF)
    for distance in distances:
        for delta in angles:
            angle = base_angle + delta
            point = xy(cell(pad.x + math.cos(angle) * distance, pad.y + math.sin(angle) * distance))
            if pad.reference == "U4" and net == "GND" and point[1] < 6.0:
                continue
            if not line_is_free(occupancy, "F.Cu", (pad.x, pad.y), point, track_radius, net):
                continue
            if via:
                ok = True
                for layer in LAYERS:
                    for candidate in disk_cells(
                        cell(*point), VIA_SIZE / 2 - SIGNAL_HALF + 0.03
                    ):
                        if not occupancy.allowed(layer, candidate, net):
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    continue
            else:
                # A narrow dogbone may end at a much wider power route.  Make
                # sure the widened route can start here without clipping an
                # adjacent connector pad or feedback component.
                extra = max(0.0, WIDTH_BY_NET[net] / 2 - SIGNAL_HALF)
                if not occupancy.allowed("F.Cu", cell(*point), net, extra):
                    continue
            return point
    raise RuntimeError(
        f"No geometrically safe escape for {pad.reference}.{pad.number} net={net}"
    )


def initialize_obstacles(board: Board, pads: list[PadPoint]):
    occupancy = Occupancy()

    def add_pad_obstacle(layer, pad):
        """Rasterise an expanded, rotated pad rectangle without corner loss."""
        # NPTH/mechanical holes use the board's explicit hole-to-copper rule,
        # which is wider than the ordinary copper-to-copper clearance.
        base_clearance = (
            HOLE_CLEARANCE
            if pad.through and pad.net == ""
            else DEFAULT_CLEARANCE
        )
        margin = base_clearance + SIGNAL_HALF
        half_diagonal = math.hypot(pad.size_x, pad.size_y) / 2 + margin
        center = cell(pad.x, pad.y)
        radius_cells = int(math.ceil(half_diagonal / GRID)) + 1
        theta = math.radians(-pad.angle)
        cos_theta, sin_theta = math.cos(theta), math.sin(theta)
        for x_index in range(center[0] - radius_cells, center[0] + radius_cells + 1):
            for y_index in range(center[1] - radius_cells, center[1] + radius_cells + 1):
                px, py = xy((x_index, y_index))
                dx, dy = px - pad.x, py - pad.y
                local_x = dx * cos_theta - dy * sin_theta
                local_y = dx * sin_theta + dy * cos_theta
                outside_x = max(abs(local_x) - pad.size_x / 2, 0.0)
                outside_y = max(abs(local_y) - pad.size_y / 2, 0.0)
                if math.hypot(outside_x, outside_y) <= margin + 1e-9:
                    occupancy.cells[layer][(x_index, y_index)].add(pad.net)

    for pad in pads:
        # Rectangular blocking is conservative for round/oval pads, but it is
        # exact for the many SMD pads that dominate this design and avoids the
        # corner under-bounding that caused real power-net shorts.
        add_pad_obstacle("F.Cu", pad)
        if pad.through:
            add_pad_obstacle("B.Cu", pad)
            add_pad_obstacle("In2.Cu", pad)

    # Mechanical holes are not electrical pads, but all outer-layer routes
    # must observe the board's explicit 0.25 mm hole-to-copper clearance.
    for x, y, drill_x, drill_y in all_npth_holes(board):
        radius = max(drill_x, drill_y) / 2 + HOLE_CLEARANCE + SIGNAL_HALF
        for layer in LAYERS:
            occupancy.add_disk(layer, x, y, radius, "#NPTH")

    # NFC antenna: all routing layers clear of copper/components.  The two
    # terminal pads at x~=48 are outside the hard region.
    for x_index in range(int(4 / GRID), int(47.1 / GRID) + 1):
        for y_index in range(int(16 / GRID), int(59 / GRID) + 1):
            for layer in LAYERS:
                occupancy.cells[layer][(x_index, y_index)].add("#NFC_KEEP_OUT")
    # ESP32-C6 module antenna end, including the internal layers.
    for x_index in range(int(92.1 / GRID), int(105.9 / GRID) + 1):
        for y_index in range(0, int(5.7 / GRID) + 1):
            for layer in LAYERS:
                occupancy.cells[layer][(x_index, y_index)].add("#ESP_ANT_KEEP_OUT")
    # M3 holes and edge clearance.
    for x, y in ((4, 4), (146, 4), (4, 71), (146, 71)):
        for layer in LAYERS:
            occupancy.add_disk(layer, x, y, 3.7, "#MOUNTING_HOLE")
    return occupancy


def fanout_endpoints(board, net_by_name, pads_by_net, occupancy, net_name, front):
    endpoints = []
    seen = set()
    seen_footprint_pads = set()
    preferred_duplicate = {}
    for candidate in pads_by_net[net_name]:
        duplicate_key = (candidate.reference, candidate.number)
        if candidate.reference.startswith("SW"):
            current = preferred_duplicate.get(duplicate_key)
            if current is None or candidate.x < current.x:
                preferred_duplicate[duplicate_key] = candidate
    for pad in pads_by_net[net_name]:
        # The two physical pad-2 locations inside AE1 are already joined by
        # the coil's B.Cu underpass.  Only its right-hand terminal is a routing
        # endpoint; treating the inner via as another endpoint would ask the
        # router to duplicate the coil connection.
        if (pad.reference == "AE1" and pad.number == "2"
                and abs(pad.x - ANTENNA.placement[0]) > 0.1):
            continue
        footprint_pad = (pad.reference, pad.number)
        if (
            footprint_pad in preferred_duplicate
            and pad != preferred_duplicate[footprint_pad]
        ):
            continue
        if footprint_pad in seen_footprint_pads:
            # Repeated pad numbers on tactile switches and the ESP exposed pad
            # are one internally common component terminal, not independent
            # ratsnest endpoints.
            continue
        seen_footprint_pads.add(footprint_pad)
        key = (round(pad.x, 3), round(pad.y, 3), pad.through)
        if key in seen:
            continue
        seen.add(key)
        if front:
            fan_width = min(WIDTH_BY_NET[net_name], 0.30)
            force_via = (pad.reference, pad.number) in FORCE_VIA_PADS
            path = FIXED_ESCAPE_PATHS.get((pad.reference, pad.number))
            if path is None:
                point = candidate_escape(pad, occupancy, net_name, via=force_via)
                path = [point]
            previous = (pad.x, pad.y)
            for point in path:
                if (
                    (pad.reference, pad.number) not in REVIEWED_DENSE_PATHS
                    and not line_is_free(
                    occupancy, "F.Cu", previous, point,
                    max(0.0, fan_width / 2 - SIGNAL_HALF), net_name,
                    )
                ):
                    raise RuntimeError(
                        f"Fixed fan-out path obstructed for {pad.reference}.{pad.number} net={net_name}"
                    )
                segment(
                    board, net_by_name, net_name, previous, point,
                    fan_width, "F.Cu",
                )
                occupancy.add_line(
                    "F.Cu", previous, point,
                    fan_width / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
                    net_name,
                )
                previous = point
            endpoint_layer = "F.Cu"
            if force_via:
                add_via(board, net_by_name, net_name, point)
                for layer in LAYERS:
                    occupancy.add_disk(
                        layer, *point,
                        VIA_SIZE / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
                        net_name,
                    )
                endpoint_layer = "B.Cu"
            endpoints.append(
                Endpoint(*point, endpoint_layer, f"{pad.reference}.{pad.number}")
            )
            continue
        if pad.through:
            endpoints.append(Endpoint(pad.x, pad.y, "B.Cu", f"{pad.reference}.{pad.number}"))
            continue

        point = candidate_escape(pad, occupancy, net_name, via=True)
        fan_width = min(WIDTH_BY_NET[net_name], 0.25)
        segment(board, net_by_name, net_name, (pad.x, pad.y), point, fan_width, "F.Cu")
        add_via(board, net_by_name, net_name, point)
        occupancy.add_line(
            "F.Cu", (pad.x, pad.y), point,
            fan_width / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF, net_name,
        )
        for layer in LAYERS:
            occupancy.add_disk(
                layer, *point,
                VIA_SIZE / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
                net_name,
            )
        endpoints.append(Endpoint(*point, "B.Cu", f"{pad.reference}.{pad.number}"))
    return endpoints


def direct_pad_endpoints(pads):
    """Deduplicate physical pads and duplicate-number footprint copper."""
    result = []
    seen_coordinates = set()
    seen_footprint_pads = set()
    for pad in pads:
        footprint_key = (pad.reference, pad.number)
        if footprint_key in seen_footprint_pads:
            continue
        seen_footprint_pads.add(footprint_key)
        coordinate_key = (round(pad.x, 3), round(pad.y, 3))
        if coordinate_key in seen_coordinates:
            continue
        seen_coordinates.add(coordinate_key)
        result.append(Endpoint(pad.x, pad.y, "F.Cu", f"{pad.reference}.{pad.number}"))
    return result


def route_direct_net(board, net_by_name, occupancy, net_name, pads, *, width_cap=0.35):
    """Connect a compact local net with its Euclidean MST on F.Cu."""
    endpoints = direct_pad_endpoints(pads)
    width = WIDTH_BY_NET[net_name]
    edge_count = 0
    for first_index, second_index in endpoint_mst(endpoints):
        first, second = endpoints[first_index], endpoints[second_index]
        start, end = (first.x, first.y), (second.x, second.y)
        # Fine-pitch device pins fan out at 0.25 mm before the nominal width;
        # the route is still widened for the remainder by the connected pads.
        actual_width = min(width, width_cap)
        segment(board, net_by_name, net_name, start, end, actual_width, "F.Cu")
        occupancy.add_line(
            "F.Cu", start, end,
            actual_width / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
            net_name,
        )
        edge_count += 1
    return endpoints, edge_count


def add_manual_chain(
    board, net_by_name, occupancy, pads_by_net, net_name, terminal_ids, width,
):
    """Add a verified short local chain and return terminals folded into it."""
    selected = []
    for reference, number in terminal_ids:
        matches = [
            pad for pad in pads_by_net[net_name]
            if pad.reference == reference and pad.number == number
        ]
        if not matches:
            raise RuntimeError(f"Manual chain terminal missing: {net_name} {reference}.{number}")
        selected.append(min(matches, key=lambda pad: pad.x))
    for first, second in zip(selected, selected[1:]):
        start, end = (first.x, first.y), (second.x, second.y)
        if not line_is_free(
            occupancy, "F.Cu", start, end,
            max(0.0, width / 2 - SIGNAL_HALF), net_name,
        ):
            raise RuntimeError(
                f"Manual chain obstructed: {net_name} {first.reference}.{first.number}"
                f" -> {second.reference}.{second.number}"
            )
        segment(board, net_by_name, net_name, start, end, width, "F.Cu")
        occupancy.add_line(
            "F.Cu", start, end,
            width / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF, net_name,
        )
    return {(pad.reference, pad.number) for pad in selected[1:]}


def endpoint_mst(endpoints: list[Endpoint]):
    """Return a Euclidean Prim MST over endpoint indices."""
    if len(endpoints) < 2:
        return []
    connected = {0}
    edges = []
    while len(connected) < len(endpoints):
        best = None
        for first in connected:
            for second in range(len(endpoints)):
                if second in connected:
                    continue
                a, b = endpoints[first], endpoints[second]
                distance = math.hypot(a.x - b.x, a.y - b.y)
                if best is None or distance < best[0]:
                    best = (distance, first, second)
        _, first, second = best
        connected.add(second)
        edges.append((first, second))
    return edges


def astar(
    occupancy: Occupancy, start, target, allowed_layers, net_name, width,
    *, max_visits=MAX_ASTAR_VISITS,
):
    layer_indices = [ROUTER_LAYERS[layer] for layer in allowed_layers]
    start_node = (start[0], start[1], ROUTER_LAYERS[start[2]])
    target_node = (target[0], target[1], ROUTER_LAYERS[target[2]])
    queue = [(0.0, 0.0, start_node)]
    came_from = {}
    score = {start_node: 0.0}
    # Orthogonal moves only.  Diagonal moves may cross in the middle of a
    # cell despite every endpoint cell being individually clear.
    directions = [
        (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
    ]
    extra_radius = max(0.0, width / 2 - SIGNAL_HALF)

    def heuristic(node):
        return math.hypot(node[0] - target_node[0], node[1] - target_node[1])

    visited = 0
    while queue:
        _estimate, current_score, current = heapq.heappop(queue)
        if current_score != score.get(current):
            continue
        visited += 1
        if visited > max_visits:
            return None, visited
        if current == target_node:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path, visited
        x_index, y_index, layer_index = current
        layer_name = LAYERS[layer_index]
        for dx, dy, cost in directions:
            neighbor = (x_index + dx, y_index + dy, layer_index)
            if not occupancy.allowed(
                layer_name, neighbor[:2], net_name, extra_radius
            ) and neighbor != target_node:
                continue
            next_score = current_score + cost
            if next_score < score.get(neighbor, float("inf")):
                score[neighbor] = next_score
                came_from[neighbor] = current
                heapq.heappush(queue, (next_score + heuristic(neighbor), next_score, neighbor))
        if len(layer_indices) > 1:
            for other_layer in layer_indices:
                if other_layer == layer_index:
                    continue
                neighbor = (x_index, y_index, other_layer)
                other_name = LAYERS[other_layer]
                px, py = xy(neighbor[:2])
                # Do not drop layer-changing vias beside the ESP module or
                # PN7161 fine-pitch fan-out.  Leave those regions on F.Cu and
                # change layer only after the parallel escape tracks separate.
                esp_fanout = 91.5 <= px <= 108.5 and 5.8 <= py <= 18.5
                pn_fanout = (
                    71.5 <= px <= 83.5 and 31.5 <= py <= 43.5
                    and net_name not in {"3V3", "PN_TVDD", "PN_VDD", "SYS_5V"}
                )
                if esp_fanout or pn_fanout:
                    continue
                if not occupancy.allowed(other_name, neighbor[:2], net_name):
                    continue
                # A through via occupies every copper layer.
                if any(
                    not occupancy.allowed(
                        layer, neighbor[:2], net_name,
                        VIA_SIZE / 2 - SIGNAL_HALF + 0.03,
                    )
                    for layer in LAYERS
                ):
                    continue
                next_score = current_score + 12.0
                if next_score < score.get(neighbor, float("inf")):
                    score[neighbor] = next_score
                    came_from[neighbor] = current
                    heapq.heappush(queue, (next_score + heuristic(neighbor), next_score, neighbor))
    return None, visited


def compress_path(path):
    chunks = []
    start = path[0]
    previous = path[0]
    previous_direction = None
    for current in path[1:]:
        direction = (
            int(math.copysign(1, current[0] - previous[0])) if current[0] != previous[0] else 0,
            int(math.copysign(1, current[1] - previous[1])) if current[1] != previous[1] else 0,
            current[2] - previous[2],
        )
        if previous_direction is not None and direction != previous_direction:
            chunks.append((start, previous))
            start = previous
        previous_direction = direction
        previous = current
    chunks.append((start, previous))
    return chunks


def emit_path(board, net_by_name, occupancy, net_name, path, width):
    via_nodes = set()
    for first, second in zip(path, path[1:]):
        if first[2] != second[2]:
            via_nodes.add((first[0], first[1]))
    for node in via_nodes:
        point = xy(node)
        add_via(board, net_by_name, net_name, point)
        for layer in LAYERS:
            occupancy.add_disk(
                layer, *point,
                VIA_SIZE / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
                net_name,
            )
    for start, end in compress_path(path):
        if start[2] != end[2]:
            continue
        layer = LAYERS[start[2]]
        point_a, point_b = xy(start[:2]), xy(end[:2])
        segment(board, net_by_name, net_name, point_a, point_b, width, layer)
        occupancy.add_line(
            layer, point_a, point_b,
            width / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
            net_name,
        )


def route_net(
    board, net_by_name, occupancy, net_name, endpoints, front,
    *, connected=None, remaining=None, max_visits=MAX_ASTAR_VISITS,
    candidate_limit=8,
):
    if len(endpoints) < 2:
        return True, 0, 0, {0}, set()
    if net_name in STRICT_FRONT_NETS:
        allowed_layers = ["F.Cu"]
    elif front:
        allowed_layers = ["F.Cu", "B.Cu", "In2.Cu"]
    else:
        allowed_layers = ["B.Cu", "In2.Cu"]
    total_visited = 0
    routed_edges = 0
    connected = {0} if connected is None else set(connected)
    remaining = (
        set(range(1, len(endpoints))) if remaining is None else set(remaining)
    )
    while remaining:
        candidates = sorted(
            (
                math.hypot(
                    endpoints[first].x - endpoints[second].x,
                    endpoints[first].y - endpoints[second].y,
                ),
                first,
                second,
            )
            for first in connected
            for second in remaining
        )
        selected = None
        # Trying every pair on a 13-endpoint rail can multiply several known
        # impossible 120k-node searches.  The eight nearest alternatives give
        # the tree useful freedom while keeping a deterministic runtime bound.
        candidate_subset = candidates if candidate_limit is None else candidates[:candidate_limit]
        for _distance, first_index, second_index in candidate_subset:
            first, second = endpoints[first_index], endpoints[second_index]
            start = (*cell(first.x, first.y), first.layer)
            target = (*cell(second.x, second.y), second.layer)
            path, visited = astar(
                occupancy, start, target, allowed_layers, net_name,
                WIDTH_BY_NET[net_name], max_visits=max_visits,
            )
            total_visited += visited
            if path is not None:
                selected = (second_index, path)
                break
        if selected is None:
            return False, routed_edges, total_visited, connected, remaining
        second_index, path = selected
        emit_path(
            board, net_by_name, occupancy, net_name, path,
            WIDTH_BY_NET[net_name],
        )
        connected.add(second_index)
        remaining.remove(second_index)
        routed_edges += 1
        if len(endpoints) > 5:
            print(
                f"  {net_name}: edge {routed_edges}/{len(endpoints)-1} -> "
                f"{endpoints[second_index].source} visited={total_visited}",
                flush=True,
            )
    return True, routed_edges, total_visited, connected, remaining


def add_ground_connections(board, net_by_name, occupancy, ground_pads, signal_vias):
    """Drop a via from every ground pad to the plane, and return their points.

    This used to have a second half that rastered In1.Cu into a mesh of tracks
    and stitched its islands together.  pour_ground_planes.py deletes every one
    of those tracks and pours a real zone instead, so the raster only ever
    reached the manufacturing image as an absence -- while still being able to
    fail the build when an island would not stitch.  It ran after every signal
    net was routed, so removing it moves no signal: the board comes out
    byte-identical.
    """
    ground_vias = []
    seen = set()
    for pad in ground_pads:
        coordinate_key = (round(pad.x, 3), round(pad.y, 3))
        if coordinate_key in seen:
            continue
        seen.add(coordinate_key)
        if pad.through:
            ground_vias.append((pad.x, pad.y))
            continue
        # Exposed PN7161 pad: four thermal/ground vias inside the slug.
        if pad.reference == "U5" and pad.number == "41":
            for dx, dy in ((-0.8, -0.8), (0.8, -0.8), (-0.8, 0.8), (0.8, 0.8)):
                point = (pad.x + dx, pad.y + dy)
                segment(board, net_by_name, "GND", (pad.x, pad.y), point, 0.35, "F.Cu")
                add_via(board, net_by_name, "GND", point, size=0.60, drill=0.30)
                occupancy.add_line(
                    "F.Cu", (pad.x, pad.y), point,
                    0.35 / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF, "GND",
                )
                for layer in LAYERS:
                    occupancy.add_disk(
                        layer, *point,
                        0.60 / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
                        "GND",
                    )
                ground_vias.append(point)
            continue
        point = candidate_escape(pad, occupancy, "GND", via=True)
        segment(board, net_by_name, "GND", (pad.x, pad.y), point, 0.30, "F.Cu")
        add_via(board, net_by_name, "GND", point)
        occupancy.add_line(
            "F.Cu", (pad.x, pad.y), point,
            0.30 / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF, "GND",
        )
        for layer in LAYERS:
            occupancy.add_disk(
                layer, *point,
                VIA_SIZE / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
                "GND",
            )
        ground_vias.append(point)

    # Extra high-current ground vias beside the servo output reservoir.
    # The final three also need explicit F.Cu ties; otherwise a through-via
    # that touches only In1.Cu is electrically valid but useless for
    # carrying the intended capacitor/regulator return current.
    tied_ground_vias = {
        (144.0, 52.0): ("C42", "2", 0.60),
        (145.0, 55.5): ("C43", "2", 0.60),
        (128.5, 53.0): ("U6", "1", 0.40),
    }
    ground_pad_by_id = {
        (pad.reference, pad.number): pad for pad in ground_pads
    }
    for point in ((140.0, 64.0), (140.0, 66.0), *tied_ground_vias):
        add_via(board, net_by_name, "GND", point, size=0.80, drill=0.40)
        tie = tied_ground_vias.get(point)
        if tie is not None:
            reference, number, width = tie
            pad = ground_pad_by_id[(reference, number)]
            start = (pad.x, pad.y)
            segment(board, net_by_name, "GND", start, point, width, "F.Cu")
            occupancy.add_line(
                "F.Cu", start, point,
                width / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
                "GND",
            )
        for layer in LAYERS:
            occupancy.add_disk(
                layer, *point,
                0.80 / 2 + DEFAULT_CLEARANCE + SIGNAL_HALF,
                "GND",
            )
        ground_vias.append(point)

    return ground_vias


def apply_reviewed_manufacturing_neckdowns(board):
    """Relieve track width where a 3V3 run crowds SYS_5V, without moving copper.

    The U5 supply approach is routed in a 0.50 mm corridor that leaves under
    0.10 mm to the SYS_5V fan-out beside it.  Narrowing those few segments to
    0.40 mm restores the clearance; they carry only logic current, so the width
    is not doing any work.

    This used to match the segments by their literal coordinates from one
    particular router run, which meant any placement change made the whole
    chain refuse to regenerate.  Match by the condition instead -- a 3V3
    segment that is too close to a SYS_5V segment -- so the relief follows the
    copper wherever the router puts it.
    """
    def seg_distance(a, b):
        """Shortest distance between two segments' centre lines."""
        def clamp(v, lo, hi):
            return max(lo, min(hi, v))

        ax, ay = a.start.X, a.start.Y
        bx, by = a.end.X - ax, a.end.Y - ay
        cx, cy = b.start.X, b.start.Y
        dx, dy = b.end.X - cx, b.end.Y - cy
        best = float("inf")
        # Sample-free approach is overkill here; the segments are short and few.
        for i in range(21):
            t1 = i / 20
            px, py = ax + bx * t1, ay + by * t1
            denom = dx * dx + dy * dy
            t2 = 0.0 if denom == 0 else clamp(((px - cx) * dx + (py - cy) * dy) / denom, 0.0, 1.0)
            qx, qy = cx + dx * t2, cy + dy * t2
            best = min(best, math.hypot(px - qx, py - qy))
        return best

    CLEARANCE = 0.10
    RELIEVED_WIDTH = 0.40

    def named(item, name):
        return (isinstance(item, Segment) and item.layer == "F.Cu"
                and board.nets[item.net].name == name)

    victims = [i for i in board.traceItems if named(i, "3V3") and i.width > RELIEVED_WIDTH]
    others = [i for i in board.traceItems if named(i, "SYS_5V")]

    matched = 0
    for seg in victims:
        for other in others:
            centre = seg_distance(seg, other)
            if centre - (seg.width + other.width) / 2 >= CLEARANCE:
                continue
            # Narrow the 3V3 side first; if the pair is still too close, relieve
            # the SYS_5V side as well.  Both carry logic current here -- 0.40 mm
            # of 1 oz outer copper is good for 1.23 A at a 10 C rise against a
            # load on the order of 0.5 A, and SYS_5V already runs at 0.30 mm
            # elsewhere on this board -- so the width is not doing any work.
            # No break: one 3V3 run can crowd several SYS_5V segments, and
            # relieving only the first one leaves the rest violating.
            if centre - (RELIEVED_WIDTH + other.width) / 2 >= CLEARANCE:
                if seg.width != RELIEVED_WIDTH:
                    seg.width = RELIEVED_WIDTH
                    matched += 1
                continue
            if centre - RELIEVED_WIDTH >= CLEARANCE:
                if seg.width != RELIEVED_WIDTH:
                    seg.width = RELIEVED_WIDTH
                    matched += 1
                if other.width != RELIEVED_WIDTH:
                    other.width = RELIEVED_WIDTH
                    matched += 1
                continue
    print(f"  Reviewed 3V3/SYS_5V neck-downs applied: {matched}")


def main() -> None:
    global ALL_PADS, NPTH_HOLES
    board = Board.from_file(str(BOARD_PATH))
    # Idempotent regeneration: placement board has no tracks; reject accidental
    # routing on top of an already routed board.
    if board.traceItems:
        raise RuntimeError("Board already contains tracks; rerun generate_board.py first")
    net_by_name = {net.name: net for net in board.nets}
    ALL_PADS = all_pad_points(board)
    NPTH_HOLES = all_npth_holes(board)
    pads_by_net = defaultdict(list)
    for point in ALL_PADS:
        pads_by_net[point.net].append(point)
    occupancy = initialize_obstacles(board, ALL_PADS)

    # KiCad does not assume that duplicated same-number pads inside a tactile
    # switch are connected by the switch's internal metal.  Add the three
    # same-net top-side links explicitly so the manufactured board and the
    # official connectivity engine agree.
    for net_name, path in (
        ("ESP_EN", [(109.4, 20.5), (116.6, 20.5)]),
        ("BOOT", [(120.4, 20.5), (127.6, 20.5)]),
        ("SERVICE_BTN", [(132.4, 20.5), (139.6, 20.5)]),
    ):
        add_explicit_path(
            board, net_by_name, occupancy, net_name, "F.Cu", 0.25, path,
        )

    # AE1's inner coil end returns to the outer ANT_P terminal on B.Cu.  The
    # footprint graphic defines the physical copper, while this net-assigned
    # board segment gives KiCad's connectivity engine the same information.
    add_explicit_path(
        board, net_by_name, occupancy, "ANT_P", "B.Cu", 0.40,
        ANTENNA.underpass,
    )

    # Fold obviously local passive clusters into one routing endpoint.  This
    # prevents a long-rail maze search from spending millions of nodes trying
    # to reach a capacitor that already sits in a clear straight row.
    folded = defaultdict(set)
    folded["SYS_5V"] |= add_manual_chain(
        board, net_by_name, occupancy, pads_by_net, "SYS_5V",
        [("C18", "1"), ("C20", "1")], 0.50,
    )
    folded["SYS_5V"] |= add_manual_chain(
        board, net_by_name, occupancy, pads_by_net, "SYS_5V",
        [("U3", "2"), ("U3", "3")], 0.20,
    )
    folded["SYS_5V"] |= add_manual_chain(
        board, net_by_name, occupancy, pads_by_net, "SYS_5V",
        [("U5", "12"), ("U5", "13")], 0.20,
    )
    folded["BAT_ADC"] |= add_manual_chain(
        board, net_by_name, occupancy, pads_by_net, "BAT_ADC",
        [("R10", "2"), ("R11", "1"), ("TP12", "1"), ("C14", "1")],
        0.25,
    )
    folded["ESP_EN"] |= add_manual_chain(
        board, net_by_name, occupancy, pads_by_net, "ESP_EN",
        [("R6", "2"), ("C13", "1"), ("SW1", "1")], 0.25,
    )
    # L2.2 and C9.1 are a clear, direct 3V3 branch.  Folding C9 into this
    # local connection prevents the wide supply router from dropping a via
    # between C9's supply and ground pads.
    folded["3V3"] |= add_manual_chain(
        board, net_by_name, occupancy, pads_by_net, "3V3",
        [("L2", "2"), ("C9", "1")], 0.50,
    )
    for net_name, terminal_ids in folded.items():
        pads_by_net[net_name] = [
            pad for pad in pads_by_net[net_name]
            if (pad.reference, pad.number) not in terminal_ids
        ]

    # BAT_SYS is a branched high-current tree.  A Euclidean MST is unsafe here
    # because it can cut directly across an adjacent converter output pin.

    # Wider/critical nets first; small control nets fill the remaining channels.
    non_ground_nets = [
        name for name in pads_by_net
        if name not in {"GND", "BAT_SYS"} and not name.startswith("#NC:")
    ]
    def route_priority(name):
        if name == "PN_VMID":
            return -3
        if name in {"PN_TX1", "PN_TX2"}:
            return -2
        if name in {"PN_RXN", "PN_RXP"}:
            return -1
        if name in RF_NETS:
            return 0
        # The 27.12 MHz crystal loop must claim its short top-layer corridor
        # before the wide PN_VDD rail.  Supplies can change layer; crystal
        # traces are intentionally restricted to F.Cu.
        if name in CRYSTAL_NETS:
            return 0.25
        if name == "SERVO_PWM":
            return 0.5
        if name in {"SERVO_6V", "SERVO_6V_OUT", "SW_SERVO", "BAT_FUSED", "BAT_RAW"}:
            return 1
        if name in {"SW_3V3", "SW_5V", "BST_3V3", "BST_5V", "BST_SERVO"}:
            return 2
        if name in {"USB_DN_CONN", "USB_DP_CONN", "USB_DM", "USB_DP"}:
            return 3
        if name in {"3V3", "SYS_5V", "PN_TVDD", "PN_VDD", "5V_BAT"}:
            return 3
        if name in {
            "BAT_ADC", "SERVICE_BTN", "SERVO_PWM", "STATUS_LED", "ESP_EN",
            "PN_NSS_IC", "PN_MOSI_IC", "PN_MISO_IC", "PN_SCK_IC",
            "PN_DWL_REQ", "PN_IRQ", "PN_VEN",
        }:
            return 4
        if name in CRYSTAL_NETS or name in {
            "USB_DM", "USB_DP", "USB_DN_CONN", "USB_DP_CONN",
        }:
            return 5
        if name in POWER_NETS:
            return 6
        # PN_MOSI is physically a front-fanned net, but its long leg must be
        # routed after the neighbouring SCK and UART nets (see note below).
        if name == "PN_MOSI":
            return 10
        if name in FRONT_NETS:
            return 7
        if name in USB_NETS:
            return 8
        # Route the long external MOSI leg after the neighbouring SCK and
        # UART traces.  Its two forced endpoint vias give it lower-layer
        # options; letting it go first otherwise consumes their only narrow
        # escape corridors with a long In2.Cu staircase.
        return 9

    non_ground_nets.sort(
        key=lambda name: (
            route_priority(name), -WIDTH_BY_NET[name],
            -len(pads_by_net[name]), name,
        )
    )

    audit_rows = []
    signal_vias = []
    failures = []

    # Reserve the high-current tree before signal fan-out and maze routing.
    # This makes every later route avoid its real copper instead of laying the
    # BAT_SYS trunk across already-completed control tracks.
    bat_trunk_edges, bat_vias = add_bat_sys_tree(
        board, net_by_name, occupancy, pads_by_net,
    )
    signal_vias.extend(bat_vias)
    audit_rows.append(
        (
            "BAT_SYS", "F.Cu/In2.Cu", len(pads_by_net["BAT_SYS"]),
            len(pads_by_net["BAT_SYS"]) - 1, len(bat_vias), 0,
            "PASS (explicit current tree)",
        )
    )

    # Fan every SMD pad out before any long maze route is created.  This is
    # essential at U5: routing one complete RF net first can otherwise wall off
    # the adjacent 0.50 mm-pitch supply or control pin before it gets a chance
    # to leave the package.
    prepared = {}
    for net_name in non_ground_nets:
        front = net_name in FRONT_NETS
        before_vias = sum(isinstance(item, Via) for item in board.traceItems)
        endpoints = fanout_endpoints(
            board, net_by_name, pads_by_net, occupancy, net_name, front
        )
        after_vias = sum(isinstance(item, Via) for item in board.traceItems)
        if not front:
            recent = [
                item for item in board.traceItems if isinstance(item, Via)
            ][before_vias:after_vias]
            signal_vias.extend((item.position.X, item.position.Y) for item in recent)
        prepared[net_name] = (front, endpoints, before_vias, after_vias)

    ground_vias = add_ground_connections(
        board, net_by_name, occupancy, pads_by_net["GND"], signal_vias,
    )

    # Keep every net's escape reachable.
    #
    # Fanout has run for every net and ground has taken its vias, so all
    # endpoints are known and nothing else is routed yet.  Reserve a little
    # room around each one, owned by its own net, so a net routed early cannot
    # seal in a net routed late.
    #
    # Without this the router is coupled across the whole board in a way that
    # makes placement experiments unreadable.  Moving R29 next to U6 rerouted
    # SERVO_6V_OUT -- a 116 mm net spanning half the board -- so that it passed
    # 0.311 mm from U3's 3V3 escape at (98.500, 37.375), 28 mm away, and 3V3
    # then reached none of its eleven remaining endpoints.  See #3.
    for reserved_net, (_front, endpoints, _before, _after) in prepared.items():
        for point in endpoints:
            occupancy.add_disk(
                point.layer, point.x, point.y, ESCAPE_KEEPALIVE, reserved_net
            )

    for net_name in non_ground_nets:
        front, endpoints, before_vias, fanout_vias = prepared[net_name]
        print(f"Routing {net_name} ({len(endpoints)} endpoints)...", flush=True)
        success, edge_count, visited, connected, remaining = route_net(
            board, net_by_name, occupancy, net_name, endpoints, front
        )
        if not success:
            print(
                f"  {net_name}: fast pass left {len(remaining)} endpoint(s); "
                "retrying from the existing tree",
                flush=True,
            )
            retry_success, retry_edges, retry_visited, connected, remaining = route_net(
                board, net_by_name, occupancy, net_name, endpoints, front,
                connected=connected, remaining=remaining, max_visits=120_000,
                candidate_limit=4,
            )
            success = retry_success
            edge_count += retry_edges
            visited += retry_visited
        after_vias = sum(isinstance(item, Via) for item in board.traceItems)
        # Capture every new non-GND route via for In1 clearance clipping,
        # including nets that began on F.Cu and changed layer later.
        recent = [
            item for item in board.traceItems
            if isinstance(item, Via)
            and board.nets[item.net].name != "GND"
        ][fanout_vias:after_vias]
        signal_vias.extend((item.position.X, item.position.Y) for item in recent)
        audit_rows.append(
            (net_name, "F.Cu" if front else "B.Cu/In2.Cu", len(endpoints), edge_count, after_vias - before_vias, visited, "PASS" if success else "FAIL")
        )
        if not success:
            failures.append(net_name)
            # A bare FAIL row says nothing about what to do next.  Name the
            # endpoint the search grew from and the ones it never reached: when
            # a net fails completely it is because that first endpoint is boxed
            # in, and nothing else in the output reveals which one it is.
            #
            # This is what identified the coupling behind issue #3.  Moving R29
            # next to U6 rerouted SERVO_6V_OUT -- a 116 mm net spanning half the
            # board -- so that it passed 0.311 mm from U3's 3V3 escape at
            # (98.500, 37.375), 28 mm away, and 3V3 lost all 11 of its
            # remaining endpoints.
            print(f"  {net_name}: UNREACHED after retry ({len(remaining)} of "
                  f"{len(endpoints)} endpoints)", flush=True)
            print(f"    grew from: {endpoints[0].source} "
                  f"({endpoints[0].x:.3f}, {endpoints[0].y:.3f}) on {endpoints[0].layer}",
                  flush=True)
            for index in sorted(remaining):
                point = endpoints[index]
                print(f"    unreached: {point.source} ({point.x:.3f}, {point.y:.3f}) "
                      f"on {point.layer}", flush=True)

    # Close the one reviewed narrow corridor after all generic signal trees
    # are fixed.  It joins two existing through-vias and therefore adds no
    # via or plane discontinuity.  The Shapely audit below the generation
    # stage checks the real copper shapes rather than trusting this exception.
    for net_name in REVIEWED_BRIDGES:
        if net_name not in failures:
            continue
        add_reviewed_bridge(board, net_by_name, occupancy, net_name)
        failures.remove(net_name)
        audit_rows = [
            (
                row[0], row[1], row[2], row[3] + 1, row[4], row[5],
                "PASS (reviewed bridge)",
            ) if row[0] == net_name else row
            for row in audit_rows
        ]

    apply_reviewed_manufacturing_neckdowns(board)

    # The second ground pass used to raster In1.Cu into a mesh of copper tracks
    # and then stitch its islands together.  pour_ground_planes.py deletes every
    # one of those tracks a few lines below and pours a real zone instead, so
    # the pass was emitting copper only to have it thrown away -- while still
    # being able to fail the whole build when an island would not stitch.
    #
    # The ground *vias* are not dead and are still placed, by the fanout-only
    # pass before the signal loop.  Only the raster is gone.  It ran after
    # every signal net was routed, so removing it cannot move a signal.
    merged_via_groups, removed_vias = consolidate_close_same_net_vias(board)
    removed_dangling_vias = remove_redundant_vias(board)
    board.to_file(str(BOARD_PATH), encoding="utf-8")
    # generate_board.py installs U4's KiCad-10 native block, but the round trip
    # through kiutils above re-serializes it and loses the reference field's
    # position, layer and effects -- which lands the designator on pad 49 and
    # trips silk_over_copper.  Reinstall it here so the routed board is the one
    # that carries the reviewed block, instead of relying on someone
    # remembering to run tools/u4_native.py by hand afterwards.
    install_u4_native_block(BOARD_PATH)
    # The router lays In1.Cu ground down as tracks because it has no filler of
    # its own.  Hand that layer, and the bare back side, over to real zones now
    # that the rest of the board is final.
    pour_ground_planes.main()
    poured = Board.from_file(str(BOARD_PATH))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with CONNECTIVITY_CSV.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow(["Net", "Layer policy", "Unique endpoints", "MST edges", "New vias", "A* visited nodes", "Status"])
        writer.writerows(audit_rows)

    # Count what is on disk, not what the router built: the ground pour has
    # since replaced the In1.Cu mesh and added its stitching vias.
    segment_count = sum(isinstance(item, Segment) for item in poured.traceItems)
    via_count = sum(isinstance(item, Via) for item in poured.traceItems)
    lines = [
        f"{PROJECT_NAME} ROUTING AUDIT",
        "=" * 72,
        f"Board: {BOARD_SIZE[0]} x {BOARD_SIZE[1]} mm, 4 layers, 1.6 mm",
        f"Footprints: {len(board.footprints)}",
        f"Electrical nets: {len(board.nets) - 1}",
        f"Track segments: {segment_count}",
        f"Vias: {via_count} (GND connection points: {len(ground_vias)})",
        f"Copper zones: {len(poured.zones)} (2 ground pours, 2 all-layer antenna keep-outs)",
        f"Close same-net via groups consolidated: {merged_via_groups} ({removed_vias} redundant vias removed)",
        f"Single-layer (no-transition) vias removed: {removed_dangling_vias}",
        f"BAT_SYS F.Cu high-current trunk edges: {bat_trunk_edges}",
        f"NFC keep-out: x=4..47.1, y=16..59 mm on every routing/plane layer",
        f"ESP antenna keep-out: x=92.1..105.9, y=0..5.7 mm on every layer",
        "",
        "Per-net result:",
    ]
    for row in audit_rows:
        lines.append(
            f"{row[0]:16s} {row[1]:11s} endpoints={row[2]:2d} edges={row[3]:2d} vias={row[4]:2d} {row[6]}"
        )
    if failures:
        lines.extend(["", "ROUTING FAILURES: " + ", ".join(failures)])
    else:
        lines.extend(["", "ROUTING CONNECTIVITY: PASS (all non-GND endpoint trees completed)"])
    lines.extend(
        [
            "GND CONNECTIVITY: explicit dogbone/thermal vias into In1.Cu and B.Cu",
            "                  ground pours -- see reports/GROUND_POUR.txt",
            "NOTE: RF matching values remain tuning starts, not door-independent guarantees.",
        ]
    )
    ROUTE_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not failures:
        # Publish the routed board for local_passive_placer.py to plan against.
        # Placement runs on the *unrouted* board and so cannot see which lanes
        # the router is already using; this is how it finds out without having
        # to fail a route first.
        shutil.copyfile(BOARD_PATH, REPORT_DIR / "ROUTED_REFERENCE.kicad_pcb")
    print("\n".join(lines[:10]))
    if failures:
        print("Failed nets:", ", ".join(failures))
        raise SystemExit(2)
    print("Routing connectivity: PASS")


if __name__ == "__main__":
    main()
