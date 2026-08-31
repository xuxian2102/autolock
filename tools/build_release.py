#!/usr/bin/env python3
"""Run all Rev A checks and create the reproducible JLC upload archive."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARDWARE = ROOT / "hardware"
PRODUCTION = HARDWARE / "production"
GERBERS = PRODUCTION / "gerbers"
REPORTS = ROOT / "reports"
PROJECT = "HomeKey-Lock-RevA-PN7161"


def run(script):
    subprocess.run([sys.executable, str(HERE / script)], cwd=ROOT, check=True)


def zip_paths(output, paths, base):
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(paths):
            if path.is_file():
                archive.write(path, path.relative_to(base))


def main():
    run("audit_board.py")
    run("audit_connectivity.py")
    run("run_official_drc.py")
    run("export_manufacturing.py")
    run("export_official_fabrication.py")
    run("render_board.py")
    run("audit_manufacturing.py")
    run("audit_cpl.py")
    run("render_official_previews.py")

    # Only the JLC upload archive is generated.  The repository itself is the
    # review package: bundling it again into a tracked zip used to store the
    # same Gerber/BOM/report set three times over.
    fab_zip = PRODUCTION / "gerber.zip"
    zip_paths(fab_zip, GERBERS.iterdir(), GERBERS)

    checksum_path = PRODUCTION / "CHECKSUMS.sha256"
    release_files = [fab_zip, *sorted((PRODUCTION / "assembly").iterdir()), *sorted(GERBERS.iterdir())]
    lines = []
    for path in release_files:
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.relative_to(PRODUCTION)}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"Created {fab_zip.relative_to(ROOT)}")
    print(f"Updated {checksum_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
