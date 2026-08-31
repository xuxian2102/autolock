"""Extract the authoritative Rev A antenna from the KiCad board or review ZIP."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
import re
import statistics
import zipfile

from .geometry import RectangularCoil


DEFAULT_REVIEW_ZIP = "HomeKey-Lock-RevA-PN7161_RevA_Review_Package.zip"
DEFAULT_BOARD_MEMBER = "kicad/HomeKey-Lock-RevA-PN7161.kicad_pcb"


@dataclass(frozen=True)
class ExtractedRevA:
    coil: RectangularCoil
    footprint_at_mm: tuple[float, float, float]
    copper_outer_bbox_mm: tuple[float, float, float, float]
    documented_keepout_bbox_mm: tuple[float, float, float, float] | None
    pcb_thickness_mm: float
    copper_thickness_source: str
    raw_trace_length_mm: float
    crossover_length_mm: float
    source: str

    def to_dict(self) -> dict:
        result = asdict(self)
        result["coil"] = asdict(self.coil)
        return result


def _balanced_block(text: str, start: int) -> str:
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise ValueError("unterminated KiCad S-expression block")


def read_board(source: str | Path, member: str = DEFAULT_BOARD_MEMBER) -> tuple[str, str]:
    source_path = Path(source)
    if source_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(source_path) as archive:
            text = archive.read(member).decode("utf-8-sig")
        return text, f"{source_path}:{member}"
    return source_path.read_text(encoding="utf-8-sig"), str(source_path)


def find_review_zip(start: str | Path | None = None) -> Path:
    current = Path(start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / DEFAULT_REVIEW_ZIP
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"could not find {DEFAULT_REVIEW_ZIP} above {current}")


@lru_cache(maxsize=8)
def extract_rev_a(source: str | Path | None = None, *, copper_thickness_um: float = 35.0) -> ExtractedRevA:
    source = Path(source) if source is not None else find_review_zip()
    text, source_name = read_board(source)
    marker = re.search(r'\(footprint\s+"[^"]*NFC_Antenna_40x40_4T"', text)
    if not marker:
        raise ValueError("Rev A NFC antenna footprint was not found")
    block = _balanced_block(text, marker.start())

    at_match = re.search(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\)', block)
    if not at_match:
        raise ValueError("antenna placement is missing")
    at = tuple(float(value or 0.0) for value in at_match.groups())

    line_pattern = re.compile(
        r'\(fp_line\s+\(start\s+(-?[\d.]+)\s+(-?[\d.]+)\)\s+'
        r'\(end\s+(-?[\d.]+)\s+(-?[\d.]+)\).*?'
        r'\(layer\s+"([FB]\.Cu)"\).*?'
        r'(?:\(stroke\s+\(width\s+([\d.]+)\)|\(width\s+([\d.]+)\))',
        re.DOTALL,
    )
    lines = []
    for match in line_pattern.finditer(block):
        x1, y1, x2, y2 = map(float, match.groups()[:4])
        width = float(match.group(6) or match.group(7))
        lines.append((x1, y1, x2, y2, match.group(5), width))
    if len(lines) < 17:
        raise ValueError(f"expected the embedded spiral copper, found only {len(lines)} line objects")

    # The continuous spiral alternates long left and right verticals.  The four
    # left-side segments each span one complete turn; right-side segments are
    # shortened by the transition to the next turn.
    long_verticals = [line for line in lines if line[4] == "F.Cu" and abs(line[0] - line[2]) < 1e-9
                      and line[0] < -20.0 and abs(line[3] - line[1]) > 30.0]
    turn_sizes = sorted(((abs(line[3] - line[1]), abs(line[3] - line[1])) for line in long_verticals), reverse=True)
    # One long vertical exists per turn in this continuous square spiral.
    trace_width = statistics.median(line[5] for line in lines)
    pitches = [turn_sizes[i][0] - turn_sizes[i + 1][0] for i in range(len(turn_sizes) - 1)]
    pitch = statistics.median(pitches) / 2.0
    spacing = pitch - trace_width
    raw_length = sum(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 for x1, y1, x2, y2, _, _ in lines)
    crossover_length = sum(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                           for x1, y1, x2, y2, layer, _ in lines if layer == "B.Cu")

    xs = [value for line in lines for value in (line[0], line[2])]
    ys = [value for line in lines for value in (line[1], line[3])]
    copper_bbox = (at[0] + min(xs) - trace_width / 2.0,
                   at[1] + min(ys) - trace_width / 2.0,
                   at[0] + max(xs) + trace_width / 2.0,
                   at[1] + max(ys) + trace_width / 2.0)

    thickness = re.search(r'\(general\s+\(thickness\s+([\d.]+)\)', text, re.DOTALL)
    keepout = re.search(r'\(gr_rect\s+\(start\s+([\d.]+)\s+([\d.]+)\)\s+'
                        r'\(end\s+([\d.]+)\s+([\d.]+)\)\s+\(layer\s+"Dwgs\.User"\)', text)
    keepout_bbox = tuple(map(float, keepout.groups())) if keepout else None

    coil = RectangularCoil(
        name="Autolock Rev A AE1",
        turn_sizes_mm=tuple(turn_sizes),
        trace_width_mm=trace_width,
        spacing_mm=spacing,
        copper_thickness_um=copper_thickness_um,
        metadata={
            "copper_thickness_range_um": [18.0, 35.0],
            "copper_thickness_status": "not encoded in KiCad stackup; 35 um nominal assumption",
            "antenna_layer": "F.Cu with one B.Cu crossover",
            "source_footprint": "HomeKey_RevA:NFC_Antenna_40x40_4T",
            "actual_conductor_length_mm": raw_length,
            "closed_turn_filament_length_mm": sum(2.0 * (width + height) for width, height in turn_sizes),
        },
    )
    return ExtractedRevA(
        coil=coil,
        footprint_at_mm=at,
        copper_outer_bbox_mm=copper_bbox,
        documented_keepout_bbox_mm=keepout_bbox,
        pcb_thickness_mm=float(thickness.group(1)) if thickness else 1.6,
        copper_thickness_source="assumed nominal 35 um; KiCad file has no stackup copper thickness",
        raw_trace_length_mm=raw_length,
        crossover_length_mm=crossover_length,
        source=source_name,
    )
