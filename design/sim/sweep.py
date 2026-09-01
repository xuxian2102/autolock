#!/usr/bin/env python3
"""Run nfc_match.cir over a list of (coil, match) cases and print the table.

The deck is the single source of truth for the topology; this only substitutes
the four .param values and reads the measured resonance back.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DECK = Path(__file__).resolve().parent / "nfc_match.cir"


def run(lant_uh, rant, cs_pf, cp_pf):
    text = DECK.read_text()
    for name, value in (("Lant", f"{lant_uh}u"), ("Rant", f"{rant}"),
                        ("Cs", f"{cs_pf}p"), ("Cp", f"{cp_pf}p")):
        text, count = re.subn(rf"^\.param {name}=.*$", f".param {name}={value}",
                              text, count=1, flags=re.MULTILINE)
        assert count == 1, name
    with tempfile.NamedTemporaryFile("w", suffix=".cir", delete=False) as handle:
        handle.write(text)
        path = handle.name
    out = subprocess.run(["ngspice", "-b", path], capture_output=True, text=True).stdout
    got = {k: float(v) for k, v in re.findall(r"^(ipk|flo|fhi) = (\S+)", out, re.MULTILINE)}
    peak = re.search(r"^ipk\s+=\s+\S+\s+at=\s*(\S+)", out, re.MULTILINE)
    if len(got) != 3 or not peak:
        sys.exit(f"ngspice did not report all measurements:\n{out}")
    got["f0"] = float(peak.group(1))
    bandwidth = got["fhi"] - got["flo"]
    return got["f0"], bandwidth, got["f0"] / bandwidth, got["ipk"]


CASES = [
    ("Rev A 40x40, as built", 1.491, 1.80, 68, 100),
    ("40x65 with the Rev A caps", 1.979, 2.40, 68, 100),
    ("40x65 retuned", 1.979, 2.40, 51, 75),
]

# Rev A as built lands here in this deck.  Quote errors against it rather than
# against 13.560 MHz: the deck carries a consistent bias (it terminates the RX
# taps into 1 k, which the review's original run evidently did not), so the
# distance from the proven design is the meaningful number, not the absolute.
REFERENCE_F0 = 13.805e6

NOMINAL = (1.979, 2.40, 51, 75)


def main():
    print(f"{'case':28}{'Cs':>5}{'Cp':>5}{'f0 MHz':>10}{'vs RevA':>9}"
          f"{'BW MHz':>9}{'Q':>7}{'I_ant':>9}")
    for label, inductance, resistance, cs, cp in CASES:
        f0, bandwidth, q, ipk = run(inductance, resistance, cs, cp)
        print(f"{label:28}{cs:>4}p{cp:>4}p{f0 / 1e6:>10.3f}"
              f"{(f0 / REFERENCE_F0 - 1) * 100:>+8.2f}%{bandwidth / 1e6:>9.3f}"
              f"{q:>7.1f}{ipk:>9.4f}")

    inductance, resistance, cs, cp = NOMINAL
    print(f"\ncoil inductance is calculated, not measured -- {cs}p/{cp}p against a"
          f" swept L (nominal {inductance} uH):")
    print(f"  {'L err':>6}{'L uH':>8}{'f0 MHz':>10}{'vs RevA':>9}   corrective action")
    for error in (-20, -10, 0, 10, 20):
        swept = round(inductance * (1 + error / 100), 4)
        f0, _bandwidth, _q, _ipk = run(swept, resistance, cs, cp)
        offset = (f0 / REFERENCE_F0 - 1) * 100
        if abs(offset) < 2.2:
            action = "none, inside the -3 dB band"
        elif offset > 0:
            action = "fit C33/C34 or C35/C36 (they only add C)"
        else:
            action = "needs SMALLER C: replace C29-C32, not a trim"
        print(f"  {error:>+5}%{swept:>8.3f}{f0 / 1e6:>10.3f}{offset:>+8.1f}%   {action}")


if __name__ == "__main__":
    main()
