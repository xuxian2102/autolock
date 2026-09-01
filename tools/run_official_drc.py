#!/usr/bin/env python3
"""Run KiCad 10's native PCB DRC and require a clean JSON report."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
WORKSPACE = ROOT.parent
BOARD = HARDWARE / "kicad" / "HomeKey-Lock-RevA-PN7161.kicad_pcb"
REPORT = ROOT / "reports" / "KICAD10_DRC_FINAL_RELEASE.json"
KICAD_ROOT = WORKSPACE / ".tools" / "kicad10-full-root"
KICAD_CLI = KICAD_ROOT / "usr" / "bin" / "kicad-cli"


def main():
    if not KICAD_CLI.exists():
        raise RuntimeError(f"KiCad 10 CLI not found: {KICAD_CLI}")
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = ":".join(
        str(path) for path in (KICAD_ROOT / "usr/lib", KICAD_ROOT / "usr/lib/x86_64-linux-gnu")
    )
    # --refill-zones: the ground pours ship unfilled so the board file stays
    # readable by every kiutils-based tool here.  Without this flag DRC would
    # check the board as if the planes were not there and report every ground
    # connection that depends on them as unconnected.
    command = [
        str(KICAD_CLI), "pcb", "drc", "--output", str(REPORT),
        "--format", "json", "--units", "mm", "--severity-all",
        "--refill-zones", "--exit-code-violations", str(BOARD),
    ]
    process = subprocess.run(
        command, cwd=ROOT, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if process.returncode:
        raise RuntimeError(f"Official KiCad DRC failed ({process.returncode}):\n{process.stdout}")
    result = json.loads(REPORT.read_text(encoding="utf-8"))
    violations = result.get("violations", [])
    unconnected = result.get("unconnected_items", [])
    parity = result.get("schematic_parity", [])
    if violations or unconnected or parity:
        raise RuntimeError(
            "Official KiCad DRC is not clean: "
            f"violations={len(violations)} unconnected={len(unconnected)} parity={len(parity)}"
        )
    print(
        f"Official KiCad {result.get('kicad_version', '10')} DRC: PASS "
        f"(0 violations, 0 unconnected)"
    )


if __name__ == "__main__":
    main()
