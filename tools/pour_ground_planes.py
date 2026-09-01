#!/usr/bin/env python3
"""Replace the routed In1.Cu ground mesh with real KiCad ground pours.

route_board.py emits In1.Cu ground as several hundred copper tracks so that a
headless run produces a deterministic manufacturing image without depending on
a zone filler.  That works, but it costs the board what a plane is actually
for: In1.Cu came out 43% covered, B.Cu was bare, and the return path under
each switching regulator was a few narrow ribbons instead of copper.

KiCad 10 removes the reason for the workaround.  ``kicad-cli pcb drc
--refill-zones`` and ``kicad-cli pcb export gerbers --check-zones`` fill zones
as part of the command, so the board file can keep *unfilled* zones and every
tool in this repository still parses it with kiutils -- the filled polygons
only ever exist inside KiCad's own processes, and the fabrication image is
still produced by one deterministic command.

This tool therefore works at the s-expression level:

* deletes the In1.Cu GND track mesh,
* adds all-layer copper keep-outs over the NFC loop and the ESP32 module
  antenna so no pour can encroach on either,
* adds a GND pour on In1.Cu and on B.Cu,
* stitches every pour island that the fill would otherwise leave floating
  back to the ground system with a via.

The island stitching is the only part that needs to know what the fill
actually looks like, so it asks KiCad's own filler (pcbnew) on a throw-away
copy of the board.  Nothing pcbnew produces is written back -- pcbnew saves in
a net-reference syntax kiutils cannot read -- it is used purely as a
measurement.

Everything this tool adds is self-identifying: each zone and stitch via
carries a UUID derived from the tool's namespace and the object's own key, so
a second run recognises and replaces its own output instead of duplicating it.
"""

from __future__ import annotations

import math
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
WORKSPACE = ROOT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKSPACE / ".tools" / "py"))

from design_data import ANTENNA, BOARD_SIZE  # noqa: E402

BOARD_PATH = HARDWARE / "kicad" / "HomeKey-Lock-RevA-PN7161.kicad_pcb"
REPORT = ROOT / "reports" / "GROUND_POUR.txt"

# Fixed namespace, so every object this tool emits has a reproducible UUID and
# a rerun can tell its own output apart from the router's.
NAMESPACE = uuid.UUID("6f0a1c52-9a1b-5f7e-8a3d-0c9f2b7e4d10")

BOARD_W, BOARD_H = BOARD_SIZE
# Pour outline inset from the board edge.  0.30 mm keeps copper off the route
# path with margin over the 0.2 mm edge clearance the design rules ask for.
EDGE_INSET = 0.30

# Both antennas need copper-free space on every layer, not just the layer they
# live on: a plane under a loop antenna is a shorted turn.  The NFC keep-out
# is drawn 0.5 mm outside the loop's outer conductor, which reaches x = 48.0.
KEEPOUTS = {
    "NFC antenna keepout": ANTENNA.keepout,
    "ESP32 antenna keepout": (91.87, 0.00, 106.13, 5.91),
}
POUR_LAYERS = ("In1.Cu", "B.Cu")

# Stitch via geometry.  0.6/0.3 is the smallest via elsewhere on this board and
# is comfortably inside JLC's 4-layer capability (0.15 mm minimum drill).
VIA_PAD, VIA_DRILL = 0.6, 0.3
VIA_CLEARANCE = 0.22
# Below this a floating fill fragment is not worth a via; the fill drops it.
MIN_ISLAND_AREA = 1.0
# Gates on the result.  Nothing floats either way -- island removal sees to
# that -- so these guard against a pour that fragments instead of filling.
MIN_COVERAGE = 0.60
MAX_DROPPED_AREA = 25.0


def zone_uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"gnd-zone|{name}")


def stitch_uuid(x: float, y: float) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"gnd-stitch|{x:.3f}|{y:.3f}")


def rect(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"(xy {x1} {y1}) (xy {x0} {y1}) (xy {x0} {y0}) (xy {x1} {y0})"


def gnd_net(text: str) -> str:
    nets = dict(re.findall(r'\(net (\d+) "([^"]*)"\)', text))
    for number, name in nets.items():
        if name == "GND":
            return number
    raise RuntimeError("board declares no GND net")


def strip_previous(text: str) -> str:
    """Remove the zones and vias a previous run of this tool added."""
    for name in list(KEEPOUTS) + [f"GND pour {layer}" for layer in POUR_LAYERS]:
        pattern = re.compile(
            r'\n  \(zone\n(?:.*?\n)*?    \(uuid "' + str(zone_uuid(name))
            + r'"\)(?:.*?\n)*?  \)'
        )
        text = pattern.sub("", text)

    def is_ours(match: re.Match) -> bool:
        x, y, identifier = float(match["x"]), float(match["y"]), match["uuid"]
        return identifier == str(stitch_uuid(x, y))

    via = re.compile(
        r'^  \(via \(at (?P<x>[-\d.]+) (?P<y>[-\d.]+)\).*?'
        r'\(tstamp (?P<uuid>[0-9a-f-]+)\)\)\n',
        re.M,
    )
    return via.sub(lambda m: "" if is_ours(m) else m.group(0), text)


def strip_in1_ground_mesh(text: str) -> tuple[str, int]:
    """Delete the routed In1.Cu GND tracks the pour replaces."""
    net = gnd_net(text)
    mesh = re.compile(
        r'^\s*\(segment \(start [-\d.]+ [-\d.]+\) \(end [-\d.]+ [-\d.]+\) '
        r'\(width [\d.]+\) \(layer "In1\.Cu"\) \(net ' + net + r'\)[^\n]*\n',
        re.M,
    )
    return mesh.subn("", text)


def zone_blocks(net: str) -> list[str]:
    blocks = []
    for name, (x0, y0, x1, y1) in KEEPOUTS.items():
        blocks.append(
            f'''  (zone
    (layers "F.Cu" "B.Cu" "In1.Cu" "In2.Cu")
    (uuid "{zone_uuid(name)}")
    (name "{name}")
    (hatch edge 0.508)
    (connect_pads (clearance 0))
    (min_thickness 0.254)
    (keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))
    (placement (enabled no) (sheetname ""))
    (fill (thermal_gap 0.508) (thermal_bridge_width 0.508) (island_removal_mode 0))
    (polygon (pts {rect(x0, y0, x1, y1)}))
  )'''
        )
    for layer in POUR_LAYERS:
        name = f"GND pour {layer}"
        blocks.append(
            f'''  (zone
    (net {net})
    (net_name "GND")
    (layer "{layer}")
    (uuid "{zone_uuid(name)}")
    (name "{name}")
    (hatch edge 0.508)
    (connect_pads (clearance 0.25))
    (min_thickness 0.2)
    (filled_areas_thickness no)
    (fill yes (thermal_gap 0.25) (thermal_bridge_width 0.4) (island_removal_mode 0))
    (polygon (pts {rect(EDGE_INSET, EDGE_INSET, BOARD_W - EDGE_INSET, BOARD_H - EDGE_INSET)}))
  )'''
        )
    return blocks


def append_before_close(text: str, blocks: list[str]) -> str:
    close = text.rstrip().rfind("\n)")
    return text[:close] + "\n" + "\n".join(blocks) + text[close:]


# --- island analysis -------------------------------------------------------
# Everything below runs against a throw-away copy of the board and only ever
# reports coordinates back.

def _load_pcbnew():
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        for candidate in (
            WORKSPACE / ".tools" / "kicad10-full-root" / "usr/lib/python3/dist-packages",
            Path("/usr/lib/python3/dist-packages"),
        ):
            if (candidate / "pcbnew.py").exists():
                sys.path.append(str(candidate))
                break
    import pcbnew

    return pcbnew


def fill_board(board_text: str, keep_islands: bool):
    """Fill a throw-away copy of the board and measure the result.

    Returns the filled polygons per layer, the copper a stitch via would have
    to avoid, and every drill on the board.
    """
    pcbnew = _load_pcbnew()
    from shapely.geometry import Polygon

    with tempfile.TemporaryDirectory(prefix="ground-pour-") as name:
        probe = Path(name) / BOARD_PATH.name
        # island_removal_mode 0 discards floating fill, which is what ships;
        # mode 1 keeps it, which is what makes islands visible to measure.
        if keep_islands:
            board_text = board_text.replace(
                "(island_removal_mode 0)", "(island_removal_mode 1)"
            )
        probe.write_text(board_text, encoding="utf-8")
        board = pcbnew.LoadBoard(str(probe))
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())

        fills = {}
        for zone in board.Zones():
            if zone.GetIsRuleArea():
                continue
            for layer in zone.GetLayerSet().Seq():
                polys = zone.GetFilledPolysList(layer)
                shapes = []
                for index in range(polys.OutlineCount()):
                    outline = polys.Outline(index)
                    shapes.append(
                        Polygon(
                            [
                                (outline.GetPoint(i).x / 1e6, outline.GetPoint(i).y / 1e6)
                                for i in range(outline.PointCount())
                            ]
                        )
                    )
                fills[board.GetLayerName(layer)] = shapes

        obstacles, drills = _obstacle_map(board, pcbnew)
    return fills, obstacles, drills


def islands(board_text: str):
    """The fill fragments KiCad itself considers unconnected, per layer.

    Rather than re-deriving what counts as connected, fill the board twice and
    subtract: island_removal_mode 1 keeps every fragment, mode 0 keeps only the
    fragments that reach the zone's net.  Whatever the first has and the second
    does not is, by KiCad's own verdict, floating copper.
    """
    kept, obstacles, drills = fill_board(board_text, keep_islands=True)
    shipped, _, _ = fill_board(board_text, keep_islands=False)
    floating = {}
    for layer, shapes in kept.items():
        survivors = shipped.get(layer, [])
        loose = [
            shape
            for shape in shapes
            if not any(other.contains(shape.representative_point()) for other in survivors)
        ]
        if loose:
            floating[layer] = sorted(loose, key=lambda shape: -shape.area)
    return floating, obstacles, drills, kept, shipped


def _obstacle_map(board, pcbnew):
    """Copper that a stitch via must stay clear of, per layer, plus drills."""
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union

    layers = {
        "F.Cu": pcbnew.F_Cu,
        "In1.Cu": pcbnew.In1_Cu,
        "In2.Cu": pcbnew.In2_Cu,
        "B.Cu": pcbnew.B_Cu,
    }
    obstacles = {name: [] for name in layers}
    drills = []

    for track in board.GetTracks():
        if isinstance(track, pcbnew.PCB_VIA):
            centre = (track.GetPosition().x / 1e6, track.GetPosition().y / 1e6)
            drills.append(Point(centre).buffer(track.GetDrill() / 1e6 / 2 + 0.15, 16))
            if track.GetNetname() != "GND":
                shape = Point(centre).buffer(track.GetWidth() / 1e6 / 2, 16)
                for name in obstacles:
                    obstacles[name].append(shape)
            continue
        if track.GetNetname() == "GND":
            continue
        name = board.GetLayerName(track.GetLayer())
        if name not in obstacles:
            continue
        start = (track.GetStart().x / 1e6, track.GetStart().y / 1e6)
        end = (track.GetEnd().x / 1e6, track.GetEnd().y / 1e6)
        if start == end:
            continue
        obstacles[name].append(
            LineString([start, end]).buffer(track.GetWidth() / 1e6 / 2, 8)
        )

    for footprint in board.GetFootprints():
        for pad in footprint.Pads():
            centre = (pad.GetPosition().x / 1e6, pad.GetPosition().y / 1e6)
            if pad.GetDrillSizeX() > 0:
                radius = max(pad.GetDrillSizeX(), pad.GetDrillSizeY()) / 1e6 / 2
                drills.append(Point(centre).buffer(radius + 0.15, 16))
            if pad.GetNetname() == "GND":
                continue
            # A circle around the pad's diagonal is conservative and keeps this
            # independent of pad shape and rotation.
            reach = math.hypot(pad.GetSizeX() / 1e6, pad.GetSizeY() / 1e6) / 2
            shape = Point(centre).buffer(reach, 12)
            for layer in pad.GetLayerSet().Seq():
                name = board.GetLayerName(layer)
                if name in obstacles:
                    obstacles[name].append(shape)

    blocked = {}
    for name, shapes in obstacles.items():
        if shapes:
            blocked[name] = unary_union(
                [shape.buffer(VIA_CLEARANCE + VIA_PAD / 2) for shape in shapes]
            )
    return blocked, unary_union(drills) if drills else None


def stitch_points(board_text: str) -> tuple[list[tuple[float, float]], list[float]]:
    """Where to put a via so each floating pour fragment joins the ground net."""
    from shapely.geometry import MultiPolygon

    floating, blocked, drills, _, _ = islands(board_text)
    placed, orphaned = [], []
    for shapes in floating.values():
        for shape in shapes:
            if shape.area < MIN_ISLAND_AREA:
                continue
            room = shape.buffer(-(VIA_PAD / 2 + 0.05))
            for region in blocked.values():
                room = room.difference(region)
            if drills is not None:
                room = room.difference(drills)
            if room.is_empty:
                orphaned.append(shape.area)
                continue
            parts = list(room.geoms) if isinstance(room, MultiPolygon) else [room]
            spot = max(parts, key=lambda part: part.area).representative_point()
            placed.append((round(spot.x, 3), round(spot.y, 3)))
    return placed, orphaned


def via_blocks(points, net: str) -> list[str]:
    return [
        f'  (via (at {x} {y}) (size {VIA_PAD}) (drill {VIA_DRILL}) '
        f'(layers "F.Cu" "B.Cu") (net {net}) (tstamp {stitch_uuid(x, y)}))'
        for x, y in points
    ]


def main() -> None:
    text = BOARD_PATH.read_text(encoding="utf-8")
    text = strip_previous(text)
    text, removed = strip_in1_ground_mesh(text)
    net = gnd_net(text)
    text = append_before_close(text, zone_blocks(net))

    points, orphaned = stitch_points(text)
    if points:
        text = append_before_close(text, via_blocks(points, net))

    # Measure the board that actually ships: vias in place, and whatever the
    # fill still cannot tie to ground dropped by island removal.
    leftover, _, _, _, shipped = islands(text)
    coverage = {name: sum(shape.area for shape in shapes) for name, shapes in shipped.items()}
    dropped = {
        name: [round(shape.area, 2) for shape in shapes]
        for name, shapes in leftover.items()
    }

    BOARD_PATH.write_text(text, encoding="utf-8")

    lines = [
        "GROUND POUR",
        "=" * 72,
        f"In1.Cu ground mesh tracks removed: {removed}",
        f"Copper keep-outs added: {len(KEEPOUTS)} (all four layers)",
        f"Ground pours added: {', '.join(POUR_LAYERS)}",
        f"Stitching vias added: {len(points)}",
        "",
        "POUR COVERAGE (of the 150 x 75 mm board)",
    ]
    for name in POUR_LAYERS:
        area = coverage.get(name, 0.0)
        lines.append(f" - {name}: {area:8.1f} mm^2  ({area / (BOARD_W * BOARD_H) * 100:.1f}%)")
    lines.append("")
    if points:
        lines.append("STITCHING VIAS")
        for x, y in points:
            lines.append(f" - {x:8.3f}, {y:8.3f}")
        lines.append("")
    if dropped:
        lines.append("FILL FRAGMENTS DROPPED BY ISLAND REMOVAL")
        for name, areas in sorted(dropped.items()):
            lines.append(
                f" - {name}: {len(areas)} fragment(s), {sum(areas):.2f} mm^2 total "
                f"(largest {max(areas):.2f} mm^2)"
            )
        lines.append(
            "   These are too small, or too hemmed in by other nets, to take a"
            " stitching via; island removal deletes them so no floating copper"
            " ships."
        )
        lines.append("")
    if orphaned:
        lines.append(
            "Fragments above the "
            f"{MIN_ISLAND_AREA:.1f} mm^2 stitching threshold with no room for a via: "
            + ", ".join(f"{area:.2f} mm^2" for area in orphaned)
        )
        lines.append("")
    lines.append(
        "Zones ship unfilled.  kicad-cli fills them itself: run_official_drc.py "
        "passes --refill-zones and export_official_fabrication.py passes "
        "--check-zones, so the manufacturing image and the DRC both see the "
        "same fill without the board file leaving kiutils-readable syntax."
    )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:8]))

    # Island removal guarantees nothing floats, so the gate is on how much the
    # pour actually achieved: a pour that fragments badly would show up here as
    # a large loss or as thin coverage, not as a DRC violation.
    for name in POUR_LAYERS:
        if coverage.get(name, 0.0) < MIN_COVERAGE * BOARD_W * BOARD_H:
            raise SystemExit(
                f"{name} pour covers only "
                f"{coverage.get(name, 0.0) / (BOARD_W * BOARD_H) * 100:.1f}% of the board"
            )
    total_dropped = sum(sum(areas) for areas in dropped.values())
    if total_dropped > MAX_DROPPED_AREA:
        raise SystemExit(
            f"island removal would delete {total_dropped:.1f} mm^2 of copper; "
            "the pour is fragmenting rather than filling"
        )
    print("Ground pour: PASS")


if __name__ == "__main__":
    main()
