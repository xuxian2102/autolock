---
name: kicad-verify
description: Independently verify the HomeKey Lock Rev A KiCad board — runs DRC, checks the copper netlist pad-by-pad against the design_data.py manifest, holds the manifest to the connection rules five critical datasheets state as requirements, and confirms the released Gerber ZIP still reproduces byte-for-byte from the committed .kicad_pcb. Use this whenever anyone touches the board, the manifest, the routing, the BOM/CPL, or the production files; before answering "is this ready to order", "did my change break anything", or "is the release still good"; and after any edit to kicad/ or tools/. Also use it when asked to review, audit, or sanity-check the PCB — the project's own audit scripts both produce and certify the release, so they cannot answer these questions on their own.
---

# Verifying the Rev A board from outside the project's own tooling

## Why this exists

`tools/` contains about 9,600 lines that generate the board *and* audit it. When
the generator and its auditor share an assumption, they agree with each other and
still be wrong — a green report from `audit_board.py` tells you the project is
self-consistent, not that it is correct.

This skill asks KiCad the same questions directly. KiCad has no stake in the
project's assumptions, so when it agrees, that means something.

## Run it

```bash
python3 .claude/skills/kicad-verify/scripts/verify_release.py
```

Takes about five seconds. Exits 0 when everything passes, 1 otherwise, so it
drops straight into CI.

Useful flags:

| Flag | Why you'd use it |
|---|---|
| `--strict` | Also re-runs DRC with every rule the project set to `ignore` turned back on. Run this before believing any "0 violations" claim. |
| `--only net` | Just the netlist check (repeatable: `--only drc --only net`). Fast loop while editing the manifest. |
| `--only pins` | Just the datasheet pin rules. Instant, needs no KiCad — run it on every manifest edit. |

CPL rotation is a separate script, `scripts/audit_cpl_rotation.py` — it needs network rather than KiCad. See below.
| `--project DIR` | Point at a different tree — e.g. a scratch copy you're experimenting on. |
| `--zip PATH` | Compare against a specific Gerber ZIP instead of the one in `production/`. |
| `--json PATH` | Machine-readable results for CI or for diffing across commits. |

The script finds the board via `$HOMEKEY_PROJECT_DIR`, else `<repo>/.work/HomeKey-Lock-RevA-PN7161`.
Both are set up by the SessionStart hook; if `kicad-cli` is missing, run
`.claude/hooks/session-start.sh`.

## What the three checks actually prove

**1. DRC** — KiCad's own rule checker on the committed board.

The plain run answers "does this pass as the project has configured it". The
`--strict` run answers the different and more useful question: *what is being
silenced?* A project can reach zero violations by fixing problems or by turning
off the rule that reports them, and only the strict pass separates those. Today
the strict pass reports 199 `track_dangling` violations on In1.Cu — the ground
"mesh" is drawn as ~7,200 track segments instead of a copper zone, so it leaves
dangling stubs, and `track_dangling` was set to `ignore` in the `.kicad_pro`.
That is expected until the plane is converted to a real zone; it is not a
netlist break (unconnected count is 0).

**2. Netlist vs manifest** — the load-bearing check.

`kicad-cli pcb export ipcd356` makes KiCad walk the actual copper and emit which
net each pad lands on. The script compares that, pad by pad, against
`design_data.py`. Nothing in `tools/` participates in computing either side, so
this genuinely tests whether the board implements the intended design.

A pad on the board that the manifest neither maps nor declares in `NC_PINS` is
reported too — that catches stray copper as well as missing connections.

Baseline: 309 pads across 104 parts, 0 mismatches.

**3. Pin maps vs datasheets** — the check that stops a dead chip.

Reading five datasheets by hand catches a wrong pin map once; encoding what
they *require* catches it every time. `references/pinmaps.json` records the
verified pin functions for U1, U3, U5, U6 and Q1, each citing the document and
revision it came from, plus the connection rules those documents state outright
— NXP's "TVDD_IN and TVDD_IN2 must be connected to VDD(TX)", "VBAT2 must be
connected to VBAT", the grounds that must be grounds, the `i.c.` pins that must
be left open, and the source/drain groupings that decide whether Q1's
reverse-polarity FET faces the right way.

A swapped supply pin is not a defect a board test finds gently, and it survives
DRC and the netlist check untouched: both of those confirm the copper matches
the manifest, and neither knows what the manifest *should* say. This is the
only check that does.

Baseline: 14 rules across 5 parts, 0 failures.

When you change a part, re-verify against its current datasheet revision and
update the JSON with the new citation — a stale rule that still passes is worse
than no rule.

**4. Gerber reproduction** — release integrity.

Re-exports Gerbers from the committed `.kicad_pcb` and compares all 11 layers
against the released ZIP, ignoring only the header lines that encode export
wall-clock time. If this passes, the files someone would send to the fab still
correspond to the board in git.

Baseline: 11/11 byte-identical.

## Reading a failure

Interpret results by *which* check broke — they fail for very different reasons:

- **Netlist mismatch** — the board and the manifest disagree. This is the
  serious one. Either the routing changed without the manifest, or vice versa.
  The reported net names tell you which side moved. Never wave this through.
- **Gerber differs, netlist and DRC clean** — the board changed since the ZIP
  was cut. Usually it just needs re-exporting; confirm the netlist check passed
  first, then regenerate the release rather than editing Gerbers.
- **Gerber differs on silkscreen only** — likely an export-flag drift, not a
  board change. The script exports with `--no-protel-ext --subtract-soldermask`;
  the soldermask subtraction is what makes silk reproduce, and dropping it makes
  F_Silkscreen/B_Silkscreen differ while the board is untouched.
- **Pin rule failure** — the manifest now contradicts a datasheet. Treat it as
  the most serious result here: DRC and the netlist check will both still pass,
  because the copper faithfully implements the wrong thing.
- **New violation type under `--strict`** — a rule got silenced. Ask why before
  accepting it.

## Settling CPL rotation without a JLCPCB preview

`scripts/audit_cpl_rotation.py` answers the question a local audit normally
cannot: is each CPL angle measured against the same zero-degree orientation
JLC's pick-and-place uses? That orientation lives in JLC's parts library — but
the library is reachable, because LCSC serves the EasyEDA package model for any
part number and that model is what JLC assembles from.

The script fetches each part's LCSC model and asks at which rotation the
project's footprint lands pad-for-pad on it *with the pad numbers agreeing*.
Insisting on the numbers is the whole point: a SOIC-8's pad positions are
180-degree symmetric, so a position-only comparison would call a rotation
harmless when it would actually swap pin 1 with pin 5.

Baseline: 52 of 55 part groups correct as emitted, including every polarised
part — D1-D5, C44, J2, Q1, U1-U6, X1 and the ESP32-C6 module. The three
exceptions are benign and worth knowing so nobody re-investigates them:

- **J4** pin header — through-hole, not in the CPL, hand-soldered to silkscreen.
- **L3/L4** 0603 inductor — numbering agrees only at 180 degrees, but a
  two-terminal inductor has interchangeable ends.
- **SW1-3** tact switch — the project numbers the pads by row (1,1,2,2), LCSC
  by corner (1,2,3,4). Geometry fits at 0 and 180, and an SPST switch is
  symmetric between its two rows.

One trap this proves: the community JLC rotation database used by the standard
KiCad plugins matches four of this board's footprints by name prefix
(`^SOIC-`, `^SOT-23`, `^TSOT-23`) and would rotate Q1, U1, U2 and U6. Those are
EasyEDA-derived footprints that already sit in JLC's orientation, so applying
those corrections would break parts that are currently right. Do not run this
board's CPL through a generic rotation-correction step.

## Things worth knowing before you edit

`design_data.py` is the single source of truth for the netlist; the PCB is
generated from it. The `.kicad_sch` files are *not* — they are drawings that do
not carry the netlist (they fail ERC heavily and there is no root sheet, so KiCad
cannot even open the project's schematic). Until that is fixed, this script is
the only thing standing between a manifest edit and a wrong board, which is why
it is worth running on every change rather than at release time.

Passing all three checks is not a production release. It says the board is
internally sound and the files match. It says nothing about the parts of the
release that only exist outside this repo: JLCPCB's Gerber and PCBA previews,
library rotation of D5/J2/U4/U5/X1, live LCSC stock, and first-article RF,
power and mechanical testing.
