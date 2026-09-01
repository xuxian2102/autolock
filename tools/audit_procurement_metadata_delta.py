#!/usr/bin/env python3
"""Prove the procurement update did not change electrical or PCB data."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HARDWARE = ROOT / "hardware"
REPORT = ROOT / "reports" / "PROCUREMENT_METADATA_DELTA_AUDIT.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_design_data(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def electrical_signature(part):
    return {
        "ref": part.ref,
        "value": part.value,
        "symbol": part.symbol,
        "footprint": part.footprint,
        "pins": dict(sorted(part.pins.items())),
        "page": part.page,
        "block": part.block,
        "pcb_at": list(part.pcb_at),
        "dnp": part.dnp,
        "source_footprint": part.source_footprint,
        "fields": dict(sorted(part.fields.items())),
    }


def normalize_schematic(text: str) -> str:
    return re.sub(
        r'(^    \(property "(?:LCSC|MPN|Manufacturer)" ")[^"]*(" \(id \d+\) \(at [^\n]+$)',
        r'\1<METADATA>\2',
        text,
        flags=re.MULTILINE,
    )


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: audit_procurement_metadata_delta.py BASELINE.tar.gz")
    archive = Path(sys.argv[1]).resolve()
    if not archive.is_file():
        raise FileNotFoundError(archive)

    errors = []
    schematic_checks = []
    with tempfile.TemporaryDirectory(prefix="rev-a-procurement-baseline-") as temp_dir:
        temp = Path(temp_dir)
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(temp, filter="data")
        baseline = temp / ROOT.name

        current_board = HARDWARE / "kicad" / "HomeKey-Lock-RevA-PN7161.kicad_pcb"
        baseline_board = baseline / "kicad" / current_board.name
        board_identical = current_board.read_bytes() == baseline_board.read_bytes()
        if not board_identical:
            errors.append("PCB differs from U5 frozen baseline")

        for current in sorted((HARDWARE / "kicad" / "schematics").glob("*.kicad_sch")):
            old = baseline / "kicad" / "schematics" / current.name
            normalized_identical = normalize_schematic(current.read_text(encoding="utf-8")) == normalize_schematic(old.read_text(encoding="utf-8"))
            schematic_checks.append({"file": current.name, "identical_after_metadata_normalization": normalized_identical})
            if not normalized_identical:
                errors.append(f"{current.name}: change outside allowed procurement fields")

        current_data = load_design_data(ROOT / "tools" / "design_data.py", "current_rev_a_design_data")
        baseline_data = load_design_data(baseline / "tools" / "design_data.py", "baseline_rev_a_design_data")
        current_signature = [electrical_signature(part) for part in current_data.parts]
        baseline_signature = [electrical_signature(part) for part in baseline_data.parts]
        electrical_identical = current_signature == baseline_signature
        if not electrical_identical:
            errors.append("design_data electrical signature differs from U5 frozen baseline")

        report = {
            "baseline_archive": str(archive),
            "baseline_archive_sha256": sha256(archive),
            "board_identical": board_identical,
            "board_sha256": sha256(current_board),
            "schematics": schematic_checks,
            "design_data_electrical_signature_identical": electrical_identical,
            "allowed_changes": ["LCSC", "MPN", "Manufacturer", "procurement note/report/export files"],
            "errors": errors,
            "result": "PASS" if not errors else "FAIL",
        }

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Procurement metadata delta audit: {report['result']}")
    print(f"PCB identical: {report['board_identical']}; electrical signature identical: {report['design_data_electrical_signature_identical']}")
    print(f"Schematic normalized checks: {sum(item['identical_after_metadata_normalization'] for item in schematic_checks)}/{len(schematic_checks)}")
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
