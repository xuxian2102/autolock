#!/usr/bin/env python3
"""Propose closer positions for passives that must sit at a specific IC pin.

Some passives only do their job if they are physically next to the pin they
serve: a switching regulator's feedback divider, an IC's bypass capacitors.
On Rev A several of them ended up 9-20 mm away, which is where the module
review found them.

This is not an autoplacer.  It moves a named handful of two-pad parts and
nothing else, and it does not decide whether the result is good -- the router
and the existing audit gate do.  The approach is the one KiCadRoutingTools'
placement work settled on (drandyhaas/KiCadRoutingTools#110): start from the
human placement, keep its intent, move a little, re-route, and accept only if
the board actually got better.

What this tool contributes is the "move a little" step, stated as intent
rather than as coordinates:

    "R28 belongs at the U6 pin its feedback net reaches"

not

    "R28 goes at (126.2, 49.76)"

so it re-derives the target from the netlist every run instead of going stale
when something moves.  It proposes; it writes nothing to the board.  The
numbers go into FIXED_PLACEMENT in generate_board.py by hand, which keeps the
generator the single source of truth for where parts sit.

Run it on the UNROUTED board (tools/generate_board.py output).  Copper is not
moved with the footprints, so a placement change is only meaningful before
routing.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy  # noqa: F401

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT.parent / ".tools" / "py"))

from audit_board import pad_geometry, reference_of  # noqa: E402
from design_data import BOARD_SIZE  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402
from route_board import FIXED_ESCAPES, FIXED_ESCAPE_PATHS  # noqa: E402
from shapely.geometry import LineString, Point, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402


REPORT = ROOT / "reports" / "PASSIVE_PLACEMENT.txt"
PROPOSAL = ROOT / "reports" / "PASSIVE_PLACEMENT.json"

# ref -> (IC, pin, why).  The pin is named, not left to proximity: each of
# these nets reaches three pins of its IC, and which one the part belongs to is
# a datasheet question, not a geometry one.  The pin numbers come from the
# per-part reviews against TI SLVSE71, NXP PN7160_PN7161 Rev 3.2 Table 5 and
# Diodes DS41326; the tool checks that the part's own net really does reach the
# named pin, so a wrong number fails loudly instead of quietly targeting the
# nearest pad that happens to share the net.
INTENTS = {
    "R28": ("U6", "4", "TPS565201 VFB divider, top leg"),
    "R29": ("U6", "4", "TPS565201 VFB divider, bottom leg"),
    # DNP feedforward across R28.  It belongs at the feedback node whether or
    # not it is populated: left behind, it stretches SERVO_FB back across the
    # board and the divider move buys almost nothing.
    "C45": ("U6", "4", "TPS565201 VFB feedforward"),
    "C17": ("U5", "13", "PN7161 VBAT/VDD(UP) rail bypass"),
    "C21": ("U5", "14", "PN7161 TVDD rail reservoir"),
    "C23": ("U5", "27", "PN7161 VDD rail bypass"),
    "C26": ("U5", "17", "PN7161 VMID bypass"),
    "C2": ("U1", "3", "AP63205 VIN bypass"),
}

# A bypass capacitor serves a *rail*, and on this package a rail arrives on
# several pins spread over more than one face.  The data sheet requires it:
#
#   TVDD  pins 14, 18 (west) and 22 (south)   -- TVDD_IN/TVDD_IN2 tie to VDD(TX)
#   VDD   pins 26, 27 (south) and 31 (east)   -- VDD(A) ties to VDD(D)
#   VBAT  pins 12, 13 (west) and 28 (south)   -- VBAT2 ties to VBAT
#   VMID  pin 17 only (west)
#
# The pin named in INTENTS is the one the module review measured against, and
# it stays as the declared intent.  But pinning the search to that one pin is
# what made this problem look unsolvable: U5's west face is full, and the first
# six routing runs all tried to squeeze capacitors into it.  Let the search
# reach any pin of the same rail and it can use the south and east faces, which
# are nearly empty -- pins 32-40 are n.c./i.c. and escape nowhere at all.
#
# The loop that matters is capacitor -> pin -> die, so the pin physically
# closest to the capacitor is the one doing the work.  TVDD is the one to watch:
# its reservoir wants to be at the input pins, which is what 18 and 22 are.
# Confirm against NXP AN13219 before this ships.
ALLOW_ANY_PIN_ON_RAIL = True

# What the router did with each proposal, 2026-08-31.  Recorded so nobody
# spends another six route runs rediscovering it:
#
#   C2  -> U1.3 VIN      18.00 -> 1.47 mm   ACCEPTED, board routes clean
#   C17 -> U5.13 VDD(UP)  8.77 -> 4.24 mm   rejected: SYS_5V, PN_NSS_IC fail
#   C21 -> U5.14 VDD(TX) 10.46 -> 3.03 mm   rejected: with C17, PN_TX2 fails
#   C23 -> U5.27 VDD      7.83 -> 3.24 mm   routes alone, but only reaches
#                                           5.50 mm once C21 stays put
#   R28 -> U6.4 VFB      13.31 -> 2.00 mm   rejected: SERVO_6V, 3V3, PN_NSS_IC
#   R29 -> U6.4 VFB      20.12 -> 1.42 mm   rejected: 3V3, PN_NSS_IC fail
#
# The U5 rejections are not congestion, they are commitment: U5's west face is
# already spoken for by its fixed pad escapes and by the TX/RX runs out to the
# matching network, and a bypass capacitor does not get to outrank either.  The
# U6 ones fail further away than they start -- moving R29 alone collapses 3V3
# from 11 routed edges to 0 -- which says the router's net order and via
# placement are globally coupled, not that R29's new spot is occupied.
#
# Both are fanout questions, not placement questions.  Answering them means
# replanning U5's escape pattern and U6's feedback corner, which is Rev B work.
#
# Round 1 of that Rev B exploration, 2026-09-01, four more routing runs:
#
#  * The first six runs were fighting a constraint this tool imposed, not one
#    the board has.  INTENTS named one pin per capacitor, and all three named
#    pins are on U5's west face.  The data sheet ties each rail across faces --
#    TVDD on 14/18 (W) and 22 (S), VDD on 26/27 (S) and 31 (E), VBAT on 12/13
#    (W) and 28 (S) -- so a bypass capacitor has a choice the tool was denying
#    it.  Allowing any pin of the rail immediately produced 2.49-2.83 mm
#    placements where the pinned version could not beat 4.24 mm.
#  * U5's east face is nearly free: pins 32-40 are n.c./i.c. and escape
#    nowhere.  That is where the room is.
#  * But the router is already using it.  PN_NSS_IC leaves U5 pin 1 heading
#    north and detours south-east to y = 38.75 first; a capacitor whose extent
#    began at y = 38.80 stopped it routing.  Nudging that same capacitor
#    0.25 mm from its original position routes fine, so these are real local
#    conflicts, not router chaos -- the accept/reject loop can be trusted.
#  * Reserve those lanes and the room disappears: best achievable becomes
#    6.43-6.55 mm for two of the four, and the other two cannot improve at all.
#
# So the capacitors cannot reach their pins until U5's escape pattern and the
# NSS/RX detours are planned together.  That is a fanout generator, not a
# placement search, and it is the actual content of Rev B issue #2 stage 1.

GRID = 0.25             # placement grid, mm
SEARCH_RADIUS = 8.0     # how far from the target pin to look
# generate_board.py's own mechanical audit rejects a placement when two part
# extents come within 0.15 mm.  Ask for a little more, so a proposal does not
# land exactly on its threshold and fail on a float comparison.
CLEARANCE = 0.20
EDGE_MARGIN = 0.50      # keep whole extents this far inside the outline
MIN_IMPROVEMENT = 0.50  # do not propose a move that gains less than this
# Width to reserve around a pad's escape line.  route_board.py refuses to route
# if anything sits on one, which is how the first version of this tool put two
# capacitors on top of U5's west-side fanout.
ESCAPE_WIDTH = 0.6

# Copper keep-outs the pours also honour.  A passive dropped in here would sit
# over an antenna.
KEEPOUTS = (box(0.0, 16.0, 48.5, 59.0), box(91.87, 0.0, 106.13, 5.91))

# XTAL2 leaves U5 through this corridor on F.Cu.  route_board.py still holds
# its path as fixed waypoints, so a part parked in the corridor would wall the
# crystal off.  Penalise rather than forbid: the router, not this tool, is the
# authority on whether a position actually breaks anything.
XTAL2_CORRIDOR = box(74.0, 44.0, 82.0, 54.0)
CORRIDOR_PENALTY = 25.0

# The PN7161 transmit, receive and antenna nets run from U5's west face out to
# the matching network.  They are short, single-ended and carry the reader's
# whole link budget, so a bypass capacitor does not get to push them around:
# reserve the straight run between their pads and place around it.
#
# This is not a guess about what the router will do -- it is what the router
# already did.  The first run of this tool put C17 and C21 across the
# U5.19 -> L4.1 line and PN_TX2 stopped routing.
RF_NETS = (
    "PN_TX1", "PN_TX2", "PN_RXN", "PN_RXP",
    "RF_P0", "RF_N0", "RF_P1", "RF_N1", "RF_P2", "RF_N2",
    "ANT_P", "ANT_N",
)
RF_WIDTH = 0.8

# Placement happens on the unrouted board, so the search cannot see where the
# router actually likes to run things.  It finds out the hard way: C23 was
# proposed at a spot whose extent began at y = 38.80, and PN_NSS_IC's route
# detours south-east to y = 38.75 before turning north.  0.05 mm apart, and the
# net stopped routing.
#
# Feed it the previous routed board as a soft obstacle: not a rule about where
# copper must go, just the observation that these lanes are in use, so prefer
# somewhere else.  Point it at the last board that routed clean.
# A snapshot of the last board that routed clean.  route_board.py is not the
# one that writes it -- keep it explicit, so planning is always against a board
# somebody decided was good, not against whatever ran last.
LANE_SOURCE = ROOT / "reports" / "ROUTED_REFERENCE.kicad_pcb"
LANE_WIDTH = 0.45


def extent(footprint):
    """Absolute extent of a placed footprint: pads plus courtyard and fab.

    Same definition generate_board.py's mechanical audit uses, so the two
    cannot disagree about what overlaps what.

    Rotation lives in one of two places depending on the part: generate_board
    bakes it into the children of everything in NATIVE_CHILD_ANGLE_REFS and
    leaves those footprints at angle zero, while every other footprint keeps a
    real angle over unrotated children.  Apply the footprint angle, which is
    zero for the baked ones -- reading the children raw got U6's box wrong by a
    quarter turn and put R28 inside it.
    """
    xs, ys = [], []
    for pad in footprint.pads:
        xs += [pad.position.X - pad.size.X / 2, pad.position.X + pad.size.X / 2]
        ys += [pad.position.Y - pad.size.Y / 2, pad.position.Y + pad.size.Y / 2]
    for item in footprint.graphicItems:
        if getattr(item, "layer", "") not in ("F.CrtYd", "F.Fab"):
            continue
        if hasattr(item, "start") and hasattr(item, "end"):
            xs += [item.start.X, item.end.X]
            ys += [item.start.Y, item.end.Y]
        elif hasattr(item, "center") and hasattr(item, "end"):
            radius = math.hypot(item.end.X - item.center.X, item.end.Y - item.center.Y)
            xs += [item.center.X - radius, item.center.X + radius]
            ys += [item.center.Y - radius, item.center.Y + radius]
    if not xs:
        return None
    corners = [
        rotate(x, y, footprint.position.angle or 0)
        for x in (min(xs), max(xs)) for y in (min(ys), max(ys))
    ]
    return box(
        footprint.position.X + min(c[0] for c in corners),
        footprint.position.Y + min(c[1] for c in corners),
        footprint.position.X + max(c[0] for c in corners),
        footprint.position.Y + max(c[1] for c in corners),
    )


def rf_reservations(board, fps):
    """Straight pad-to-pad runs of the RF nets, as no-go areas."""
    points = defaultdict(list)
    for footprint in board.footprints:
        for pad in footprint.pads:
            if pad.net is not None and pad.net.name in RF_NETS:
                centre = pad_geometry(footprint, pad).centroid
                points[pad.net.name].append((centre.x, centre.y))
    shapes = []
    for net, pads in points.items():
        if len(pads) < 2:
            continue
        # Minimum spanning tree over the pads: the shortest set of runs that
        # could connect them, which is what the router is trying to build.
        remaining = pads[1:]
        tree = [pads[0]]
        while remaining:
            a, b = min(
                ((t_, r) for t_ in tree for r in remaining),
                key=lambda pair: math.dist(*pair),
            )
            shapes.append(LineString([a, b]).buffer(RF_WIDTH / 2))
            tree.append(b)
            remaining.remove(b)
    return shapes


def lane_reservations(path):
    """Copper the previous successful route laid down, as places to avoid.

    Vias count, and ground vias count most of all.  The first version of this
    read only track segments and skipped GND outright, so it happily put C45's
    pad 0.025 mm from a 0.8 mm ground via -- a placement the geometry audit
    rejects, and one no amount of rerouting can fix, because the offending
    copper is the capacitor's own pad.
    """
    from kiutils.items.brditems import Segment, Via
    if not path.exists():
        print(
            f"note: {path.name} is missing, so the search is running blind to the\n"
            "      router's existing lanes.  Run tools/route_board.py once on a\n"
            "      board that routes clean and it will publish one.",
            file=sys.stderr,
        )
        return []
    board = Board.from_file(str(path))
    shapes = []
    for item in board.traceItems:
        if isinstance(item, Via):
            # A via is a hole and a pad, not a lane: nothing may sit on one,
            # whatever its net.
            shapes.append(
                Point(item.position.X, item.position.Y).buffer(
                    item.size / 2 + CLEARANCE, 12
                )
            )
            continue
        if not isinstance(item, Segment):
            continue
        if board.nets[item.net].name == "GND":
            continue        # the pour carries ground; its tracks are not lanes
        start = (item.start.X, item.start.Y)
        end = (item.end.X, item.end.Y)
        if start == end:
            continue
        shapes.append(LineString([start, end]).buffer(item.width / 2 + LANE_WIDTH, 4))
    return shapes


def escape_reservations(fps):
    """The pad escapes route_board.py refuses to have anything sitting on.

    Read from the router's own tables rather than restated here, so this stays
    true if an escape moves.  Each reservation is the run from the pad to its
    escape point, plus any continuation path the router pins after it.
    """
    shapes = []
    for (ref, number), point in FIXED_ESCAPES.items():
        footprint = fps.get(ref)
        if footprint is None:
            continue
        pad = next((p for p in footprint.pads if p.number == number), None)
        if pad is None:
            continue
        start = pad_geometry(footprint, pad).centroid
        run = [(start.x, start.y), tuple(point)]
        run += [tuple(step) for step in FIXED_ESCAPE_PATHS.get((ref, number), [])]
        shapes.append(LineString(run).buffer(ESCAPE_WIDTH / 2))
    for key, path in FIXED_ESCAPE_PATHS.items():
        if key not in FIXED_ESCAPES and len(path) > 1:
            shapes.append(LineString([tuple(step) for step in path]).buffer(ESCAPE_WIDTH / 2))
    return shapes


def pads_by_net(footprint):
    nets = defaultdict(list)
    for pad in footprint.pads:
        if pad.net is None or not pad.net.name:
            continue
        nets[pad.net.name].append(pad)
    return nets


def local_pad_offsets(footprint):
    """Each pad's offset from the footprint origin, in the footprint frame."""
    return {
        pad.number: (pad.position.X, pad.position.Y)
        for pad in footprint.pads
        if pad.net is not None and pad.net.name
    }


def rotate(x, y, angle):
    theta = math.radians(-angle)
    return x * math.cos(theta) - y * math.sin(theta), x * math.sin(theta) + y * math.cos(theta)


def target_for(part, ic, pin):
    """The IC pads this part may bypass at, and the part pad that reaches them.

    Returns every pad of the declared pin's net when ALLOW_ANY_PIN_ON_RAIL is
    set, so the search can pick the face with room rather than the face the
    review happened to measure.
    """
    ic_pad = next((p for p in ic.pads if p.number == pin), None)
    if ic_pad is None or ic_pad.net is None or not ic_pad.net.name:
        raise RuntimeError(f"{reference_of(ic)} pin {pin} carries no net")
    net = ic_pad.net.name
    part_pads = [p for p in part.pads if p.net is not None and p.net.name == net]
    if not part_pads:
        raise RuntimeError(
            f"{reference_of(part)} has no pad on {net}, the net of "
            f"{reference_of(ic)} pin {pin} -- the stated intent does not match the netlist"
        )
    if ALLOW_ANY_PIN_ON_RAIL:
        candidates = [p for p in ic.pads if p.net is not None and p.net.name == net]
    else:
        candidates = [ic_pad]
    targets = [(p.number, pad_geometry(ic, p).centroid) for p in candidates]
    return net, part_pads[0].number, targets


def main() -> None:
    board = Board.from_file(str(BOARD_PATH))
    if any(getattr(item, "layer", None) for item in board.traceItems):
        print(
            "WARNING: this board is routed.  Placement does not move copper, so "
            "run this on the unrouted board from generate_board.py.",
            file=sys.stderr,
        )

    # Placing a subset is the normal case, not an edge case: the router rejects
    # some of these moves, and the ones that survive have to be placed against
    # the ones that stay put.  Naming refs on the command line restricts the
    # run to them and leaves the rest as obstacles.
    wanted = [ref for ref in sys.argv[1:] if not ref.startswith("-")]
    intents = {ref: INTENTS[ref] for ref in wanted} if wanted else dict(INTENTS)
    unknown = [ref for ref in wanted if ref not in INTENTS]
    if unknown:
        raise SystemExit("no intent recorded for: " + ", ".join(unknown))

    fps = {reference_of(f): f for f in board.footprints}
    missing = [ref for ref in intents if ref not in fps]
    if missing:
        raise SystemExit("not on the board: " + ", ".join(missing))

    movers = set(intents)
    static = unary_union(
        [e for ref, f in fps.items() if ref not in movers and (e := extent(f))]
        + list(KEEPOUTS)
        + escape_reservations(fps)
        + rf_reservations(board, fps)
        + lane_reservations(LANE_SOURCE)
    )

    # Parts that serve the same IC compete for the same space, so solve them
    # together: place the one with the tightest requirement first, then let the
    # next see it as an obstacle.
    clusters = defaultdict(list)
    for ref, (ic, _pin, _why) in intents.items():
        clusters[ic].append(ref)

    proposals, rows, skipped = {}, [], []
    for ic_ref, refs in sorted(clusters.items()):
        ic = fps[ic_ref]
        taken = []
        wants = []
        for ref in refs:
            net, part_pad, targets = target_for(fps[ref], ic, intents[ref][1])
            here = pad_geometry(fps[ref], next(
                p for p in fps[ref].pads if p.number == part_pad)).centroid
            now = min(here.distance(t_) for _, t_ in targets)
            wants.append((now, ref, net, part_pad, targets))
        # Least freedom first, then worst off.  A rail that arrives on one pin
        # has nowhere else to go, so it picks before a rail with three -- order
        # it the other way and VMID, which only has pin 17, arrives to find its
        # own face taken and ends up further from its pin than it started.
        for now, ref, net, part_pad_no, targets in sorted(
            wants, key=lambda item: (len(item[4]), -item[0])
        ):
            f = fps[ref]
            here_box = extent(f)
            if here_box is None:
                raise SystemExit(f"{ref} has no extent to place against")
            x0, y0, x1, y1 = here_box.bounds
            w, h = x1 - x0, y1 - y0
            # A proposed angle is absolute, but the extent read off the board is
            # the one at the part's current angle, so the search has to turn it
            # by the difference.  Take that angle from the same board the extent
            # came from: reading it from FIXED_PLACEMENT instead means comparing
            # a fresh proposal against an already-edited table, which silently
            # transposes every 0603 and lands two of them inside U5.
            current_angle = f.position.angle or 0
            offsets = local_pad_offsets(f)
            px, py = offsets[part_pad_no]
            obstacles = unary_union([static] + taken) if taken else static

            best = None
            steps = int(SEARCH_RADIUS / GRID)
            # Every pin of the rail is a candidate anchor, so a capacitor
            # blocked out of a crowded face can take a clear one instead.
            for pin_no, target in targets:
                for angle in (0, 90, 180, 270):
                    # Dimensions: the extent read off the board is the one at
                    # the part's current angle, so turn it by the difference.
                    cw, ch = (w, h) if (angle - current_angle) % 180 == 0 else (h, w)
                    # Pad offset: pad.position is always stored unrotated -- the
                    # footprint angle is what places it -- so this one takes the
                    # proposed angle outright.  Using the difference here costs
                    # about a millimetre on every 0603 and quietly mis-ranks
                    # candidates.
                    dx, dy = rotate(px, py, angle)
                    for ix in range(-steps, steps + 1):
                        for iy in range(-steps, steps + 1):
                            cx = round(target.x + ix * GRID, 3)
                            cy = round(target.y + iy * GRID, 3)
                            # Inflate one side by the whole gap, not half: the
                            # obstacles are not inflated, so half on the
                            # candidate alone buys half the clearance required.
                            crt = box(cx - cw / 2 - CLEARANCE, cy - ch / 2 - CLEARANCE,
                                      cx + cw / 2 + CLEARANCE, cy + ch / 2 + CLEARANCE)
                            if (crt.bounds[0] < EDGE_MARGIN or crt.bounds[1] < EDGE_MARGIN
                                    or crt.bounds[2] > BOARD_SIZE[0] - EDGE_MARGIN
                                    or crt.bounds[3] > BOARD_SIZE[1] - EDGE_MARGIN):
                                continue
                            if crt.intersects(obstacles):
                                continue
                            cost = math.hypot(cx + dx - target.x, cy + dy - target.y)
                            if crt.intersects(XTAL2_CORRIDOR):
                                cost += CORRIDOR_PENALTY
                            if best is None or cost < best[0] - 1e-9:
                                best = (cost, cx, cy, angle, crt, pin_no)
            if best is None:
                raise SystemExit(
                    f"{ref}: no legal position within {SEARCH_RADIUS} mm of any "
                    f"{ic_ref} pin on {net}"
                )
            cost, cx, cy, angle, crt, ic_pad_no = best
            if cost > now - MIN_IMPROVEMENT:
                # Never propose a move that does not clearly help.  The search
                # returns the best legal spot near a pin, which is not the same
                # as an improvement on where the part already sits.
                skipped.append((ref, now, cost))
                taken.append(extent(f))
                continue
            taken.append(crt)
            proposals[ref] = {"at": [cx, cy, angle], "target": f"{ic_ref}.{ic_pad_no}",
                              "net": net, "was_mm": round(now, 2), "now_mm": round(cost, 2)}
            rows.append((ref, ic_ref, ic_pad_no, net, now, cost, cx, cy, angle))

    lines = [
        "PASSIVE PLACEMENT PROPOSAL",
        "=" * 72,
        f"Grid {GRID} mm, search radius {SEARCH_RADIUS} mm, courtyard gap {CLEARANCE} mm",
        "",
        f"{'part':5s} {'serves':10s} {'net':12s} {'was':>8s} {'now':>8s}   proposed pcb_at",
    ]
    for ref, ic_ref, ic_pad, net, now, cost, cx, cy, angle in sorted(rows):
        lines.append(
            f"{ref:5s} {ic_ref + '.' + ic_pad:10s} {net:12s} {now:7.2f}mm {cost:7.2f}mm   ({cx}, {cy}, {angle})"
        )
    if skipped:
        lines.append("")
        lines.append("LEFT ALONE (no position found that improves on the current one)")
        for ref, now, cost in sorted(skipped):
            lines.append(f"  {ref:5s} stays at {now:.2f} mm; best legal spot was {cost:.2f} mm")
    lines += [
        "",
        "Distances are pad-to-pad on the shared net, not part centre to part centre.",
        "",
        "This is a proposal.  Put the numbers in FIXED_PLACEMENT (generate_board.py),",
        "regenerate, route,",
        "and keep them only if the audit gate and KiCad DRC still pass -- placement",
        "score and routability are only loosely related, so the router decides.",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    PROPOSAL.write_text(json.dumps(proposals, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
