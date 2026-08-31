#!/usr/bin/env python3
"""Render the routed board and top assembly drawing for visual QA."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy  # noqa: F401


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKSPACE / ".tools" / "py"))

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon, Rectangle  # noqa: E402
from audit_board import build_copper, reference_of  # noqa: E402
from design_data import BOARD_SIZE, PROJECT_NAME, part_by_ref  # noqa: E402
from generate_board import BOARD_PATH  # noqa: E402
from kiutils.board import Board  # noqa: E402
from shapely.ops import unary_union  # noqa: E402


OUT = ROOT / "reports" / "layers"
COLORS = {
    "GND": "#61a5c2",
    "BAT_RAW": "#f72585", "BAT_FUSED": "#f72585", "BAT_SYS": "#f72585",
    "SERVO_6V": "#ff9f1c", "SERVO_6V_OUT": "#ff9f1c", "SW_SERVO": "#ff9f1c",
    "3V3": "#8ac926", "SYS_5V": "#ffca3a", "5V_BAT": "#ffca3a",
    "ANT_P": "#9b5de5", "ANT_N": "#9b5de5", "#AE1_NET_TIE": "#9b5de5",
}


def draw_geometry(ax, geometry, color, alpha=0.85):
    geometries = list(geometry.geoms) if geometry.geom_type in {"MultiPolygon", "GeometryCollection"} else [geometry]
    for item in geometries:
        if item.geom_type != "Polygon" or item.is_empty:
            continue
        ax.add_patch(MplPolygon(list(item.exterior.coords), closed=True, facecolor=color, edgecolor="none", alpha=alpha))
        for interior in item.interiors:
            ax.add_patch(MplPolygon(list(interior.coords), closed=True, facecolor="#111827", edgecolor="none"))


def setup_axis(ax, title):
    ax.set_facecolor("#111827")
    ax.add_patch(Rectangle((0, 0), BOARD_SIZE[0], BOARD_SIZE[1], fill=False, edgecolor="#e5e7eb", linewidth=0.7))
    ax.set_xlim(-1, BOARD_SIZE[0] + 1)
    ax.set_ylim(BOARD_SIZE[1] + 1, -1)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=10, color="#111827")
    ax.set_xticks([])
    ax.set_yticks([])


def draw_top_crop(board, unions, bounds, filename, title):
    x0, x1, y0, y1 = bounds
    fig, ax = plt.subplots(figsize=(11, 7), constrained_layout=True)
    ax.set_facecolor("#111827")
    for (layer, net), geometry in unions.items():
        if layer == "F.Cu":
            draw_geometry(ax, geometry, COLORS.get(net, "#d1d5db"), alpha=0.72)
    for footprint in board.footprints:
        reference = reference_of(footprint)
        x, y = float(footprint.position.X), float(footprint.position.Y)
        if not reference or reference == "AE1" or not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        color = "#ef4444" if reference.startswith("H") or part_by_ref(reference).dnp else "#f8fafc"
        ax.text(x, y, reference, fontsize=7, ha="center", va="center", color=color, weight="bold")
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title, fontsize=12)
    fig.savefig(OUT / filename, dpi=240, facecolor="white")
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    board = Board.from_file(str(BOARD_PATH))
    copper, _counts = build_copper(board)
    unions = {key: unary_union(items) for key, items in copper.items()}

    layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    fig, axes = plt.subplots(2, 2, figsize=(15, 8), constrained_layout=True)
    for ax, layer in zip(axes.flat, layers):
        setup_axis(ax, layer)
        for (candidate, net), geometry in unions.items():
            if candidate != layer:
                continue
            color = COLORS.get(net, "#f1faee" if not net.startswith("#NC") else "#6b7280")
            draw_geometry(ax, geometry, color)
        if layer == "F.Cu":
            ax.add_patch(Rectangle((4, 16), 43.1, 43, fill=False, edgecolor="#ef4444", linewidth=0.8, linestyle="--"))
            ax.add_patch(Rectangle((92.1, 0), 13.8, 5.7, fill=False, edgecolor="#ef4444", linewidth=0.8, linestyle="--"))
    fig.suptitle(f"{PROJECT_NAME} — routed copper visual audit", fontsize=14)
    fig.savefig(OUT / "copper_layers.png", dpi=220, facecolor="white")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(15, 7.5), constrained_layout=True)
    setup_axis(ax, "Top assembly / component reference")
    for (layer, net), geometry in unions.items():
        if layer == "F.Cu":
            draw_geometry(ax, geometry, COLORS.get(net, "#d1d5db"), alpha=0.50)
    for footprint in board.footprints:
        reference = reference_of(footprint)
        if not reference or reference.startswith("H") or reference == "AE1":
            continue
        x, y = float(footprint.position.X), float(footprint.position.Y)
        color = "#dc2626" if part_by_ref(reference).dnp else "#111827"
        ax.text(x, y, reference, fontsize=4.2, ha="center", va="center", color=color, weight="bold")
    ax.add_patch(Rectangle((4, 16), 43.1, 43, fill=False, edgecolor="#dc2626", linewidth=1.0, linestyle="--"))
    ax.text(25.5, 14.5, "NFC copper/component keep-out", ha="center", va="center", fontsize=7, color="#dc2626")
    fig.savefig(OUT / "top_assembly.png", dpi=240, facecolor="white")
    plt.close(fig)
    draw_top_crop(board, unions, (45, 94, 22, 54), "rf_crop.png", "PN7161 + NFC matching / top copper")
    draw_top_crop(board, unions, (109, 149.5, 0, 27), "usb_crop.png", "ESP32-C6 + USB-C / top copper")
    draw_top_crop(board, unions, (82, 149.5, 28, 74.5), "power_crop.png", "Battery + servo power / top copper")
    print(f"Rendered {OUT.relative_to(ROOT)}/copper_layers.png")
    print(f"Rendered {OUT.relative_to(ROOT)}/top_assembly.png")
    print(f"Rendered RF, USB, and power close-ups")


if __name__ == "__main__":
    main()
