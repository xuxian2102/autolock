# Schematic PDF status

The editable KiCad sources under `kicad/` are authoritative.

The existing schematic PDFs were plotted before the D5 polarity repair. A release-day regeneration attempt with KiCad 10.0.6 produced the same `bus error` for every sheet as the unchanged baseline, so no newly plotted page was accepted. The five page PDFs and their combined PDF therefore remain reference-only; this limitation does not affect the separately exported PCB Gerbers, drill files, BOM, or CPL.

For D5, ignore the old `LED_K` label in the PDFs. The released source and manufacturing data use:

- D5 pin 1 / cathode → GND;
- D5 pin 2 / anode → R9 → `STATUS_LED`;
- D5: C12624 / KT-0603G;
- PCB/CPL rotation: 270°.

Regenerate `02_MCU_IO.pdf` and the combined schematic PDF from the supplied KiCad source before treating the PDF set as current.
