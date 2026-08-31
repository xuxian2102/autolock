#!/usr/bin/env python3
"""Replace fallback fabrication layers with KiCad 10's official plot output."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
WORKSPACE = ROOT.parent
BOARD = HARDWARE / "kicad" / "HomeKey-Lock-RevA-PN7161.kicad_pcb"
GERBERS = HARDWARE / "production" / "gerbers"
REPORTS = ROOT / "reports"
MANIFEST = REPORTS / "MANUFACTURING_EXPORT.json"
REPORT = REPORTS / "OFFICIAL_KICAD10_FABRICATION_EXPORT.md"
PROJECT = "HomeKey-Lock-RevA-PN7161"
KICAD_ROOT = WORKSPACE / ".tools" / "kicad10-full-root"
KICAD_CLI = KICAD_ROOT / "usr" / "bin" / "kicad-cli"


GERBER_MAP = {
    f"{PROJECT}-F_Cu.gtl": f"{PROJECT}.GTL",
    f"{PROJECT}-In1_Cu.g1": f"{PROJECT}.G2",
    f"{PROJECT}-In2_Cu.g2": f"{PROJECT}.G3",
    f"{PROJECT}-B_Cu.gbl": f"{PROJECT}.GBL",
    f"{PROJECT}-F_Mask.gts": f"{PROJECT}.GTS",
    f"{PROJECT}-B_Mask.gbs": f"{PROJECT}.GBS",
    f"{PROJECT}-F_Paste.gtp": f"{PROJECT}.GTP",
    f"{PROJECT}-B_Paste.gbp": f"{PROJECT}.GBP",
    f"{PROJECT}-F_Silkscreen.gto": f"{PROJECT}.GTO",
    f"{PROJECT}-B_Silkscreen.gbo": f"{PROJECT}.GBO",
    f"{PROJECT}-Edge_Cuts.gm1": f"{PROJECT}.GKO",
}
DRILL_NAMES = (f"{PROJECT}-PTH.drl", f"{PROJECT}-NPTH.drl")


def run(command: list[str], environment: dict[str, str]):
    process = subprocess.run(
        command, cwd=ROOT, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if process.returncode:
        raise RuntimeError(f"Command failed ({process.returncode}): {' '.join(command)}\n{process.stdout}")
    return process.stdout


def validate_gerber(path: Path):
    text = path.read_text(encoding="ascii")
    required = ("TF.GenerationSoftware,KiCad", "TF.FileFunction", "%MOMM*%", "M02*")
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(f"Official Gerber validation failed for {path.name}: missing {missing}")


def validate_drill(path: Path):
    text = path.read_text(encoding="ascii")
    required = ("TF.GenerationSoftware,Kicad", "METRIC", "M30")
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(f"Official drill validation failed for {path.name}: missing {missing}")


def main():
    if not KICAD_CLI.exists():
        raise RuntimeError(f"KiCad 10 CLI not found: {KICAD_CLI}")
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = ":".join(
        str(path) for path in (KICAD_ROOT / "usr/lib", KICAD_ROOT / "usr/lib/x86_64-linux-gnu")
    )
    version = run([str(KICAD_CLI), "--version"], environment).strip().splitlines()[-1]
    with tempfile.TemporaryDirectory(prefix="official-fabrication-", dir=REPORTS) as temp_name:
        temporary = Path(temp_name)
        run(
            [
                str(KICAD_CLI), "pcb", "export", "gerbers", "--output", str(temporary),
                "--layers", "F.Cu,In1.Cu,In2.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts",
                "--subtract-soldermask", "--precision", "6", str(BOARD),
            ],
            environment,
        )
        run(
            [
                str(KICAD_CLI), "pcb", "export", "drill", "--output", str(temporary),
                "--format", "excellon", "--drill-origin", "absolute",
                "--excellon-zeros-format", "decimal", "--excellon-oval-format", "route",
                "--excellon-units", "mm", "--excellon-separate-th", str(BOARD),
            ],
            environment,
        )
        for source_name, destination_name in GERBER_MAP.items():
            source = temporary / source_name
            if not source.exists():
                raise RuntimeError(f"Missing official Gerber: {source_name}")
            validate_gerber(source)
            shutil.copyfile(source, GERBERS / destination_name)
        for name in DRILL_NAMES:
            source = temporary / name
            if not source.exists():
                raise RuntimeError(f"Missing official drill file: {name}")
            validate_drill(source)
            shutil.copyfile(source, GERBERS / name)

    expected = set(GERBER_MAP.values()) | set(DRILL_NAMES)
    actual = {path.name for path in GERBERS.iterdir() if path.is_file()}
    if actual != expected:
        raise RuntimeError(f"Official output set mismatch: missing={sorted(expected-actual)} extra={sorted(actual-expected)}")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(GERBERS.iterdir())}
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["fabrication_exporter"] = f"KiCad official pcb export {version}"
    manifest["sha256"] = hashes
    manifest["official_layer_mapping"] = GERBER_MAP
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Official KiCad 10 fabrication export",
        "",
        "- Result: **PASS**",
        f"- KiCad: `{version}`",
        f"- Board: `{BOARD.relative_to(ROOT)}`",
        f"- Files: `{len(actual)}/13`",
        "- Gerber: 11 official X2 layers, metric, 4.6 precision",
        "- Drill: official metric PTH + NPTH Excellon, routed slots",
        "- Silkscreen: reference text and legacy arcs retained",
        "",
        "The filename mapping preserves the existing JLCPCB upload contract while replacing every fallback fabrication file with KiCad's official plot output.",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Official KiCad fabrication export: PASS ({len(actual)}/13 files)")


if __name__ == "__main__":
    main()
