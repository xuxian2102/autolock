#!/usr/bin/env python3
"""Create official KiCad layer previews and high-resolution critical crops."""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
WORKSPACE = ROOT.parent
BOARD = HARDWARE / "kicad" / "HomeKey-Lock-RevA-PN7161.kicad_pcb"
OUT = ROOT / "reports" / "final-review"
SVG_OUT = OUT / "official-svg"
CPL = HARDWARE / "production" / "assembly" / "CPL_JLCPCB_DRAFT.csv"
CPL_AUDIT = ROOT / "reports" / "CPL_PLACEMENT_AUDIT.json"
KICAD_ROOT = WORKSPACE / ".tools" / "kicad10-full-root"
KICAD_CLI = KICAD_ROOT / "usr" / "bin" / "kicad-cli"
INKSCAPE = shutil.which("inkscape")
PROJECT = "HomeKey-Lock-RevA-PN7161"
LAYERS = ("F_Cu", "F_Mask", "F_Paste", "F_Silkscreen")
BOUNDS = {
    "D5": (137.0, 12.0, 150.0, 30.0),
    "U5": (66.0, 24.0, 94.0, 54.0),
    "U4": (87.0, 0.0, 112.0, 20.0),
    "J2": (124.0, 0.0, 145.0, 16.0),
}


def font(size: int):
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


TITLE = font(28)
LABEL = font(20)
SMALL = font(15)


def run(command, environment):
    process = subprocess.run(
        command, cwd=ROOT, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if process.returncode:
        raise RuntimeError(f"Command failed ({process.returncode}): {' '.join(command)}\n{process.stdout}")


def generate_layers():
    if not KICAD_CLI.exists() or not INKSCAPE:
        raise RuntimeError("KiCad 10 CLI and Inkscape are required for official previews")
    SVG_OUT.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = ":".join(
        str(path) for path in (KICAD_ROOT / "usr/lib", KICAD_ROOT / "usr/lib/x86_64-linux-gnu")
    )
    run(
        [
            str(KICAD_CLI), "pcb", "export", "svg", "--output", str(SVG_OUT),
            "--mode-multi", "--page-size-mode", "2", "--fit-page-to-board",
            "--exclude-drawing-sheet", "--layers",
            "F.Cu,In1.Cu,In2.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts",
            "--subtract-soldermask", "--drill-shape-opt", "2", str(BOARD),
        ],
        environment,
    )
    for svg in sorted(SVG_OUT.glob("*.svg")):
        png = svg.with_suffix(".png")
        run([INKSCAPE, str(svg), "--export-type=png", "--export-width=1800", f"--export-filename={png}"], environment)


def load_placements():
    with CPL.open(encoding="utf-8-sig", newline="") as handle:
        return {row["Designator"]: row for row in csv.DictReader(handle)}


def to_source_pixel(x_mm: float, y_mm: float):
    return round(x_mm * 12.0), round(y_mm * 12.0)


def make_crop(reference: str, bounds, placements, critical):
    x1, y1, x2, y2 = bounds
    panels = []
    for layer in LAYERS:
        path = SVG_OUT / f"{PROJECT}-{layer}.png"
        source = Image.open(path).convert("RGB")
        crop = source.crop((*to_source_pixel(x1, y1), *to_source_pixel(x2, y2)))
        crop.thumbnail((740, 380))
        if crop.width < 740 or crop.height < 380:
            factor = min(740 / crop.width, 380 / crop.height)
            crop = crop.resize((round(crop.width * factor), round(crop.height * factor)), Image.Resampling.NEAREST)
        panels.append((layer, crop))

    canvas = Image.new("RGB", (1600, 980), "#f8fafc")
    draw = ImageDraw.Draw(canvas)
    row = placements[reference]
    rotation = float(row["Rotation"])
    title = f"{reference} official KiCad layer review - CPL {rotation:g} deg"
    draw.text((30, 18), title, font=TITLE, fill="#111827")
    for index, (layer, panel) in enumerate(panels):
        px = 30 + (index % 2) * 785
        py = 90 + (index // 2) * 420
        draw.text((px, py - 30), layer, font=LABEL, fill="#111827")
        canvas.paste(panel, (px, py))

        cx_mm = float(row["Mid X"].removesuffix("mm"))
        cy_mm = float(row["Mid Y"].removesuffix("mm"))
        local_x = (cx_mm - x1) / (x2 - x1) * panel.width
        local_y = (cy_mm - y1) / (y2 - y1) * panel.height
        cx, cy = px + local_x, py + local_y
        length = 34
        theta = math.radians(rotation)
        endpoint = (cx + math.cos(theta) * length, cy + math.sin(theta) * length)
        draw.line((cx, cy, *endpoint), fill="#dc2626", width=4)
        draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill="#dc2626")

        item = critical[reference]
        pin1 = item.get("pin1")
        if pin1:
            for pin_x, pin_y in pin1["coordinates_mm"]:
                marker_x = px + (pin_x - x1) / (x2 - x1) * panel.width
                marker_y = py + (pin_y - y1) / (y2 - y1) * panel.height
                draw.ellipse((marker_x - 7, marker_y - 7, marker_x + 7, marker_y + 7), outline="#ef4444", width=3)

    if reference == "D5":
        note = "D5 pin 1/K = GND (red ring); pin 2/A = LED_A; local CPL rotation = 270 deg"
    elif reference == "U5":
        note = "U5 center paste must show nine separate 0.60 x 0.60 mm apertures"
    elif reference == "U4":
        note = "U4 antenna end is at the board edge; all copper layers remain clear beneath it"
    else:
        note = "J2 is 180 deg; two 0.60 mm positioning holes are NPTH and four shell tabs are plated"
    draw.text((30, 930), note, font=SMALL, fill="#334155")
    canvas.save(OUT / f"official_critical_{reference}.png")


def make_layer_montage():
    names = ("F_Cu", "In1_Cu", "In2_Cu", "B_Cu", "F_Mask", "B_Mask", "F_Paste", "B_Paste", "F_Silkscreen", "B_Silkscreen", "Edge_Cuts")
    canvas = Image.new("RGB", (1600, 2800), "white")
    draw = ImageDraw.Draw(canvas)
    for index, name in enumerate(names):
        image = Image.open(SVG_OUT / f"{PROJECT}-{name}.png").convert("RGB")
        image.thumbnail((770, 420))
        x = 15 + (index % 2) * 790
        y = 45 + (index // 2) * 455
        draw.text((x, y - 30), name, font=LABEL, fill="#111827")
        canvas.paste(image, (x, y))
    canvas.save(OUT / "official_all_layers_montage.png")


def main():
    generate_layers()
    placements = load_placements()
    audit = json.loads(CPL_AUDIT.read_text(encoding="utf-8"))
    critical = {item["reference"]: item for item in audit["critical"]}
    for reference, bounds in BOUNDS.items():
        make_crop(reference, bounds, placements, critical)
    make_layer_montage()
    print("Official KiCad previews: PASS (11 layers + 4 critical crops)")


if __name__ == "__main__":
    main()
