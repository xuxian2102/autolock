#!/usr/bin/env python3
"""Export Rev A manufacturing and assembly files without KiCad's GUI runtime.

The legacy bundled KiCad 7 CLI can parse its own version flag in this environment but
crashes while loading a PCB.  This exporter therefore reads the authoritative
KiCad board with kiutils and emits standards-compliant X2 Gerber, Excellon,
BOM, and CPL files.  audit_manufacturing.py cross-checks the results against
the source board before a release ZIP is created.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy  # noqa: F401


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
WORKSPACE = ROOT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKSPACE / ".tools" / "py"))

import gerber_writer as gw  # noqa: E402
from audit_board import absolute_point, reference_of, rotate_point  # noqa: E402
from design_data import (  # noqa: E402
    BOARD_SIZE,
    NATIVE_CHILD_ANGLE_REFS,
    PROJECT_NAME,
    REVISION,
    footprint_for,
    part_by_ref,
    parts,
)
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402
from kiutils.items.brditems import Segment, Via  # noqa: E402
from shapely.geometry import Polygon, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402


PRODUCTION = HARDWARE / "production"
GERBER_DIR = PRODUCTION / "gerbers"
ASSEMBLY_DIR = PRODUCTION / "assembly"
REPORT_DIR = ROOT / "reports"
MANIFEST_PATH = REPORT_DIR / "MANUFACTURING_EXPORT.json"
PREFIX = PROJECT_NAME

COPPER_SPECS = {
    "F.Cu": ("Copper,L1,Top,Signal", f"{PREFIX}.GTL"),
    "In1.Cu": ("Copper,L2,Inr,Plane", f"{PREFIX}.G2"),
    "In2.Cu": ("Copper,L3,Inr,Signal", f"{PREFIX}.G3"),
    "B.Cu": ("Copper,L4,Bot,Signal", f"{PREFIX}.GBL"),
}
OTHER_SPECS = {
    "F.Mask": ("Soldermask,Top", f"{PREFIX}.GTS"),
    "B.Mask": ("Soldermask,Bot", f"{PREFIX}.GBS"),
    "F.Paste": ("Paste,Top", f"{PREFIX}.GTP"),
    "B.Paste": ("Paste,Bot", f"{PREFIX}.GBP"),
    "F.SilkS": ("Legend,Top", f"{PREFIX}.GTO"),
    "B.SilkS": ("Legend,Bot", f"{PREFIX}.GBO"),
    "Edge.Cuts": ("Profile,NP", f"{PREFIX}.GKO"),
}


def layer_matches(pad, layer: str) -> bool:
    if layer in pad.layers:
        return True
    if layer.endswith(".Cu") and "*.Cu" in pad.layers:
        return True
    if layer.endswith(".Mask") and "*.Mask" in pad.layers:
        return True
    return False


def pad_position(footprint, pad):
    return absolute_point(footprint, float(pad.position.X), float(pad.position.Y))


def pad_angle(footprint, pad):
    # Reviewed footprint instances use KiCad's board-coordinate child angle.
    # Other footprints retain the legacy generator representation until their
    # own reviewed sync batches, so keep the old relative-angle fallback.
    if reference_of(footprint) in NATIVE_CHILD_ANGLE_REFS:
        return float(pad.position.angle or 0)
    return float((footprint.position.angle or 0) + (pad.position.angle or 0))


def _polygon_pieces(pad):
    pieces = []
    anchor = getattr(getattr(pad, "customPadOptions", None), "anchor", None)
    if anchor == "rect" or (float(pad.size.X) > 0.02 and float(pad.size.Y) > 0.02):
        pieces.append(
            box(
                -float(pad.size.X) / 2,
                -float(pad.size.Y) / 2,
                float(pad.size.X) / 2,
                float(pad.size.Y) / 2,
            )
        )
    for primitive in getattr(pad, "customPadPrimitives", []) or []:
        coordinates = getattr(primitive, "coordinates", None)
        if not coordinates:
            continue
        polygon = Polygon([(float(point.X), float(point.Y)) for point in coordinates])
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if not polygon.is_empty:
            # EasyEDA's USB-C polygons contain repeated vertices and sub-micron
            # jogs written in scientific notation.  Simplifying by 0.5 um is
            # far below PCB fabrication tolerance and keeps RS-274X aperture
            # macro parameters in portable fixed-decimal form.
            pieces.append(polygon.simplify(0.0005, preserve_topology=True))
    if not pieces:
        pieces.append(
            box(
                -float(pad.size.X) / 2,
                -float(pad.size.Y) / 2,
                float(pad.size.X) / 2,
                float(pad.size.Y) / 2,
            )
        )
    result = unary_union(pieces)
    return list(result.geoms) if result.geom_type == "MultiPolygon" else [result]


def add_pad(layer, footprint, pad, function: str):
    """Add one KiCad pad, including exact custom polygons, to a Gerber layer."""
    x_size, y_size = float(pad.size.X), float(pad.size.Y)
    position = pad_position(footprint, pad)
    angle = pad_angle(footprint, pad)
    if pad.shape == "circle" or (pad.shape == "oval" and abs(x_size - y_size) < 1e-6):
        layer.add_pad(gw.Circle(x_size, function), position, angle)
    elif pad.shape == "rect":
        layer.add_pad(gw.Rectangle(x_size, y_size, function), position, angle)
    elif pad.shape in {"oval", "roundrect", "rounded_rectangle"}:
        radius = min(x_size, y_size) / 2 if pad.shape == "oval" else min(x_size, y_size) * float(pad.roundrectRatio or 0.25)
        layer.add_pad(gw.RoundedRectangle(x_size, y_size, radius, function), position, angle)
    elif pad.shape == "custom":
        for polygon in _polygon_pieces(pad):
            coordinates = []
            for x, y in polygon.exterior.coords:
                point = tuple(0.0 if abs(value) < 0.0005 else round(float(value), 6) for value in (x, y))
                if not coordinates or point != coordinates[-1]:
                    coordinates.append(point)
            if coordinates[0] != coordinates[-1]:
                coordinates.append(coordinates[0])
            layer.add_pad(gw.UserPolygon(tuple(coordinates), function), position, angle)
    else:
        layer.add_pad(gw.Rectangle(x_size, y_size, function), position, angle)


def item_width(item, minimum=0.0):
    width = getattr(item, "width", None)
    if width is None and getattr(item, "stroke", None) is not None:
        width = item.stroke.width
    return max(float(width or 0.10), minimum)


def add_fp_line(layer, footprint, item, function, minimum_width=0.0):
    start = absolute_point(footprint, float(item.start.X), float(item.start.Y))
    end = absolute_point(footprint, float(item.end.X), float(item.end.Y))
    layer.add_trace_line(start, end, item_width(item, minimum_width), function)


def add_polyline_circle(layer, footprint, item, function, minimum_width=0.0):
    center = absolute_point(footprint, float(item.center.X), float(item.center.Y))
    edge = absolute_point(footprint, float(item.end.X), float(item.end.Y))
    radius = math.dist(center, edge)
    if radius < 0.04:
        # Imported polarity dots are often encoded as a 0.03 mm circle, which
        # is below normal silkscreen capability.  Render a visible 0.15 mm dot.
        layer.add_pad(gw.Circle(0.15, function), center)
        return
    points = [
        (center[0] + radius * math.cos(index * 2 * math.pi / 48),
         center[1] + radius * math.sin(index * 2 * math.pi / 48))
        for index in range(49)
    ]
    for start, end in zip(points, points[1:]):
        layer.add_trace_line(start, end, item_width(item, minimum_width), function)


def add_fp_polygon(layer, footprint, item, function, minimum_width=0.0):
    coordinates = getattr(item, "coordinates", None) or []
    points = [absolute_point(footprint, float(point.X), float(point.Y)) for point in coordinates]
    if len(points) < 2:
        return
    if getattr(item, "fill", None) in {"yes", "solid"} and len(points) >= 3:
        path = gw.Path()
        path.moveto(points[0])
        for point in points[1:]:
            path.lineto(point)
        path.lineto(points[0])
        layer.add_region(path, function)
    else:
        for start, end in zip(points, points[1:] + [points[0]]):
            layer.add_trace_line(start, end, item_width(item, minimum_width), function)


def write_gerber(path: Path, layer) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        layer.dump_gerber(handle)


def export_copper(board):
    records = {}
    for copper_layer, (file_function, filename) in COPPER_SPECS.items():
        output = gw.DataLayer(file_function)
        counts = Counter()
        for footprint in board.footprints:
            for pad in footprint.pads:
                if pad.type == "np_thru_hole" or not layer_matches(pad, copper_layer):
                    continue
                add_pad(output, footprint, pad, "ComponentPad" if pad.type == "thru_hole" else "SMDPad,CuDef")
                counts["pads"] += 1
            for item in footprint.graphicItems:
                if getattr(item, "layer", "") == copper_layer and hasattr(item, "start") and hasattr(item, "end"):
                    add_fp_line(output, footprint, item, "Conductor")
                    counts["footprint_lines"] += 1
        for item in board.traceItems:
            if isinstance(item, Segment) and item.layer == copper_layer:
                output.add_trace_line(
                    (float(item.start.X), float(item.start.Y)),
                    (float(item.end.X), float(item.end.Y)),
                    float(item.width), "Conductor",
                )
                counts["tracks"] += 1
            elif isinstance(item, Via):
                output.add_pad(gw.Circle(float(item.size), "ViaPad"), (float(item.position.X), float(item.position.Y)))
                counts["vias"] += 1
        path = GERBER_DIR / filename
        write_gerber(path, output)
        records[filename] = dict(counts)
    return records


def export_mask_and_paste(board):
    records = {}
    for source_layer in ("F.Mask", "B.Mask", "F.Paste", "B.Paste"):
        file_function, filename = OTHER_SPECS[source_layer]
        output = gw.DataLayer(file_function)
        count = 0
        for footprint in board.footprints:
            for pad in footprint.pads:
                if not layer_matches(pad, source_layer):
                    continue
                function = "ComponentPad" if pad.type in {"thru_hole", "np_thru_hole"} else "SMDPad,CuDef"
                add_pad(output, footprint, pad, function)
                count += 1
        path = GERBER_DIR / filename
        write_gerber(path, output)
        records[filename] = {"pad_openings": count}
    return records


def export_silkscreen(board):
    records = {}
    for source_layer in ("F.SilkS", "B.SilkS"):
        file_function, filename = OTHER_SPECS[source_layer]
        output = gw.DataLayer(file_function)
        counts = Counter()
        for footprint in board.footprints:
            for item in footprint.graphicItems:
                if getattr(item, "layer", "") != source_layer:
                    continue
                name = type(item).__name__
                if name == "FpLine":
                    add_fp_line(output, footprint, item, "Legend", minimum_width=0.15)
                    counts["lines"] += 1
                elif name == "FpCircle":
                    add_polyline_circle(output, footprint, item, "Legend", minimum_width=0.15)
                    counts["circles"] += 1
                elif name == "FpPoly":
                    add_fp_polygon(output, footprint, item, "Legend", minimum_width=0.15)
                    counts["polygons"] += 1
                # Text and legacy arcs remain in the KiCad source and assembly
                # drawing.  The fallback exporter deliberately omits them from
                # physical silk instead of attempting an unverified font/arc
                # conversion.
        path = GERBER_DIR / filename
        write_gerber(path, output)
        records[filename] = dict(counts)
    return records


def export_profile(board):
    file_function, filename = OTHER_SPECS["Edge.Cuts"]
    output = gw.DataLayer(file_function)
    count = 0
    for item in board.graphicItems:
        if getattr(item, "layer", "") == "Edge.Cuts" and hasattr(item, "start") and hasattr(item, "end"):
            output.add_trace_line(
                (float(item.start.X), float(item.start.Y)),
                (float(item.end.X), float(item.end.Y)),
                item_width(item, 0.10), "Profile",
            )
            count += 1
    path = GERBER_DIR / filename
    write_gerber(path, output)
    return {filename: {"profile_segments": count}}


def _drill_header(file_function: str, tools):
    lines = [
        "M48",
        f"; {PROJECT_NAME} Rev {REVISION}",
        "; FORMAT={-:-/ absolute / metric / decimal}",
        f"; #@! TF.GenerationSoftware,OpenAI,RevAExporter,1.0",
        f"; #@! TF.FileFunction,{file_function}",
        "FMAT,2",
        "METRIC",
    ]
    lines.extend(f"T{number:02d}C{diameter:.6f}" for diameter, number in tools.items())
    lines.extend(["%", "G90", "G05"])
    return lines


def export_drills(board):
    plated_round = []
    plated_slots = []
    nonplated_round = []
    nonplated_slots = []
    for item in board.traceItems:
        if isinstance(item, Via):
            plated_round.append((float(item.drill), float(item.position.X), float(item.position.Y)))
    for footprint in board.footprints:
        for pad in footprint.pads:
            if pad.drill is None:
                continue
            target_round = nonplated_round if pad.type == "np_thru_hole" else plated_round
            target_slots = nonplated_slots if pad.type == "np_thru_hole" else plated_slots
            center = pad_position(footprint, pad)
            diameter = float(pad.drill.diameter)
            if getattr(pad.drill, "oval", False) and pad.drill.width:
                major = float(pad.drill.width)
                half_run = max(0.0, (major - diameter) / 2)
                theta = math.radians(pad_angle(footprint, pad))
                # KiCad's second oval-drill dimension is along local +Y.
                dx, dy = -math.sin(theta) * half_run, math.cos(theta) * half_run
                target_slots.append((diameter, center[0] - dx, center[1] - dy, center[0] + dx, center[1] + dy))
            else:
                target_round.append((diameter, center[0], center[1]))

    def write_file(path, file_function, rounds, slots):
        diameters = sorted({round(item[0], 6) for item in rounds + slots})
        tools = {diameter: index + 1 for index, diameter in enumerate(diameters)}
        lines = _drill_header(file_function, tools)
        grouped_round = defaultdict(list)
        grouped_slot = defaultdict(list)
        for item in rounds:
            grouped_round[round(item[0], 6)].append(item)
        for item in slots:
            grouped_slot[round(item[0], 6)].append(item)
        for diameter in diameters:
            lines.append(f"T{tools[diameter]:02d}")
            for _, x, y in grouped_round[diameter]:
                lines.append(f"X{x:.6f}Y{y:.6f}")
            for _, x1, y1, x2, y2 in grouped_slot[diameter]:
                lines.append(f"X{x1:.6f}Y{y1:.6f}G85X{x2:.6f}Y{y2:.6f}")
        lines.append("M30")
        path.write_text("\n".join(lines) + "\n", encoding="ascii")
        return {"round_hits": len(rounds), "slots": len(slots), "tools": len(tools)}

    pth_name = f"{PREFIX}-PTH.drl"
    npth_name = f"{PREFIX}-NPTH.drl"
    return {
        pth_name: write_file(GERBER_DIR / pth_name, "Plated,1,4,PTH", plated_round, plated_slots),
        npth_name: write_file(GERBER_DIR / npth_name, "NonPlated,1,4,NPTH", nonplated_round, nonplated_slots),
    }


def export_bom_and_cpl(board):
    footprints = {reference_of(footprint): footprint for footprint in board.footprints}
    full_path = ASSEMBLY_DIR / "BOM_FULL.csv"
    draft_path = ASSEMBLY_DIR / "BOM_JLCPCB_DRAFT.csv"
    cpl_path = ASSEMBLY_DIR / "CPL_JLCPCB_DRAFT.csv"

    full_rows = []
    active = []
    for part in parts:
        excluded = part.dnp or part.fields.get("ExcludeFromBOM") == "yes"
        footprint = footprints[part.ref]
        has_smd = any(pad.type == "smd" and "F.Cu" in pad.layers for pad in footprint.pads)
        status = "DNP" if part.dnp else "BOARD_COPPER" if part.fields.get("ExcludeFromBOM") == "yes" else "READY" if part.lcsc else "LCSC_UNRESOLVED"
        assembly = "SMT" if has_smd else "MANUAL_THT"
        full_rows.append([
            part.ref, part.value, footprint_for(part), part.lcsc, part.mpn,
            part.manufacturer, status, assembly, part.note,
        ])
        if not excluded:
            active.append((part, footprint, has_smd))

    with full_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Designator", "Value", "Footprint", "LCSC", "MPN", "Manufacturer", "Status", "Assembly", "Note"])
        writer.writerows(full_rows)

    groups = defaultdict(list)
    for part, _footprint, _has_smd in active:
        groups[(part.value, footprint_for(part), part.lcsc)].append(part.ref)
    with draft_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (value, footprint, lcsc), refs in sorted(groups.items(), key=lambda item: item[1][0]):
            writer.writerow([value, ",".join(sorted(refs)), footprint.split(":")[-1], lcsc])

    cpl_count = 0
    with cpl_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for part, footprint, has_smd in active:
            if not has_smd:
                continue
            writer.writerow([
                part.ref,
                f"{float(footprint.position.X):.3f}mm",
                f"{float(footprint.position.Y):.3f}mm",
                "Top",
                f"{float(footprint.position.angle or 0) % 360:.1f}",
            ])
            cpl_count += 1

    unresolved = [part.ref for part, _fp, _smd in active if not part.lcsc]
    manual = [part.ref for part, _fp, has_smd in active if not has_smd]
    return {
        "full_parts": len(parts),
        "active_bom_refs": len(active),
        "jlc_bom_groups": len(groups),
        "cpl_refs": cpl_count,
        "unresolved_lcsc_refs": unresolved,
        "manual_tht_refs": manual,
    }


def main():
    GERBER_DIR.mkdir(parents=True, exist_ok=True)
    ASSEMBLY_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    for path in GERBER_DIR.iterdir():
        if path.is_file():
            path.unlink()
    gw.set_generation_software("OpenAI", "RevAExporter", "1.0")
    board = Board.from_file(str(BOARD_PATH))
    records = {}
    records.update(export_copper(board))
    records.update(export_mask_and_paste(board))
    records.update(export_silkscreen(board))
    records.update(export_profile(board))
    records.update(export_drills(board))
    assembly = export_bom_and_cpl(board)
    hashes = {}
    for path in sorted(GERBER_DIR.iterdir()):
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "project": PROJECT_NAME,
        "revision": REVISION,
        "board_mm": list(BOARD_SIZE),
        "source_board": str(BOARD_PATH.relative_to(ROOT)),
        "exporter": "tools/export_manufacturing.py",
        "gerber_and_drill": records,
        "assembly": assembly,
        "sha256": hashes,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(hashes)} fabrication files to {GERBER_DIR.relative_to(ROOT)}")
    print(f"CPL refs: {assembly['cpl_refs']}; unresolved LCSC refs: {len(assembly['unresolved_lcsc_refs'])}")


if __name__ == "__main__":
    main()
