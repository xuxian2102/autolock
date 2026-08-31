#!/usr/bin/env python3
"""Render the deterministic Rev A Gerber/Excellon outputs for visual review.

This is intentionally a small reader for the RS-274X subset emitted by
export_manufacturing.py: metric absolute coordinates, linear interpolation,
flashes, straight traces, and filled regions.  It reads the exported files,
not the KiCad board, so the resulting images independently exercise the
manufacturing package.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
HARDWARE = ROOT / "hardware"
GERBERS = HARDWARE / "production" / "gerbers"
ASSEMBLY = HARDWARE / "production" / "assembly"
OUT = ROOT / "reports" / "final-review"
PREFIX = "HomeKey-Lock-RevA-PN7161"
BOARD_W = 150.0
BOARD_H = 75.0
SCALE = 12
MARGIN = 36
CANVAS = (round(BOARD_W * SCALE + 2 * MARGIN), round(BOARD_H * SCALE + 2 * MARGIN))


@dataclass
class Aperture:
    shape: str
    width: float
    height: float
    angle: float = 0.0
    polygon: tuple[tuple[float, float], ...] = ()


@dataclass
class GerberData:
    apertures: dict[int, Aperture]
    flashes: list[tuple[float, float, int]]
    segments: list[tuple[float, float, float, float, int]]
    regions: list[list[tuple[float, float]]]


def font(size: int):
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


FONT = font(16)
SMALL = font(12)
TITLE = font(22)


def xy(point: tuple[float, float], scale: float = SCALE, margin: float = MARGIN):
    x, y = point
    return (round(margin + x * scale), round(margin + (BOARD_H - y) * scale))


def rotate(point: tuple[float, float], degrees: float):
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    x, y = point
    return (x * cosine - y * sine, x * sine + y * cosine)


def parse_macros(text: str):
    macros: dict[str, tuple[tuple[float, float], ...]] = {}
    for match in re.finditer(r"%AM(UserPolygon_\d+)\*(.*?)\n%", text, re.S):
        name, body = match.groups()
        values = re.findall(r"-?\d+(?:\.\d+)?", body)
        if len(values) < 8:
            continue
        # The emitted primitive is 4,1,N followed by N x/y pairs and $1.
        count = int(values[2])
        coordinates = tuple(
            (float(values[3 + index * 2]), float(values[4 + index * 2]))
            for index in range(count)
        )
        macros[name] = coordinates
    return macros


def parse_gerber(path: Path):
    text = path.read_text(encoding="ascii")
    macros = parse_macros(text)
    apertures: dict[int, Aperture] = {}
    pending_shape: list[str] | None = None
    for line in text.splitlines():
        shape_match = re.search(r"TAShape,([^*]+)\*", line)
        if shape_match:
            pending_shape = shape_match.group(1).split(",")
            continue
        add = re.match(r"%ADD(\d+)([^,*]+)(?:,([^*]+))?\*%", line)
        if not add:
            continue
        code, raw_shape, raw_args = add.groups()
        args = [float(value) for value in (raw_args or "").split("X") if value]
        shape = raw_shape
        angle = 0.0
        polygon: tuple[tuple[float, float], ...] = ()
        if pending_shape:
            shape = pending_shape[0]
            numbers = [float(value) for value in pending_shape[1:] if re.fullmatch(r"-?\d+(?:\.\d+)?", value)]
        else:
            numbers = []
        if shape == "Circle" or raw_shape == "C":
            width = height = numbers[0] if numbers else args[0]
            shape = "circle"
        elif shape == "Rectangle" or raw_shape == "R":
            width = numbers[0] if len(numbers) >= 2 else args[0] * (2 if raw_shape == "Rectangle" else 1)
            height = numbers[1] if len(numbers) >= 2 else args[1] * (2 if raw_shape == "Rectangle" else 1)
            angle = numbers[-1] if len(numbers) >= 3 else (args[-1] if len(args) >= 3 else 0.0)
            shape = "rect"
        elif shape == "RoundedRectangle":
            width, height = numbers[:2]
            angle = numbers[-1]
            shape = "roundrect"
        elif raw_shape == "O":
            width, height = args[:2]
            shape = "oval"
        elif shape == "UserPolygon":
            polygon = macros.get(raw_shape, ())
            angle = numbers[-1] if numbers else (args[-1] if args else 0.0)
            if polygon:
                xs, ys = zip(*polygon)
                width, height = max(xs) - min(xs), max(ys) - min(ys)
            else:
                width = height = 0.6
            shape = "polygon"
        else:
            width = args[0] if args else 0.2
            height = args[1] if len(args) > 1 else width
            shape = "rect"
        apertures[int(code)] = Aperture(shape, width, height, angle, polygon)
        pending_shape = None

    flashes: list[tuple[float, float, int]] = []
    segments: list[tuple[float, float, float, float, int]] = []
    regions: list[list[tuple[float, float]]] = []
    current_aperture = 0
    current = (0.0, 0.0)
    active_region: list[tuple[float, float]] | None = None
    for line in text.splitlines():
        selected = re.fullmatch(r"D(\d+)\*", line)
        if selected and int(selected.group(1)) >= 10:
            current_aperture = int(selected.group(1))
            continue
        if line == "G36*":
            active_region = []
            continue
        if line == "G37*":
            if active_region and len(active_region) >= 3:
                regions.append(active_region)
            active_region = None
            continue
        command = re.fullmatch(r"(?:G0?1\*)?X(-?\d+)Y(-?\d+)D0([123])\*", line)
        if not command:
            continue
        target = (int(command.group(1)) / 1_000_000, int(command.group(2)) / 1_000_000)
        operation = int(command.group(3))
        if operation == 1:
            if active_region is not None:
                if not active_region:
                    active_region.append(current)
                active_region.append(target)
            else:
                segments.append((*current, *target, current_aperture))
        elif operation == 2 and active_region is not None:
            active_region.append(target)
        elif operation == 3:
            flashes.append((*target, current_aperture))
        current = target
    return GerberData(apertures, flashes, segments, regions)


def aperture_polygon(aperture: Aperture, center: tuple[float, float]):
    cx, cy = center
    if aperture.shape == "polygon" and aperture.polygon:
        points = aperture.polygon
    else:
        half_x, half_y = aperture.width / 2, aperture.height / 2
        points = ((-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y))
    return [(cx + point[0], cy + point[1]) for point in (rotate(p, aperture.angle) for p in points)]


def draw_flash(draw: ImageDraw.ImageDraw, aperture: Aperture, center, color, scale=SCALE, margin=MARGIN):
    cx, cy = center
    if aperture.shape == "circle":
        radius = aperture.width / 2
        left, top = xy((cx - radius, cy + radius), scale, margin)
        right, bottom = xy((cx + radius, cy - radius), scale, margin)
        draw.ellipse((left, top, right, bottom), fill=color)
        return
    points = [xy(point, scale, margin) for point in aperture_polygon(aperture, center)]
    if aperture.shape in {"oval", "roundrect"}:
        # A rotated rounded rectangle is adequate for the emitted oval/roundrect subset.
        draw.polygon(points, fill=color)
        radius = min(aperture.width, aperture.height) * scale / 3
        for px, py in points:
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=color)
    else:
        draw.polygon(points, fill=color)


def render_layer(data: GerberData, color, background=(12, 18, 27, 255)):
    image = Image.new("RGBA", CANVAS, background)
    draw = ImageDraw.Draw(image, "RGBA")
    for region in data.regions:
        draw.polygon([xy(point) for point in region], fill=color)
    for x1, y1, x2, y2, aperture_id in data.segments:
        aperture = data.apertures.get(aperture_id, Aperture("circle", 0.15, 0.15))
        width = max(1, round(min(aperture.width, aperture.height) * SCALE))
        draw.line((xy((x1, y1)), xy((x2, y2))), fill=color, width=width)
    for x, y, aperture_id in data.flashes:
        draw_flash(draw, data.apertures[aperture_id], (x, y), color)
    return image


def parse_drill(path: Path):
    tools: dict[int, float] = {}
    hits: list[tuple[float, float, float]] = []
    slots: list[tuple[float, float, float, float, float]] = []
    selected = 0
    for line in path.read_text(encoding="ascii").splitlines():
        tool = re.fullmatch(r"T(\d+)C([0-9.]+)", line)
        if tool:
            tools[int(tool.group(1))] = float(tool.group(2))
            continue
        select = re.fullmatch(r"T(\d+)", line)
        if select:
            selected = int(select.group(1))
            continue
        slot = re.fullmatch(r"X([0-9.]+)Y([0-9.]+)G85X([0-9.]+)Y([0-9.]+)", line)
        if slot:
            slots.append((*map(float, slot.groups()), tools[selected]))
            continue
        hit = re.fullmatch(r"X([0-9.]+)Y([0-9.]+)", line)
        if hit:
            hits.append((float(hit.group(1)), float(hit.group(2)), tools[selected]))
    return hits, slots


def overlay_drills(image: Image.Image, pth_color=(8, 11, 16, 255), npth_color=(239, 68, 68, 255)):
    draw = ImageDraw.Draw(image, "RGBA")
    for filename, color in ((f"{PREFIX}-PTH.drl", pth_color), (f"{PREFIX}-NPTH.drl", npth_color)):
        hits, slots = parse_drill(GERBERS / filename)
        for x, y, diameter in hits:
            radius = max(1.5, diameter * SCALE / 2)
            cx, cy = xy((x, y))
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)
        for x1, y1, x2, y2, diameter in slots:
            draw.line((xy((x1, y1)), xy((x2, y2))), fill=color, width=max(2, round(diameter * SCALE)))


def label(image: Image.Image, title: str):
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width, 31), fill=(255, 255, 255, 238))
    draw.text((10, 4), title, font=FONT, fill=(20, 25, 35, 255))


def save_layer(name: str, data: GerberData, color):
    image = render_layer(data, color)
    overlay_drills(image)
    draw = ImageDraw.Draw(image)
    draw.rectangle((*xy((0, BOARD_H)), *xy((BOARD_W, 0))), outline=(148, 163, 184, 255), width=2)
    label(image, name)
    path = OUT / f"{name}.png"
    image.save(path)
    return path


def alpha_layer(data: GerberData, color):
    return render_layer(data, color, (0, 0, 0, 0))


def composite(title: str, layers: list[tuple[GerberData, tuple[int, int, int, int]]], drills=True):
    image = Image.new("RGBA", CANVAS, (20, 74, 50, 255))
    for data, color in layers:
        image.alpha_composite(alpha_layer(data, color))
    if drills:
        overlay_drills(image)
    draw = ImageDraw.Draw(image)
    draw.rectangle((*xy((0, BOARD_H)), *xy((BOARD_W, 0))), outline=(225, 232, 240, 255), width=2)
    label(image, title)
    return image


def make_montage(paths: list[Path]):
    thumbs = []
    for path in paths:
        image = Image.open(path).convert("RGB")
        image.thumbnail((780, 420))
        thumbs.append((path.stem, image.copy()))
    width, height = 1600, math.ceil(len(thumbs) / 2) * 470
    montage = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(montage)
    for index, (name, image) in enumerate(thumbs):
        x = 10 + (index % 2) * 795
        y = 38 + (index // 2) * 470
        draw.text((x, y - 28), name, font=FONT, fill="#111827")
        montage.paste(image, (x, y))
    montage.save(OUT / "all_layers_montage.png")


def critical_crops(top: Image.Image):
    crops = [
        ("U4 + J2", (88, 0, 150, 27)),
        ("U5 + RF network", (46, 24, 94, 56)),
        ("D5 status LED", (136, 12, 150, 28)),
        ("Power + servo", (88, 28, 150, 75)),
    ]
    canvas = Image.new("RGB", (1600, 1100), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (title, bounds) in enumerate(crops):
        x1, y1, x2, y2 = bounds
        pixel_box = (xy((x1, y2))[0], xy((x1, y2))[1], xy((x2, y1))[0], xy((x2, y1))[1])
        crop = top.crop(pixel_box).convert("RGB")
        crop.thumbnail((760, 470))
        x = 20 + (index % 2) * 790
        y = 50 + (index // 2) * 530
        draw.text((x, y - 32), title, font=TITLE, fill="#111827")
        canvas.paste(crop, (x, y))
    canvas.save(OUT / "critical_crops.png")


def cpl_map(top: Image.Image):
    image = top.convert("RGB")
    draw = ImageDraw.Draw(image)
    with (ASSEMBLY / "CPL_JLCPCB_DRAFT.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    critical = {"D1", "D2", "D3", "D4", "D5", "Q1", "U1", "U2", "U3", "U4", "U5", "U6", "J2", "X1"}
    for row in rows:
        x = float(row["Mid X"].removesuffix("mm"))
        y = float(row["Mid Y"].removesuffix("mm"))
        angle = float(row["Rotation"])
        cx, cy = xy((x, y))
        length = 13 if row["Designator"] in critical else 7
        theta = math.radians(angle)
        endpoint = (cx + math.cos(theta) * length, cy - math.sin(theta) * length)
        color = (239, 68, 68) if row["Designator"] in critical else (17, 24, 39)
        draw.line((cx, cy, *endpoint), fill=color, width=2)
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=color)
        if row["Designator"] in critical:
            draw.text((cx + 4, cy - 16), f"{row['Designator']} {angle:g}deg", font=SMALL, fill=color)
    label(image, f"CPL centroid/orientation overlay - {len(rows)} top-side placements")
    image.save(OUT / "cpl_orientation_overlay.png")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "F_Cu": "GTL", "In1_Cu": "G2", "In2_Cu": "G3", "B_Cu": "GBL",
        "F_Mask": "GTS", "B_Mask": "GBS", "F_Paste": "GTP", "B_Paste": "GBP",
        "F_Silk": "GTO", "B_Silk": "GBO", "Edge_Cuts": "GKO",
    }
    parsed = {name: parse_gerber(GERBERS / f"{PREFIX}.{suffix}") for name, suffix in files.items()}
    colors = {
        "F_Cu": (245, 158, 11, 255), "In1_Cu": (96, 165, 250, 255),
        "In2_Cu": (244, 114, 182, 255), "B_Cu": (167, 139, 250, 255),
        "F_Mask": (34, 197, 94, 230), "B_Mask": (22, 163, 74, 230),
        "F_Paste": (226, 232, 240, 255), "B_Paste": (148, 163, 184, 255),
        "F_Silk": (255, 255, 255, 255), "B_Silk": (226, 232, 240, 255),
        "Edge_Cuts": (248, 250, 252, 255),
    }
    layer_paths = [save_layer(name, parsed[name], colors[name]) for name in files]
    make_montage(layer_paths)

    top = composite(
        "Top fabrication composite (direct from Gerber + Excellon)",
        [(parsed["F_Cu"], (245, 158, 11, 215)), (parsed["F_Mask"], (16, 185, 129, 75)),
         (parsed["F_Paste"], (226, 232, 240, 220)), (parsed["F_Silk"], (255, 255, 255, 255))],
    )
    top.save(OUT / "top_fabrication_composite.png")
    bottom = composite(
        "Bottom fabrication composite (direct from Gerber + Excellon)",
        [(parsed["B_Cu"], (167, 139, 250, 215)), (parsed["B_Mask"], (16, 185, 129, 75)),
         (parsed["B_Paste"], (226, 232, 240, 220)), (parsed["B_Silk"], (255, 255, 255, 255))],
    )
    bottom.save(OUT / "bottom_fabrication_composite.png")
    critical_crops(top)
    cpl_map(top)

    drill = Image.new("RGBA", CANVAS, (248, 250, 252, 255))
    overlay_drills(drill, (30, 64, 175, 255), (220, 38, 38, 255))
    draw = ImageDraw.Draw(drill)
    draw.rectangle((*xy((0, BOARD_H)), *xy((BOARD_W, 0))), outline=(17, 24, 39, 255), width=2)
    label(drill, "Drill map - plated blue, non-plated red")
    drill.save(OUT / "drill_map.png")
    print(f"Rendered {len(layer_paths) + 6} review images in {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
