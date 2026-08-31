#!/usr/bin/env python3
"""Generate the Rev A KiCad PCB from the shared electrical manifest.

This first stage creates the exact board outline, antenna copper, footprints,
net assignments and mechanical placement.  Routing is added by route_board.py
after placement audits pass.
"""

from __future__ import annotations

import copy
import math
import sys
from pathlib import Path
from uuid import uuid4


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
KIUTILS = WORKSPACE_ROOT / ".tools" / "py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(KIUTILS))

from design_data import (  # noqa: E402
    BOARD_ONLY,
    BOARD_SIZE,
    DATE,
    GRAPHICS_SYNC_REFS,
    NATIVE_CHILD_ANGLE_REFS,
    PROJECT_NAME,
    REVISION,
    footprint_for,
    parts,
)
from generate_schematics import LIB_OUT, OUT, source_footprint  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.footprint import Footprint  # noqa: E402
from kiutils.items.brditems import LayerToken  # noqa: E402
from kiutils.items.common import Effects, Font, Net, PageSettings, Position, TitleBlock  # noqa: E402
from kiutils.items.gritems import GrLine, GrRect, GrText  # noqa: E402
from u4_native import install_u4_native_block  # noqa: E402


BOARD_PATH = OUT / f"{PROJECT_NAME}.kicad_pcb"
ANTENNA_FOOTPRINT = LIB_OUT / "NFC_Antenna_40x40_4T.kicad_mod"


# These reference labels sit inside dense 0402/0603 and RF/power clusters.
# Keeping them on F.SilkS would place ink over exposed solder mask.  Retain
# them on F.Fab for assembly drawings and PCB inspection instead of hiding the
# electrical identity entirely.
FAB_ONLY_REFERENCES = {
    "C5", "C7", "C10", "C11", "C29", "C34", "C39", "C42", "C43",
    "C44",
    "C8", "C9", "C12", "C13", "C15", "C16", "C17", "C18", "C19",
    "C20", "C21", "C22", "C23", "C24", "C25", "C27", "C28", "C31",
    "C32", "C33", "C35", "C36", "C40",
    "D1", "D2", "D4", "D5",
    "Q1",
    "R2", "R3", "R8", "R9",
    "R10", "R11", "R12", "R13", "R14", "R15", "R16", "R17", "R28",
    "R29", "R31",
    "TP3", "TP11", "U5",
}


# Rotation is part of the electrical placement: for example, the RF chain is
# deliberately ordered PN7161 -> L0 -> C1 -> Rq -> antenna.
FIXED_PLACEMENT = {
    # Board antenna and 13.56 MHz matching chain.
    "AE1": (48.0, 37.5, 0),
    # The differential rows are physically inverted so TX1 (the lower-left
    # PN7161 pin after U5 rotation) has a straight run.  Differential polarity
    # is maintained by swapping the two AE1 terminal net assignments below.
    "R22": (50.7, 40.0, 180), "R23": (50.7, 35.0, 180),
    "R18": (54.2, 40.0, 180), "R19": (54.2, 35.0, 180),
    "C29": (57.7, 40.0, 180), "C30": (57.7, 35.0, 180),
    "C33": (57.7, 42.5, 180), "C34": (57.7, 32.5, 180),
    "C31": (60.2, 44.0, 90), "C32": (60.2, 31.0, 90),
    "C35": (57.0, 45.5, 90), "C36": (57.0, 29.5, 90),
    "C27": (63.0, 44.0, 90), "C28": (63.0, 31.0, 90),
    "L3": (66.5, 40.0, 180), "L4": (66.5, 35.0, 180),
    "R20": (69.7, 44.0, 180), "C37": (73.1, 44.0, 180),
    "R21": (69.7, 31.0, 180), "C38": (73.1, 31.0, 180),
    "R24": (51.5, 52.0, 0), "R25": (51.5, 49.0, 0),
    "J3": (58.5, 50.5, 90),
    # NFC controller and clock.
    # Rotate PN7161 so TX1/TX2/RXP/RXN face the matching network at left,
    # SPI faces the series-resistor bank above, and XTAL pins face X1 below.
    "U5": (78.5, 37.5, 180),
    "X1": (79.0, 48.0, 0),
    "C15": (76.0, 52.0, 90), "C16": (82.0, 52.0, 90),
    "R12": (75.0, 27.0, 90), "R13": (71.5, 27.0, 90),
    "R14": (82.0, 27.0, 90), "R15": (84.5, 27.0, 90),
    # Keep the SPI series-resistor order aligned with the rotated PN7161 pin
    # order; swapping only the physical locations removes an avoidable
    # MOSI/MISO crossover without changing the schematic nets.
    "R16": (89.5, 27.0, 90), "R17": (87.0, 27.0, 90),
    "C17": (83.5, 32.0, 90), "C18": (86.0, 32.0, 90),
    "C19": (84.0, 36.5, 90), "C20": (88.5, 32.0, 90),
    "C21": (84.0, 42.0, 90), "C22": (87.0, 42.0, 90),
    "C23": (84.0, 46.0, 90), "C24": (87.0, 46.0, 90),
    "C25": (90.0, 46.0, 90), "C26": (70.0, 38.25, 180),
    # ESP32-C6 and USB edge connector.
    "U4": (99.0, 8.5, 0),
    "C11": (91.0, 20.0, 90), "C12": (94.0, 20.0, 90),
    "J2": (134.0, 5.3, 180),
    "U2": (122.0, 11.0, 0),
    "R2": (140.0, 12.0, 90), "R3": (143.0, 12.0, 90),
    "R4": (116.0, 9.5, 0), "R5": (116.0, 14.0, 0),
    "SW1": (113.0, 22.0, 0), "SW2": (124.0, 22.0, 0),
    "SW3": (136.0, 22.0, 0),
    # Keep the east side of U4 clear for the parallel GPIO/SPI fan-out.
    "R6": (102.0, 21.0, 0), "C13": (105.5, 21.5, 90),
    "R7": (120.0, 17.5, 90), "R8": (132.0, 17.5, 90),
    "D5": (145.0, 21.0, 270), "R9": (145.0, 17.5, 90),
    "R10": (91.0, 24.0, 0), "R11": (95.0, 25.0, 0),
    "C14": (99.0, 25.0, 0),
    # 3.3 V always-on buck.
    "C7": (94.0, 35.5, 90), "U3": (100.0, 35.5, 0),
    "C8": (99.5, 31.5, 0), "L2": (110.5, 35.5, 0),
    "C9": (116.0, 34.0, 90), "C10": (119.0, 37.0, 90),
    "D4": (122.0, 28.5, 0),
    # Battery input, reverse protection and 5 V logic buck.
    "J1": (87.5, 69.0, 0), "F1": (97.5, 62.0, 0),
    "D1": (103.5, 62.0, 90), "Q1": (108.0, 68.0, 0),
    "R1": (114.5, 59.0, 0), "D2": (110.0, 62.0, 90),
    "C1": (114.0, 68.0, 90), "C2": (116.5, 63.0, 90),
    "C3": (94.0, 53.5, 90), "U1": (100.0, 53.5, 0),
    "C4": (105.0, 49.0, 0), "L1": (110.5, 53.5, 0),
    "C5": (116.0, 51.0, 90), "C6": (119.0, 55.5, 90),
    "D3": (121.0, 45.5, 0),
    # Servo 5.98 V high-current supply and output.
    "R26": (124.0, 37.0, 0), "R27": (128.0, 37.0, 0),
    "C39": (122.0, 49.0, 90), "C40": (124.5, 44.0, 90),
    "U6": (128.0, 50.5, 0), "C41": (132.0, 45.5, 0),
    "L5": (137.0, 50.5, 0),
    "R28": (137.0, 42.0, 90), "R29": (143.0, 37.0, 90),
    "C45": (139.0, 38.0, 0),
    "C42": (143.0, 53.5, 90), "C43": (146.0, 57.0, 90),
    "C44": (136.0, 65.0, 0), "F2": (126.5, 69.0, 0),
    "R30": (136.0, 33.0, 0), "R31": (141.0, 33.0, 90),
    # At +90 degrees the three-pin header extends toward board +X.  Keep its
    # courtyard inside the 150 mm board edge while retaining edge access.
    "J4": (143.0, 44.0, 90),
}


# Test pads occupy otherwise unused service space.  Their DNP flag only means
# “not assembled”; the exposed copper is present on every PCB.
for index, xyz in enumerate(
    [
        # TP3 sits below/right of the first two test pads.  Keeping it on the
        # original y=22 row overlaps the rotated C11 1206 courtyard; (88, 24)
        # clears both C11 and R10 while retaining easy probe access.
        (83.0, 22.0, 0), (86.0, 22.0, 0), (88.0, 24.0, 0),
        (92.0, 29.0, 0), (96.0, 29.0, 0), (100.0, 29.0, 0),
        (84.0, 57.0, 0), (87.0, 57.0, 0), (90.0, 57.0, 0),
        (103.0, 29.0, 0), (106.0, 29.0, 0), (96.5, 23.0, 0),
    ],
    start=1,
):
    FIXED_PLACEMENT[f"TP{index}"] = xyz


def antenna_footprint_text() -> str:
    """Create the 40 x 40 mm, four-turn, 0.4/0.3 mm reference coil."""
    # Coordinates are relative to a terminal pair at x=0.  The coil occupies
    # x=-43..-3 and y=-20..20 when AE1 is placed at (48, 37.5).
    points = [
        (0.0, -2.5), (-3.0, -2.5), (-3.0, -20.0), (-43.0, -20.0),
        (-43.0, 20.0), (-3.7, 20.0), (-3.7, -19.3), (-42.3, -19.3),
        (-42.3, 19.3), (-4.4, 19.3), (-4.4, -18.6), (-41.6, -18.6),
        (-41.6, 18.6), (-5.1, 18.6), (-5.1, -17.9), (-40.9, -17.9),
        (-40.9, 17.9), (-5.8, 17.9),
    ]
    lines = []
    for start, end in zip(points, points[1:]):
        lines.append(
            f'  (fp_line (start {start[0]} {start[1]}) (end {end[0]} {end[1]}) '
            f'(stroke (width 0.4) (type solid)) (layer "F.Cu"))'
        )
    # B.Cu underpass connects the inner end back to terminal pad 2.
    lines.append(
        '  (fp_line (start -5.8 17.9) (end 0.0 2.5) '
        '(stroke (width 0.4) (type solid)) (layer "B.Cu"))'
    )
    return (
        '(footprint "NFC_Antenna_40x40_4T" (version 20221018) (generator pcbnew)\n'
        '  (layer "F.Cu")\n'
        '  (attr board_only exclude_from_pos_files exclude_from_bom)\n'
        '  (net_tie_pad_groups "1,2")\n'
        '  (fp_text reference "AE1" (at -23 -23 0) (layer "F.SilkS")\n'
        '    (effects (font (size 1 1) (thickness 0.15))))\n'
        '  (fp_text value "40x40mm 4-turn PCB antenna" (at -23 23 0) (layer "F.Fab") hide\n'
        '    (effects (font (size 1 1) (thickness 0.15))))\n'
        + "\n".join(lines)
        + '\n  (pad "1" smd rect (at 0 -2.5) (size 1.2 1.2) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
        '  (pad "2" thru_hole circle (at -5.8 17.9) (size 1.2 1.2) (drill 0.5) (layers "*.Cu" "*.Mask"))\n'
        '  (pad "2" thru_hole circle (at 0 2.5) (size 1.2 1.2) (drill 0.5) (layers "*.Cu" "*.Mask"))\n'
        ')\n'
    )


def footprint_bbox(footprint: Footprint, placement):
    """Axis-aligned board bbox including courtyard and pads."""
    xs, ys = [], []
    for pad in footprint.pads:
        xs.extend((pad.position.X - pad.size.X / 2, pad.position.X + pad.size.X / 2))
        ys.extend((pad.position.Y - pad.size.Y / 2, pad.position.Y + pad.size.Y / 2))
    for item in footprint.graphicItems:
        if getattr(item, "layer", "") not in ("F.CrtYd", "F.Fab"):
            continue
        if hasattr(item, "start") and hasattr(item, "end"):
            xs.extend((item.start.X, item.end.X))
            ys.extend((item.start.Y, item.end.Y))
    if not xs:
        xs, ys = [-1, 1], [-1, 1]
    x0, y0, angle = placement
    # KiCad board coordinates grow downward on Y, so a positive footprint
    # angle is clockwise in the Cartesian coordinates used by this script.
    theta = math.radians(-angle)
    transformed = []
    for x in (min(xs), max(xs)):
        for y in (min(ys), max(ys)):
            transformed.append(
                (
                    x0 + x * math.cos(theta) - y * math.sin(theta),
                    y0 + x * math.sin(theta) + y * math.cos(theta),
                )
            )
    return (
        min(point[0] for point in transformed), max(point[0] for point in transformed),
        min(point[1] for point in transformed), max(point[1] for point in transformed),
    )


def apply_reference(footprint: Footprint, reference: str, value: str) -> None:
    # Library footprints are parsed afresh for every component, but their child
    # UUIDs come from the source .kicad_mod.  Reusing those UUIDs across placed
    # instances makes KiCad identify remote pads/graphics as the same object and
    # can produce false short-circuit reports.  Give every placed child a fresh
    # identity, and preserve any footprint-group membership through the remap.
    uuid_remap = {}

    def renew_uuid(item, attribute="tstamp"):
        old_uuid = getattr(item, attribute, None)
        new_uuid = str(uuid4())
        if old_uuid not in (None, ""):
            uuid_remap[old_uuid] = new_uuid
        setattr(item, attribute, new_uuid)

    for item in footprint.graphicItems:
        if getattr(item, "type", None) == "reference":
            item.text = reference
            item.hide = False
            if reference in FAB_ONLY_REFERENCES:
                item.layer = "F.Fab"
        elif getattr(item, "type", None) == "value":
            item.text = value
            item.hide = True
        renew_uuid(item)
    for pad in footprint.pads:
        renew_uuid(pad)
    for zone in footprint.zones:
        renew_uuid(zone)
    for group in footprint.groups:
        renew_uuid(group, "id")
        group.members = [uuid_remap.get(member, member) for member in group.members]


def apply_absolute_child_rotation(footprint: Footprint, angle: float) -> None:
    """Write KiCad board-item angles for a rotated placed footprint.

    KiCad stores pad and text orientations in board coordinates even though
    their positions remain footprint-local.  The original generator wrote
    the library-local child angles unchanged, which made every rotated
    passive fail the native library-footprint comparison.  Batch 1 is kept
    deliberately narrow: only the 58 reviewed passive instances are updated.
    """

    def board_angle(local_angle):
        value = (float(local_angle or 0) + float(angle or 0)) % 360
        return 0 if abs(value) < 1e-9 else value

    for item in footprint.graphicItems:
        if type(item).__name__ == "FpText":
            item.position.angle = board_angle(item.position.angle)
    for pad in footprint.pads:
        pad.position.angle = board_angle(pad.position.angle)


def create_mounting_hole(reference: str, x: float, y: float) -> Footprint:
    source = (
        WORKSPACE_ROOT / ".tools" / "kicad-root" / "usr" / "share" / "kicad" /
        "footprints" / "MountingHole.pretty" / "MountingHole_3.2mm_M3.kicad_mod"
    )
    footprint = Footprint.from_file(str(source))
    footprint.libId = "HomeKey_RevA:MountingHole_3.2mm_M3"
    footprint.position = Position(x, y, 0)
    footprint.tstamp = str(uuid4())
    footprint.attributes.excludeFromBOM = True
    footprint.attributes.excludeFromPosFiles = True
    apply_reference(footprint, reference, "M3")
    # Corner mounting-hole references would sit above the y=0 board edge.
    # The holes are unambiguous mechanically and excluded from assembly, so
    # keep their references in the design data but hide them on production
    # silkscreen.
    for item in footprint.graphicItems:
        if getattr(item, "type", None) == "reference":
            item.hide = True
    return footprint


def board_text(text: str, x: float, y: float, size=1.0, layer="F.SilkS", angle=0):
    return GrText(
        text=text,
        position=Position(x, y, angle),
        layer=layer,
        effects=Effects(Font(height=size, width=size, thickness=0.15)),
        tstamp=str(uuid4()),
    )


def generate() -> None:
    if set(FIXED_PLACEMENT) != {part.ref for part in parts}:
        missing = sorted({part.ref for part in parts} - set(FIXED_PLACEMENT))
        extra = sorted(set(FIXED_PLACEMENT) - {part.ref for part in parts})
        raise ValueError(f"Placement map mismatch. missing={missing} extra={extra}")

    ANTENNA_FOOTPRINT.write_text(antenna_footprint_text(), encoding="utf-8")
    mounting_source = (
        WORKSPACE_ROOT / ".tools" / "kicad-root" / "usr" / "share" / "kicad" /
        "footprints" / "MountingHole.pretty" / "MountingHole_3.2mm_M3.kicad_mod"
    )
    (LIB_OUT / "MountingHole_3.2mm_M3.kicad_mod").write_text(
        mounting_source.read_text(encoding="utf-8"), encoding="utf-8"
    )

    board = Board.create_new()
    board.paper = PageSettings("A4")
    board.titleBlock = TitleBlock(
        title=f"{PROJECT_NAME} — integrated PN7161 / ESP32-C6 reader-actuator",
        date=DATE,
        revision=REVISION,
        company="DIY engineering prototype",
        comments={1: "4-layer, 1.6 mm; all components on top side", 2: "RF values require door-installed tuning"},
    )
    # Add two internal copper layers for a 4-layer JLCPCB build.
    board.layers.insert(1, LayerToken(ordinal=2, name="In1.Cu", type="power"))
    board.layers.insert(2, LayerToken(ordinal=4, name="In2.Cu", type="signal"))

    net_names = sorted({net for part in parts for net in part.pins.values()})
    net_by_name = {"": board.nets[0]}
    for number, name in enumerate(net_names, start=1):
        net = Net(number, name)
        board.nets.append(net)
        net_by_name[name] = net

    bboxes = {}
    for part in parts:
        if part.ref == "AE1":
            source = ANTENNA_FOOTPRINT
        elif part.ref in GRAPHICS_SYNC_REFS or part.ref == "U5":
            # These packages have reviewed project-local corrections.  U5
            # additionally carries NXP's SOT618-1 3 x 3 exposed-pad stencil.
            # Loading the upstream source here would silently reintroduce the
            # old definitions on a clean regeneration.
            source = LIB_OUT / f"{part.footprint.split(':', 1)[1]}.kicad_mod"
            if not source.exists():
                raise FileNotFoundError(
                    f"Missing canonical project footprint for {part.ref}: {source}"
                )
        else:
            source = source_footprint(part)
        footprint = Footprint.from_file(str(source))
        if part.ref == "U4":
            # Espressif's canonical ESP32-C6-MINI-1 footprint intentionally
            # has no legacy tedit field.  kiutils synthesizes one while
            # parsing, which makes KiCad report an otherwise identical placed
            # module as out of sync with the project library.
            footprint.tedit = None
        # kiutils serializes a one-entry wildcard zone as `(layer "*.Cu")`,
        # which KiCad 10 rejects as an undefined layer.  Expand the ESP module
        # antenna keepout to the actual Rev A copper stack before writing.
        for zone in footprint.zones:
            if zone.layers == ["*.Cu"]:
                zone.layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
        footprint.libId = footprint_for(part)
        x, y, angle = FIXED_PLACEMENT[part.ref]
        footprint.position = Position(x, y, angle)
        footprint.tstamp = str(uuid4())
        footprint.path = f"/generated/{part.ref}"
        footprint.attributes.excludeFromBOM = part.fields.get("ExcludeFromBOM") == "yes"
        footprint.attributes.excludeFromPosFiles = part.fields.get("ExcludeFromPosition") == "yes"
        apply_reference(footprint, part.ref, part.value)
        if part.ref in NATIVE_CHILD_ANGLE_REFS:
            apply_absolute_child_rotation(footprint, angle)
        if part.ref in {"F1", "F2"}:
            # The imported fuse footprint contains two degenerate three-point
            # arcs.  KiCad resolves their centre thousands of millimetres away
            # and reports false board-edge crossings.  The six straight outline
            # strokes already identify the body, so omit only those bad arcs.
            footprint.graphicItems = [
                item for item in footprint.graphicItems
                if not (
                    type(item).__name__ == "FpArc"
                    and getattr(item, "layer", None) == "F.SilkS"
                )
            ]
        if part.ref == "U4":
            # U4's antenna end is intentionally flush with the top PCB edge.
            # Remove the three decorative outline strokes lying exactly on
            # y=0; moving the RF module inward would worsen antenna clearance.
            footprint.graphicItems = [
                item for item in footprint.graphicItems
                if not (
                    type(item).__name__ == "FpLine"
                    and getattr(item, "layer", None) == "F.SilkS"
                    and min(item.start.Y, item.end.Y) <= -8.5 + 1e-6
                )
            ]
        if part.ref == "J2":
            # The two unnumbered 0.60 mm connector locating pegs are plastic
            # alignment holes, not plated electrical terminals.  The imported
            # EasyEDA footprint labels them as zero-annulus PTH pads, which is
            # both physically inaccurate and rejected by fabrication DRC.
            for pad in footprint.pads:
                if str(pad.number) == "":
                    pad.type = "np_thru_hole"
        if part.ref == "U5":
            # The imported PN7161 footprint contains a nearly full-circle
            # F.SilkS arc (start/end differ by only 0.15 mm).  It crosses the
            # exposed centre pad and several perimeter pads.  The eight corner
            # strokes and pin-1 marker already provide an unambiguous outline.
            footprint.graphicItems = [
                item for item in footprint.graphicItems
                if not (
                    type(item).__name__ == "FpArc"
                    and getattr(item, "layer", None) == "F.SilkS"
                )
            ]
        for pad in footprint.pads:
            number = str(pad.number)
            pad.net = net_by_name[part.pins[number]] if number in part.pins else net_by_name[""]
        board.footprints.append(footprint)
        bboxes[part.ref] = footprint_bbox(footprint, FIXED_PLACEMENT[part.ref])

    for reference, x, y, _drill, _diameter in BOARD_ONLY:
        footprint = create_mounting_hole(reference, x, y)
        board.footprints.append(footprint)
        bboxes[reference] = footprint_bbox(footprint, (x, y, 0))

    width, height = BOARD_SIZE
    outline_points = [(0, 0), (width, 0), (width, height), (0, height), (0, 0)]
    for start, end in zip(outline_points, outline_points[1:]):
        board.graphicItems.append(
            GrLine(
                start=Position(*start), end=Position(*end), layer="Edge.Cuts",
                width=0.1, tstamp=str(uuid4()),
            )
        )
    # Visual design zones; copper keep-outs are added by the routing stage.
    board.graphicItems.extend(
        [
            GrRect(
                start=Position(4.0, 16.0), end=Position(48.0, 59.0),
                layer="Dwgs.User", width=0.2, tstamp=str(uuid4()),
            ),
            board_text("NFC ANTENNA — NO METAL / NO COPPER", 26.0, 11.5, 1.0, "F.SilkS"),
            board_text("HomeKey Lock Rev A", 65.0, 72.5, 1.2, "F.SilkS"),
            board_text("PN7161 + ESP32-C6", 26.0, 62.0, 0.85, "F.SilkS"),
            board_text("BAT 3S", 87.5, 63.5, 0.8, "F.SilkS"),
            board_text("SERVO", 146.5, 40.0, 0.8, "F.SilkS", 90),
            board_text("USB LOGIC ONLY", 134.0, 10.7, 0.8, "F.SilkS"),
        ]
    )

    board.to_file(str(BOARD_PATH), encoding="utf-8")
    # U4 contains a footprint keepout and a custom centre pad.  KiCad 10's
    # native representation is required for its library comparator to treat
    # the placed module as identical; the reviewed snippet retains the same
    # 61 pads, 48 connected-pad nets, board-edge silk choice and four-layer
    # antenna keepout while remaining readable by the downstream audit tools.
    install_u4_native_block(BOARD_PATH)

    # Mechanical audit: no out-of-board or courtyard overlap is accepted.
    errors = []
    for reference, bbox in bboxes.items():
        if bbox[0] < -0.01 or bbox[1] > width + 0.01 or bbox[2] < -0.01 or bbox[3] > height + 0.01:
            errors.append(f"OUT OF BOARD {reference}: {bbox}")
    references = list(bboxes)
    for index, first in enumerate(references):
        a = bboxes[first]
        for second in references[index + 1:]:
            b = bboxes[second]
            if a[0] < b[1] + 0.15 and a[1] + 0.15 > b[0] and a[2] < b[3] + 0.15 and a[3] + 0.15 > b[2]:
                # The antenna is an intentional copper footprint surrounding
                # its own matching components only at the terminal edge.
                if "AE1" in (first, second):
                    other = second if first == "AE1" else first
                    if other in {"R22", "R23"}:
                        continue
                errors.append(f"COURTYARD OVERLAP {first} / {second}")
    print(f"Generated {BOARD_PATH.relative_to(PROJECT_ROOT)}")
    print(f"Footprints: {len(board.footprints)}, nets: {len(board.nets) - 1}")
    if errors:
        print("Placement audit warnings:")
        for error in errors:
            print(" -", error)
        raise SystemExit(2)
    print("Placement audit: PASS")


if __name__ == "__main__":
    generate()
