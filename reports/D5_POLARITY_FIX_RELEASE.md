# D5 polarity fix — release regression

## Approved change

- D5 pin 1 / K → GND.
- D5 pin 2 / A → R9 → `STATUS_LED`.
- Net 17 renamed from `LED_K` to `LED_A`.
- D5 rotated in place from 90° to 270°; pad centers and all track/via geometry are preserved.
- D5 assigned C12624 / KT-0603G / Hubei KENTO Elec.

## Regression result

| Check | Result |
|---|---|
| Exact delta against 20/21 frozen archive | PASS |
| Non-target schematic sheets byte-identical | PASS, 4/4 |
| PCB track/via records byte-identical | PASS |
| D5 pad centers swapped exactly in place | PASS |
| Independent geometry audit | PASS, 0 errors / 0 warnings |
| Physical copper connectivity | PASS, 68/68 nets |
| KiCad 10 PCB DRC | PASS, 0 violations / 0 unconnected |
| Manufacturing export audit | PASS, 13/13 files |
| JLC BOM unresolved active SMT LCSC fields | PASS, 0 |
| CPL D5 entry | PASS, X=145.000 mm, Y=21.000 mm, Top, 270° |

KiCad command-line schematic ERC/PDF plotting bus-errors on both the unchanged baseline and the repaired file in this portable environment. The exact schematic baseline audit therefore gates this repair. The editable KiCad source is authoritative; see `docs/SCHEMATIC_PDF_STATUS.md` for the stale PDF notice.

Production remains HOLD until the Gerber and CPL previews are inspected on JLCPCB and the first board completes RF, power and mechanism tests.
