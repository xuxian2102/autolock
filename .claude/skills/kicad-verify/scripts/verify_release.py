#!/usr/bin/env python3
"""Verify the HomeKey Lock Rev A release without trusting the project's own audits.

The project ships ~9600 lines of audit tooling that both produces the release
and certifies it.  That is a closed loop: if a generator is wrong, its matching
auditor is wrong the same way.  This script closes the loop from the outside by
asking KiCad itself three questions:

  1. DRC   - does KiCad think the board is clean?
  2. NET   - does the copper actually implement design_data.py, pad by pad?
           (KiCad computes the netlist; we never ask the project's code.)
  3. PINS  - does the manifest still obey the connection rules the five
           critical datasheets state as requirements?
  4. GERBER- does the released ZIP still fall out of the committed .kicad_pcb,
           byte for byte?

Only step 2 reads project code, and only design_data.py, which is the manifest
under test rather than an auditor of it.

Exit status is 0 when every requested check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# Released ZIP uses Protel extensions; kicad-cli emits -<Layer>.gbr names.
LAYER_MAP = {
    "GTL": "F_Cu", "G2": "In1_Cu", "G3": "In2_Cu", "GBL": "B_Cu",
    "GTS": "F_Mask", "GBS": "B_Mask", "GTP": "F_Paste", "GBP": "B_Paste",
    "GTO": "F_Silkscreen", "GBO": "B_Silkscreen", "GKO": "Edge_Cuts",
}

# These flags are not cosmetic: --subtract-soldermask is what makes the
# silkscreen layers reproduce.  Without it F_Silkscreen/B_Silkscreen differ
# from the release even though the board is identical, which looks alarming
# and is not.  Found by bisecting the export options against the shipped ZIP.
# --check-zones fills the ground pours as part of the export.  The board file
# stores them unfilled -- that is what keeps it readable by the project's
# kiutils tools -- so without this flag In1.Cu and B.Cu come out as bare tracks
# and nothing matches the release.
GERBER_FLAGS = ["--no-protel-ext", "--subtract-soldermask", "--check-zones"]

# Volatile header lines: they encode export wall-clock time, not board content.
VOLATILE = re.compile(r"TF\.CreationDate|TF\.ProjectId|G04 Created by|CreationDate")

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def paint(ok: bool, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{GREEN if ok else RED}{text}{RESET}"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def normalise(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [line for line in text if not VOLATILE.search(line)]


def find_project(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("HOMEKEY_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "hardware" / "kicad").is_dir() and (parent / "tools").is_dir():
            return parent
    sys.exit(
        "Cannot locate the project.  Expected a repository root holding both\n"
        "  hardware/kicad/   and   tools/\n"
        "Pass --project if it lives somewhere else."
    )


# --------------------------------------------------------------------------
# 1. DRC
# --------------------------------------------------------------------------
def check_drc(pcb: Path, workdir: Path, strict: bool) -> dict:
    """Ask KiCad whether the board is clean.

    With --strict we first flip every rule the project set to "ignore" back to
    "error".  That matters because a project can reach "0 violations" either by
    fixing problems or by silencing the rule that reports them, and only the
    strict pass tells those two apart.
    """
    target_pcb, label = pcb, "as configured"
    if strict:
        staged = workdir / "strict"
        shutil.copytree(pcb.parent, staged)
        silenced = []
        for pro in staged.glob("*.kicad_pro"):
            data = json.loads(pro.read_text())
            sev = data.get("board", {}).get("design_settings", {}).get("rule_severities", {})
            for key, value in list(sev.items()):
                if value == "ignore":
                    sev[key] = "error"
                    silenced.append(key)
            pro.write_text(json.dumps(data, indent=2))
        target_pcb = staged / pcb.name
        label = f"strict (re-enabled: {', '.join(silenced) or 'nothing was ignored'})"

    out = workdir / f"drc{'_strict' if strict else ''}.json"
    # --refill-zones for the same reason as the Gerber export: the pours ship
    # unfilled, and DRC without it reports every ground connection that relies
    # on a plane as unconnected.
    proc = run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-all",
                "--refill-zones", "--output", str(out), str(target_pcb)])
    if not out.exists():
        return {"ok": False, "error": proc.stderr.strip() or proc.stdout.strip(), "label": label}

    report = json.loads(out.read_text())
    by_type: dict[str, int] = {}
    for violation in report.get("violations", []):
        by_type[violation["type"]] = by_type.get(violation["type"], 0) + 1
    unconnected = len(report.get("unconnected_items", []))
    return {
        "ok": not by_type and not unconnected,
        "label": label,
        "kicad_version": report.get("kicad_version"),
        "violations": sum(by_type.values()),
        "unconnected": unconnected,
        "by_type": by_type,
    }


# --------------------------------------------------------------------------
# 2. Netlist vs manifest
# --------------------------------------------------------------------------
def parse_ipcd356(path: Path) -> dict[str, dict[str, str]]:
    """Fixed-column IPC-D-356: net 4-20, refdes 21-26, pad 28-31."""
    pads: dict[str, dict[str, str]] = {}
    for line in path.read_text(errors="replace").splitlines():
        if not line.startswith(("317", "327")):
            continue
        ref = line[20:26].strip()
        if not ref or ref == "VIA":
            continue
        pads.setdefault(ref, {})[line[27:31].strip()] = line[3:20].strip()
    return pads


def check_netlist(pcb: Path, project: Path, workdir: Path) -> dict:
    out = workdir / "net.d356"
    proc = run(["kicad-cli", "pcb", "export", "ipcd356", "-o", str(out), str(pcb)])
    if not out.exists():
        return {"ok": False, "error": proc.stderr.strip() or proc.stdout.strip()}
    pcb_pads = parse_ipcd356(out)

    sys.path.insert(0, str(project / "tools"))
    try:
        import design_data  # noqa: E402  - the manifest under test
    except Exception as exc:                                  # pragma: no cover
        return {"ok": False, "error": f"cannot import design_data.py: {exc}"}

    # Test points are copper pads with no component; they carry no pin map.
    want: dict[str, dict[str, str]] = {
        part.ref: dict(part.pins)
        for part in design_data.parts
        if not part.ref.startswith("TP")
    }

    mismatches, missing_refs, undeclared = [], [], []
    compared = 0
    for ref, pins in sorted(want.items()):
        got = pcb_pads.get(ref)
        if got is None:
            missing_refs.append(ref)
            continue
        for pad, net in pins.items():
            compared += 1
            key = pad if pad in got else None
            if key is None:
                # IPC-D-356 truncates pad names to 4 chars (e.g. J2's "A1B12").
                candidates = [k for k in got if pad.startswith(k)]
                key = candidates[0] if len(candidates) == 1 else None
            if key is None:
                mismatches.append((ref, pad, net, "absent from the board"))
            elif got[key] != net:
                mismatches.append((ref, pad, net, got[key]))
        declared_nc = set(design_data.NC_PINS.get(ref, []))
        for pad, net in got.items():
            if pad in pins or any(p.startswith(pad) for p in pins):
                continue
            if pad in declared_nc or any(n.startswith(pad) for n in declared_nc):
                continue
            undeclared.append((ref, pad, net))

    return {
        "ok": not mismatches and not missing_refs and not undeclared,
        "parts": len(want),
        "pads_compared": compared,
        "mismatches": mismatches,
        "missing_refs": missing_refs,
        "undeclared_pads": undeclared,
    }


# --------------------------------------------------------------------------
# 2b. Pin maps against the datasheets
# --------------------------------------------------------------------------
def check_pins(project: Path) -> dict:
    """Hold the manifest to the connection rules the datasheets state.

    Reading five datasheets by hand catches a wrong pin map once.  Encoding
    what they require catches it every time someone edits design_data.py --
    which matters because a swapped supply pin is not a defect a board test
    finds gently.  references/pinmaps.json carries the verified pin functions
    and cites the document each came from.
    """
    spec_path = Path(__file__).resolve().parent.parent / "references" / "pinmaps.json"
    if not spec_path.is_file():
        return {"ok": False, "error": f"pin map reference missing: {spec_path}"}
    spec = json.loads(spec_path.read_text())["parts"]

    sys.path.insert(0, str(project / "tools"))
    try:
        import design_data
    except Exception as exc:                                  # pragma: no cover
        return {"ok": False, "error": f"cannot import design_data.py: {exc}"}

    failures, checked = [], 0
    for ref, part_spec in sorted(spec.items()):
        try:
            part = design_data.part_by_ref(ref)
        except StopIteration:
            failures.append((ref, "-", f"{ref} is in the pin-map reference but not in the manifest"))
            continue
        declared_nc = set(design_data.NC_PINS.get(ref, []))

        # The recorded pin functions must still describe the same part.
        for pin in part_spec["pins"]:
            if pin not in part.pins and pin not in declared_nc:
                failures.append((ref, pin,
                                 f"{part_spec['pins'][pin]} is neither wired nor declared NC"))

        for rule in part_spec["rules"]:
            checked += 1
            pins, why = rule["pins"], rule["why"]
            if rule["kind"] == "ground":
                for p in pins:
                    net = part.pins.get(p)
                    if net != "GND":
                        failures.append((ref, p, f"must be GND ({why}), found {net!r}"))
            elif rule["kind"] == "same_net":
                nets = {p: part.pins.get(p) for p in pins}
                if len(set(nets.values())) != 1 or None in nets.values():
                    failures.append((ref, ",".join(pins),
                                     f"must share one net ({why}), found {nets}"))
            elif rule["kind"] == "open":
                for p in pins:
                    if p in part.pins:
                        failures.append((ref, p,
                                         f"must be left open ({why}), found {part.pins[p]!r}"))
                    elif p not in declared_nc:
                        failures.append((ref, p, f"must be declared in NC_PINS ({why})"))

    return {"ok": not failures, "parts": len(spec), "rules": checked, "failures": failures}


# --------------------------------------------------------------------------
# 3. Gerber reproduction
# --------------------------------------------------------------------------
def check_gerbers(pcb: Path, released_zip: Path, workdir: Path) -> dict:
    if not released_zip.is_file():
        return {"ok": False, "error": f"released ZIP not found: {released_zip}"}

    shipped = workdir / "shipped"
    shipped.mkdir()
    with zipfile.ZipFile(released_zip) as archive:
        archive.extractall(shipped)

    regen = workdir / "regen"
    regen.mkdir()
    proc = run(["kicad-cli", "pcb", "export", "gerbers", *GERBER_FLAGS,
                "-o", str(regen) + "/", str(pcb)])
    if not any(regen.iterdir()):
        return {"ok": False, "error": proc.stderr.strip() or proc.stdout.strip()}

    stem = pcb.stem
    identical, differing, absent = [], [], []
    for ext, layer in LAYER_MAP.items():
        a = shipped / f"{stem}.{ext}"
        b = regen / f"{stem}-{layer}.gbr"
        if not a.is_file() or not b.is_file():
            absent.append(ext)
        elif normalise(a) == normalise(b):
            identical.append(ext)
        else:
            differing.append(ext)
    return {
        "ok": not differing and not absent,
        "identical": sorted(identical),
        "differing": sorted(differing),
        "absent": sorted(absent),
        "total": len(LAYER_MAP),
    }


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="repository root (default: $HOMEKEY_PROJECT_DIR, else found from this script)")
    ap.add_argument("--zip", dest="zip_path", help="released Gerber ZIP to reproduce")
    ap.add_argument("--strict", action="store_true",
                    help="also run DRC with every ignored rule re-enabled")
    ap.add_argument("--only", choices=["drc", "net", "pins", "gerber"], action="append",
                    help="run only these checks (repeatable)")
    ap.add_argument("--json", dest="json_out", help="write the full result as JSON")
    args = ap.parse_args()

    if not shutil.which("kicad-cli"):
        sys.exit("kicad-cli not on PATH.  Run .claude/hooks/session-start.sh first.")

    project = find_project(args.project)
    pcbs = list((project / "hardware" / "kicad").glob("*.kicad_pcb"))
    if len(pcbs) != 1:
        sys.exit(f"expected exactly one .kicad_pcb under {project/'kicad'}, found {len(pcbs)}")
    pcb = pcbs[0]

    if args.zip_path:
        released = Path(args.zip_path).resolve()
    else:
        released = project / "hardware" / "production" / "gerber.zip"

    wanted = set(args.only or ["drc", "net", "pins", "gerber"])
    version = run(["kicad-cli", "--version"]).stdout.strip()

    print(f"project    {project}")
    print(f"board      {pcb.name}")
    print(f"kicad-cli  {version}")
    print()

    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="kicad-verify-") as tmp:
        workdir = Path(tmp)

        if "drc" in wanted:
            results["drc"] = check_drc(pcb, workdir, strict=False)
            if args.strict:
                results["drc_strict"] = check_drc(pcb, workdir, strict=True)
        if "net" in wanted:
            results["netlist"] = check_netlist(pcb, project, workdir)
        if "pins" in wanted:
            results["pins"] = check_pins(project)
        if "gerber" in wanted:
            results["gerbers"] = check_gerbers(pcb, released, workdir)

    # ---- report ----------------------------------------------------------
    for key in ("drc", "drc_strict"):
        r = results.get(key)
        if not r:
            continue
        title = "DRC" if key == "drc" else "DRC (strict)"
        if "error" in r:
            print(f"{paint(False, 'FAIL')}  {title}: {r['error']}")
            continue
        print(f"{paint(r['ok'], 'PASS' if r['ok'] else 'FAIL')}  {title} — "
              f"{r['violations']} violations, {r['unconnected']} unconnected")
        print(f"      {DIM}{r['label']}{RESET}" if sys.stdout.isatty() else f"      {r['label']}")
        for vtype, count in sorted(r["by_type"].items(), key=lambda kv: -kv[1]):
            print(f"        {count:5d}  {vtype}")

    r = results.get("netlist")
    if r:
        if "error" in r:
            print(f"{paint(False, 'FAIL')}  Netlist: {r['error']}")
        else:
            print(f"{paint(r['ok'], 'PASS' if r['ok'] else 'FAIL')}  Netlist vs design_data.py — "
                  f"{r['pads_compared']} pads across {r['parts']} parts, "
                  f"{len(r['mismatches'])} mismatches")
            for ref, pad, want_net, got in r["mismatches"][:20]:
                print(f"        {ref}.{pad}: manifest says {want_net!r}, board has {got!r}")
            if len(r["mismatches"]) > 20:
                print(f"        ... and {len(r['mismatches']) - 20} more")
            for ref in r["missing_refs"]:
                print(f"        {ref}: in the manifest but not on the board")
            for ref, pad, net in r["undeclared_pads"]:
                print(f"        {ref}.{pad} carries {net!r} but is neither mapped nor declared NC")

    r = results.get("pins")
    if r:
        if "error" in r:
            print(f"{paint(False, 'FAIL')}  Pin maps: {r['error']}")
        else:
            print(f"{paint(r['ok'], 'PASS' if r['ok'] else 'FAIL')}  Pin maps vs datasheets — "
                  f"{r['rules']} rules across {r['parts']} parts, {len(r['failures'])} failures")
            for ref, pin, msg in r["failures"][:20]:
                print(f"        {ref} pin {pin}: {msg}")

    r = results.get("gerbers")
    if r:
        if "error" in r:
            print(f"{paint(False, 'FAIL')}  Gerbers: {r['error']}")
        else:
            print(f"{paint(r['ok'], 'PASS' if r['ok'] else 'FAIL')}  Gerber reproduction — "
                  f"{len(r['identical'])}/{r['total']} layers byte-identical")
            for ext in r["differing"]:
                print(f"        .{ext} differs from the released ZIP")
            for ext in r["absent"]:
                print(f"        .{ext} missing on one side")

    ok = all(r.get("ok") for r in results.values())
    print()
    print(paint(ok, "All checks passed." if ok else "FAILED — see above."))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, default=str))
        print(f"wrote {args.json_out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
