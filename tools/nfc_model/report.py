"""Deterministically generate the Rev A engineering report, tables, and plots."""

from __future__ import annotations

import csv
from dataclasses import replace
import json
from math import log10, pi, sqrt
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .coil import estimate_electrical, estimate_inductance, magnetic_field
from .coupling import coupling_coefficient, mutual_inductance
from .ferrite import FerriteSheet
from .geometry import RectangularCoil, phone_case
from .kicad_extract import extract_rev_a
from .metal import MetalObject
from .model import db20, simulate
from .monte_carlo import run_monte_carlo
from .scenarios import get_scenario


REPORT_PATH = Path("reports/NFC_LINK_MODEL_REV_A.md")
DATA_DIR = Path("reports/nfc_model")
PLOT_DIR = DATA_DIR / "plots"


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _scenario_row(name: str, source) -> dict:
    result = simulate(get_scenario(name), source=source)
    return {
        "scenario": name,
        "separation_mm": result.geometry["total_coil_separation_mm"],
        "field_loss_db": result.field["field_loss_db"],
        "coupling_loss_db": result.coupling["coupling_loss_db"],
        "L_installed_uH": result.antenna_installed["L_uH"],
        "Q_loaded": result.antenna_installed["Q_loaded"],
        "f0_installed_MHz": result.matching["f0_installed_hz"] / 1e6,
        "detune_loss_db": result.matching["detune_loss_db"],
        "nfc_margin_proxy_db": result.margin["nfc_margin_proxy_db"],
        "prior_uncertainty_db": result.environment["prior_uncertainty_db_1sigma_approx"],
    }


def _scaled_coil(base: RectangularCoil, outer_mm: float) -> RectangularCoil:
    delta = outer_mm - base.outer_width_mm
    sizes = tuple((width + delta, height + delta) for width, height in base.turn_sizes_mm)
    return RectangularCoil(
        name=f"Rev B parametric {outer_mm:.0f} mm",
        turn_sizes_mm=sizes,
        trace_width_mm=base.trace_width_mm,
        spacing_mm=base.spacing_mm,
        copper_thickness_um=base.copper_thickness_um,
        metadata={"status": "parametric geometry only; not a PCB revision"},
    )


def _fmt(value, digits=2):
    return f"{value:.{digits}f}"


def _table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def generate_report(*, source: str | Path | None = None, monte_carlo_samples: int = 1200) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    extracted = extract_rev_a(source)
    source = source or Path(extracted.source.split(":", 1)[0])
    coil = extracted.coil
    air = estimate_electrical(coil)

    scenario_names = [
        "FREE_AIR", "ZETLAND_BASELINE", "ZETLAND_FRAME", "ZETLAND_LOCK_NEAR",
        "ZETLAND_LOCK_FRAME", "ZETLAND_LOCK_FERRITE_03", "ZETLAND_LOCK_FERRITE_05",
        "ZETLAND_LOCK_FRAME_FERRITE_05", "WORST_STEEL",
    ]
    scenarios = [_scenario_row(name, source) for name in scenario_names]
    _write_csv(DATA_DIR / "scenario_results.csv", scenarios)

    door_rows = []
    for thickness in (30, 35, 40, 45, 50):
        for material in ("air", "solid_timber", "plywood", "MDF", "particleboard",
                         "fire_rated_composite", "glass", "common_plastic"):
            base = get_scenario("ZETLAND_BASELINE").with_updates(
                name=f"door-{material}-{thickness}", door_material=material, door_thickness_mm=thickness)
            result = simulate(base, source=source)
            door_rows.append({
                "material": material,
                "thickness_mm": thickness,
                "total_separation_mm": result.geometry["total_coil_separation_mm"],
                "door_material_term_db": result.environment["door"]["loss_db"],
                "field_loss_db": result.field["field_loss_db"],
                "coupling_loss_db": result.coupling["coupling_loss_db"],
            })
    _write_csv(DATA_DIR / "door_sweep.csv", door_rows)

    metal_rows = []
    base_lock = get_scenario("ZETLAND_LOCK_NEAR")
    for position in ("behind", "partial_overlap", "beside"):
        for gap in (2, 5, 10, 20, 30, 50, 80):
            for ferrite_mm in (0.0, 0.3, 0.5):
                metal = replace(base_lock.metals[0], distance_mm=gap, position=position)
                case = base_lock.with_updates(name=f"metal-{position}-{gap}-{ferrite_mm}", metals=(metal,),
                                              ferrite=FerriteSheet("medium", ferrite_mm))
                result = simulate(case, source=source)
                effect = result.environment["metal_effects"][0]["effect"]
                metal_rows.append({
                    "position": position, "gap_mm": gap, "ferrite_mm": ferrite_mm,
                    "field_term_db": effect["field_loss_db"],
                    "coupling_loss_db": result.coupling["coupling_loss_db"],
                    "L_uH": result.antenna_installed["L_uH"],
                    "Q_loaded": result.antenna_installed["Q_loaded"],
                    "f0_MHz": result.matching["f0_installed_hz"] / 1e6,
                    "uncertainty_db": effect["uncertainty_db"],
                })
    _write_csv(DATA_DIR / "metal_gap_sweep.csv", metal_rows)

    ferrite_rows = []
    z4 = get_scenario("ZETLAND_LOCK_FRAME")
    for ferrite_class in ("low", "medium", "high"):
        for thickness in (0, 0.2, 0.3, 0.5, 0.8, 1.0):
            case = z4.with_updates(name=f"ferrite-{ferrite_class}-{thickness}",
                                   ferrite=FerriteSheet(ferrite_class, thickness))
            result = simulate(case, source=source)
            ferrite_rows.append({
                "ferrite_class": ferrite_class, "thickness_mm": thickness,
                "coupling_loss_db": result.coupling["coupling_loss_db"],
                "recovery_vs_Z4_db": result.coupling["coupling_loss_db"] - scenarios[4]["coupling_loss_db"],
                "L_uH": result.antenna_installed["L_uH"],
                "Q_loaded": result.antenna_installed["Q_loaded"],
                "f0_MHz": result.matching["f0_installed_hz"] / 1e6,
                "detune_loss_db": result.matching["detune_loss_db"],
            })
    _write_csv(DATA_DIR / "ferrite_sweep.csv", ferrite_rows)

    alignment_rows = []
    baseline = get_scenario("ZETLAND_BASELINE")
    for offset in (0, 5, 10, 15, 20, 25, 30):
        for angle in (0, 10, 20, 30):
            result = simulate(baseline.with_updates(name=f"align-{offset}-{angle}", offset_x_mm=offset,
                                                    tilt_y_deg=angle), source=source)
            alignment_rows.append({"offset_mm": offset, "angle_deg": angle,
                                   "coupling_loss_db": result.coupling["coupling_loss_db"],
                                   "field_loss_db": result.field["field_loss_db"]})
    _write_csv(DATA_DIR / "alignment_sweep.csv", alignment_rows)

    size_rows = []
    rx = phone_case("Phone-M")
    rev_a_m = mutual_inductance(coil, rx, separation_mm=42.0, quadrature_order=5)
    rev_a_k = coupling_coefficient(rev_a_m, air.inductance_h, estimate_inductance(rx))
    for size in (35, 40, 45, 50, 55, 60):
        candidate = _scaled_coil(coil, size)
        electrical = estimate_electrical(candidate)
        mutual = mutual_inductance(candidate, rx, separation_mm=42.0, quadrature_order=5)
        k = coupling_coefficient(mutual, electrical.inductance_h, estimate_inductance(rx))
        h = float(np.linalg.norm(magnetic_field(candidate, (0, 0, 42))) / (4e-7 * pi))
        size_rows.append({
            "outer_centerline_mm": size,
            "L_uH": electrical.inductance_h * 1e6,
            "R_ac_ohm": electrical.resistance_ac_ohm,
            "Q_bare": electrical.q_air,
            "M_at_42mm_nH": mutual * 1e9,
            "k_at_42mm": k,
            "k_gain_vs_RevA_db": db20(k / rev_a_k),
            "H_at_42mm_A_per_m_per_A": h,
        })
    _write_csv(DATA_DIR / "antenna_size_sweep.csv", size_rows)

    mc_base = get_scenario("ZETLAND_LOCK_FRAME_FERRITE_05")
    monte = run_monte_carlo(mc_base, n=monte_carlo_samples, seed=7161, source=source, include_samples=True)
    (DATA_DIR / "monte_carlo_summary.json").write_text(
        json.dumps(monte.to_dict(include_samples=False), indent=2), encoding="utf-8")
    _write_csv(DATA_DIR / "monte_carlo_samples.csv", monte.samples)
    _write_csv(DATA_DIR / "sensitivity.csv", monte.sensitivity)
    (DATA_DIR / "rev_a_extraction.json").write_text(
        json.dumps(extracted.to_dict(), indent=2), encoding="utf-8")
    acceptance_case = get_scenario("ZETLAND_BASELINE").with_updates(
        name="ACCEPTANCE_EXAMPLE",
        offset_x_mm=10.0,
        tilt_y_deg=10.0,
        metals=(MetalObject(distance_mm=10.0, position="partial_overlap"),),
        ferrite=FerriteSheet("medium", 0.5),
    )
    acceptance_result = simulate(acceptance_case, source=source)
    (DATA_DIR / "acceptance_example.json").write_text(
        json.dumps(acceptance_result.to_dict(), indent=2), encoding="utf-8")

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    baseline_material = [row for row in door_rows if row["material"] == "fire_rated_composite"]
    ax.plot([row["thickness_mm"] for row in baseline_material],
            [row["coupling_loss_db"] for row in baseline_material], marker="o")
    ax.set(xlabel="Door thickness (mm)", ylabel="Coupling margin vs 2 mm free air (dB)",
           title="Distance dominates the non-metal door sweep")
    fig.tight_layout(); fig.savefig(PLOT_DIR / "door_thickness.png", dpi=170); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for ferrite_mm in (0.0, 0.3, 0.5):
        rows = [row for row in metal_rows if row["position"] == "partial_overlap" and row["ferrite_mm"] == ferrite_mm]
        ax.plot([row["gap_mm"] for row in rows], [row["field_term_db"] for row in rows], marker="o",
                label=f"ferrite {ferrite_mm:.1f} mm")
    ax.axhline(-1.0, color="black", linewidth=0.8, linestyle="--", label="-1 dB design line")
    ax.set(xlabel="Antenna-to-lock metal gap (mm)", ylabel="Metal field term (dB)",
           title="Reduced-order lock-body prior (partial overlap)")
    ax.legend(); fig.tight_layout(); fig.savefig(PLOT_DIR / "metal_gap.png", dpi=170); plt.close(fig)

    fig, ax1 = plt.subplots(figsize=(7.2, 4.4))
    rows = [row for row in ferrite_rows if row["ferrite_class"] == "medium"]
    ax1.plot([row["thickness_mm"] for row in rows], [row["recovery_vs_Z4_db"] for row in rows],
             marker="o", color="#1877b5", label="coupling recovery")
    ax1.set(xlabel="Ferrite thickness (mm)", ylabel="Coupling recovery (dB)")
    ax2 = ax1.twinx()
    ax2.plot([row["thickness_mm"] for row in rows], [row["f0_MHz"] for row in rows],
             marker="s", color="#c44e52", label="installed f0")
    ax2.set_ylabel("First-order installed f0 (MHz)")
    ax1.set_title("Ferrite recovery saturates while inductance detunes the network")
    lines = ax1.lines + ax2.lines
    ax1.legend(lines, [line.get_label() for line in lines], loc="center right")
    fig.tight_layout(); fig.savefig(PLOT_DIR / "ferrite_tradeoff.png", dpi=170); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    sens = monte.sensitivity[:10][::-1]
    ax.barh([item["variable"] for item in sens], [item["importance_abs_rho"] for item in sens])
    ax.set(xlabel="|Spearman rho| with NFC margin proxy", title="Global sensitivity over stated uncertainty ranges")
    fig.tight_layout(); fig.savefig(PLOT_DIR / "sensitivity.png", dpi=170); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    margins = [row["nfc_margin_proxy_db"] for row in monte.samples]
    ax.hist(margins, bins=36, color="#4c72b0", alpha=0.85)
    for percentile, style in ((50, "-"), (10, "--"), (1, ":")):
        ax.axvline(np.percentile(margins, percentile), color="black", linestyle=style,
                   label=f"P{percentile} {np.percentile(margins, percentile):.1f} dB")
    ax.set(xlabel="NFC margin proxy vs 2 mm free air (dB)", ylabel="Samples",
           title=f"Z5 uncertainty propagation (n={monte_carlo_samples})")
    ax.legend(); fig.tight_layout(); fig.savefig(PLOT_DIR / "monte_carlo_margin.png", dpi=170); plt.close(fig)

    partial_no_ferrite = [row for row in metal_rows if row["position"] == "partial_overlap" and row["ferrite_mm"] == 0.0]
    partial_ferrite = [row for row in metal_rows if row["position"] == "partial_overlap" and row["ferrite_mm"] == 0.5]
    gap_no_ferrite = next((row["gap_mm"] for row in partial_no_ferrite if row["field_term_db"] >= -1.0), 80)
    gap_ferrite = next((row["gap_mm"] for row in partial_ferrite if row["field_term_db"] >= -1.0), 80)
    z1 = scenarios[1]; z3 = scenarios[3]; z4row = scenarios[4]; zf03 = scenarios[5]; zf05 = scenarios[7]
    mc_margin = monte.summary["nfc_margin_proxy_db"]
    top_sensitivity = monte.sensitivity[:8]
    size50 = next(row for row in size_rows if row["outer_centerline_mm"] == 50)

    scenario_table = _table(
        ["Case", "H loss dB", "k loss dB", "L µH", "loaded Q", "f0 MHz", "detune dB", "margin proxy dB"],
        [[row["scenario"], _fmt(row["field_loss_db"]), _fmt(row["coupling_loss_db"]),
          _fmt(row["L_installed_uH"], 3), _fmt(row["Q_loaded"], 1), _fmt(row["f0_installed_MHz"], 3),
          _fmt(row["detune_loss_db"]), _fmt(row["nfc_margin_proxy_db"])] for row in scenarios],
    )
    thickness_table = _table(
        ["Door mm", "total separation mm", "composite material term dB", "coupling margin dB"],
        [[row["thickness_mm"], row["total_separation_mm"], _fmt(row["door_material_term_db"], 3),
          _fmt(row["coupling_loss_db"])] for row in baseline_material],
    )
    ferrite_table = _table(
        ["Ferrite mm", "recovery vs Z4 dB", "L µH", "f0 MHz", "detune dB"],
        [[row["thickness_mm"], _fmt(row["recovery_vs_Z4_db"]), _fmt(row["L_uH"], 3),
          _fmt(row["f0_MHz"], 3), _fmt(row["detune_loss_db"])] for row in rows],
    )
    sensitivity_table = _table(
        ["Rank", "Variable", "Spearman ρ", "|ρ|"],
        [[index + 1, item["variable"], _fmt(item["spearman_rho"], 3), _fmt(item["importance_abs_rho"], 3)]
         for index, item in enumerate(top_sensitivity)],
    )

    report = rf"""# Autolock NFC Link Model — Rev A

Generated from the authoritative KiCad board inside the Rev A review package. Model version 0.1.0. Results are relative magnetic-near-field engineering quantities, not far-field path loss and not absolute HomeKey pass/fail claims.

## A. Executive conclusion

**Rev A is worth continuing as an instrumented first-board prototype, but the model does not establish that it will unlock through a 40 mm door.** The extracted 40 mm four-turn coil is electrically plausible: independent calculation gives **L = {air.inductance_h*1e6:.3f} µH**, **RAC ≈ {air.resistance_ac_ohm:.2f} Ω**, and bare-air **Q ≈ {air.q_air:.0f}**. The older 1.49 µH estimate is {abs(air.inductance_h*1e6/1.49-1)*100:.1f}% lower; that disagreement is small enough for a first-order formula but large enough to require VNA measurement before fixing matching values.

The non-metal door core is not the main magnetic blocker. At 40 mm thickness plus a 2 mm phone gap, the model gives **{z1['coupling_loss_db']:.1f} dB coupling loss** relative to the same Phone-M equivalent at 2 mm. Almost all of that is geometric separation; the deliberately conservative composite-material term is only **{next(row for row in baseline_material if row['thickness_mm']==40)['door_material_term_db']:.3f} dB**.

The lock body is more dangerous than the core. The Z3 prior adds about **{z3['coupling_loss_db']-z1['coupling_loss_db']:.1f} dB** of coupling loss at 10 mm partial overlap, while the side frame at 50 mm adds only **{scenarios[2]['coupling_loss_db']-z1['coupling_loss_db']:.2f} dB** in the stated geometry. A frame or reinforcement that overlaps the coil is a different, high-risk case and is not represented by the benign side-frame number.

Ferrite is worth designing into the mechanical stack, but **0.3 mm is the modeled knee**, not a free gain. In Z4, 0.3 mm medium ferrite recovers **{zf03['coupling_loss_db']-z4row['coupling_loss_db']:.2f} dB** of coupling and 0.5 mm recovers **{zf05['coupling_loss_db']-z4row['coupling_loss_db']:.2f} dB**; the extra 0.2 mm gives only {zf05['coupling_loss_db']-zf03['coupling_loss_db']:.2f} dB in this prior. Ferrite also raises L, moving the normalized network to {zf03['f0_installed_MHz']:.3f} MHz at 0.3 mm and {zf05['f0_installed_MHz']:.3f} MHz at 0.5 mm. Therefore **default 0.3 mm for the first mechanical stack, buy 0.5 mm as a comparison sample, and tune only in the final installed state**.

The first Rev B priority is mechanical/RF co-design: preserve alignment, maximize antenna-to-metal gap, reserve full ferrite coverage, and make installed matching easy. A parametric 50 mm coil improves k at 42 mm by **{size50['k_gain_vs_RevA_db']:.2f} dB** in the simplified geometry, but it also changes L and matching; do not enlarge the antenna until Rev A measurements show that the 40 mm coil lacks margin. Matching optimization cannot recover geometric flux by itself, but an untuned ferrite stack can throw away several dB.

## B. Model assumptions

- Frequency: 13.56 MHz; quasi-static magnetic coupling because dimensions are tiny relative to wavelength.
- Tx geometry: actual KiCad turn centre-lines. H uses numerical Biot–Savart integration; M uses the Neumann line integral.
- Rx geometry: parameterized Phone-S/M/L equivalents only. They are not identified with any iPhone model.
- Reference: same phone equivalent, centered/parallel at a 2 mm free-air gap.
- Ordinary door materials have µr≈1. Their permittivity/loss ranges are retained, but door thickness enters mainly as coil separation.
- Metal and ferrite results are bounded, named priors. Their coefficients are not universal material constants.
- Air matching is normalized to the 13.56 MHz design intent. The full differential PN7161 port impedance is not known.
- `NFC margin proxy` combines 20log10(k/kref), first-order resonance response, and a stored-energy Q term. Its success threshold is intentionally null.

Core equations:

\[
M=\frac{{\mu_0}}{{4\pi}}\oint\!\oint
\frac{{d\boldsymbol\ell_1\cdot d\boldsymbol\ell_2}}{{|\mathbf r_1-\mathbf r_2|}},
\quad k=\frac{{M}}{{\sqrt{{L_1L_2}}}},
\quad \delta=\sqrt{{\frac{{2}}{{\omega\mu\sigma}}}}.
\]

The square-spiral L estimate uses Mohan's current-sheet expression. Metal is separated into field shielding, L shift, Q loss, and detuning. Ferrite enters both alone and as an explicit metal×ferrite isolation term.

## C. Baseline Rev A

| Quantity | Extracted/calculated value | Status |
|---|---:|---|
| PCB | 150 × 75 × {extracted.pcb_thickness_mm:.1f} mm, 4 layer | KiCad/production baseline |
| AE1 placement | ({extracted.footprint_at_mm[0]:.1f}, {extracted.footprint_at_mm[1]:.1f}) mm on F.Cu | KiCad extracted |
| Turn centre-line sides | {', '.join(f'{w:.1f}' for w, _ in coil.turn_sizes_mm)} mm | KiCad extracted |
| Copper outer extent | {coil.outer_width_mm+coil.trace_width_mm:.1f} × {coil.outer_height_mm+coil.trace_width_mm:.1f} mm | KiCad extracted |
| Turns / width / gap | {coil.turns} / {coil.trace_width_mm:.1f} / {coil.spacing_mm:.1f} mm | KiCad extracted |
| Conductor length | {extracted.raw_trace_length_mm:.3f} mm incl. {extracted.crossover_length_mm:.3f} mm B.Cu crossover | KiCad extracted |
| Copper thickness | 35 µm nominal; 18–35 µm range | **not encoded in stackup; TODO verify order** |
| Lair | {air.inductance_h*1e6:.3f} µH | Mohan estimate |
| RDC / RAC | {air.resistance_dc_ohm:.3f} / {air.resistance_ac_ohm:.3f} Ω | RAC has proximity prior |
| Qair bare / loaded reference | {air.q_air:.1f} / {scenarios[0]['Q_loaded']:.1f} | loaded includes R18/R19 and estimated L3/L4 ESR |

The board drawing marks x=4–48 mm, y=16–59 mm as “NFC ANTENNA — NO METAL / NO COPPER”; official layer review found no inner-layer copper under AE1. This is a board/layout fact, not proof that the installed door has no metal.

Current RF values independently read from the design manifest/BOM are L3/L4 160 nH, C27/C28 330 pF, C29/C30 68 pF, C31/C32 100 pF, R18/R19 2.7 Ω, with C33–C36 DNP trim pads.

## D. Door material and thickness results

{thickness_table}

At fixed 40 mm thickness, every non-metal material in the database changes the magnetic result by less than 0.04 dB in this reduced model. That conclusion is robust only if the “fire-rated core” contains no sheet, mesh, foil, large fasteners, or reinforcement. Moisture-dependent dielectric ranges remain in the database, but they do not become a made-up fixed “wood door attenuation”.

![Door thickness sweep](nfc_model/plots/door_thickness.png)

## E. Metal results

{scenario_table}

For the medium partial-overlap lock prior, the first sampled gap meeting a -1 dB metal-field design line is **{gap_no_ferrite} mm without ferrite** and **{gap_ferrite} mm with 0.5 mm medium ferrite**. These are model-derived mechanical targets, not universal NFC rules. TI's independent general guidance uses 10 mm as a baseline separation while warning that effects remain application-specific.

Skin depth at 13.56 MHz is only a few micrometres for the median mild-steel prior, so millimetre metal is already “thick” electromagnetically. That does not justify exp(-t/δ) as a whole-system link loss: finite size, edge return paths, overlap, orientation, L/Q change, and ferrite interaction dominate.

![Metal gap sweep](nfc_model/plots/metal_gap.png)

## F. Ferrite results

{ferrite_table}

The recovery saturates with thickness, while L continues to move enough to require installed-state tuning. Low/medium/high permeability sweeps are in `reports/nfc_model/ferrite_sweep.csv`; the class names are ranges, not approved part numbers.

![Ferrite trade-off](nfc_model/plots/ferrite_tradeoff.png)

## G. Sensitivity

Sensitivity uses |Spearman ρ| against the margin proxy over the explicit Monte Carlo ranges. It is not a universal feature ranking.

{sensitivity_table}

The leading electrical variable is installed L tolerance because the nominal network is held fixed; this is exactly why VNA tuning after adding ferrite/metal is a release gate. Among mechanical variables, door thickness/total separation and phone alignment dominate. Lock distance and ferrite become more important when their uncertainty is restricted to close-metal assemblies rather than the broad mixed range used here.

![Sensitivity](nfc_model/plots/sensitivity.png)

## H. Monte Carlo

Z5 (40 mm nominal door, lock + frame + 0.5 mm ferrite) with {monte_carlo_samples} samples gives:

| Metric | P10 | median | P90 | worst reasonable P1 |
|---|---:|---:|---:|---:|
| coupling margin dB | {monte.summary['coupling_margin_db']['P10']:.2f} | {monte.summary['coupling_margin_db']['median']:.2f} | {monte.summary['coupling_margin_db']['P90']:.2f} | {monte.summary['coupling_margin_db']['worst_reasonable_P1']:.2f} |
| NFC margin proxy dB | {mc_margin['P10']:.2f} | {mc_margin['median']:.2f} | {mc_margin['P90']:.2f} | {mc_margin['worst_reasonable_P1']:.2f} |
| installed L µH | {monte.summary['installed_L_uH']['P10']:.3f} | {monte.summary['installed_L_uH']['median']:.3f} | {monte.summary['installed_L_uH']['P90']:.3f} | {monte.summary['installed_L_uH']['worst_reasonable_P1']:.3f} |
| installed f0 MHz | {monte.summary['installed_f0_MHz']['P10']:.3f} | {monte.summary['installed_f0_MHz']['median']:.3f} | {monte.summary['installed_f0_MHz']['P90']:.3f} | {monte.summary['installed_f0_MHz']['worst_reasonable_P1']:.3f} |

“Worst reasonable” is P1 of these stated priors, not an absolute physical bound. No synthetic success probability is attached.

![Monte Carlo margin](nfc_model/plots/monte_carlo_margin.png)

## I. Installation and Rev B recommendations

1. Treat **{gap_no_ferrite} mm** as the no-ferrite target from antenna copper boundary to a partial-overlap medium lock body; if the real geometry cannot meet it, use full-area NFC ferrite and target at least **{gap_ferrite} mm** in the same model class.
2. Avoid any steel-frame or reinforcement overlap with the 40.4 mm copper outline. “Frame at the side” is safe only when the measured edge gap matches the scenario.
3. Start with **0.3 mm medium NFC ferrite** covering the entire loop plus a small margin. Also procure 0.5 mm for A/B testing; do not stack blindly.
4. Mark the outside phone target over the coil centre. At 42 mm separation, keep routine lateral error below **10 mm** and tilt below **10°** until measured maps establish more margin. The full grid is in `alignment_sweep.csv`.
5. Measure AE1 alone, then assembled. If L rises, replace C29/C30 with lower values; DNP C33/C34 can only add parallel capacitance. Use C35/C36 after the resonance direction is corrected to refine impedance/bandwidth. The example 0.3 mm Z4 prior requires about {68*(1/(zf03['L_installed_uH']/(air.inductance_h*1e6))-1):.1f} pF change per 68 pF series branch (negative means reduce), but the VNA value wins.
6. Rev B ordering: **mechanical location/metal clearance → ferrite provision → installed matching accessibility → antenna size**. A 50 mm parametric coil has a modeled {size50['k_gain_vs_RevA_db']:.2f} dB k gain at 42 mm, but only after its new L/network and phone-size interaction are designed.

## J. What cannot be known yet

- exact iPhone or Apple Watch NFC antenna geometry and orientation;
- HomeKey receiver/reader threshold and PN7161 dynamic-power behavior;
- actual door internal structure, moisture, metal mesh/reinforcement, and lock-body shape;
- actual ferrite µ′, µ″, adhesive, coverage, cracks, and compression;
- copper thickness and fabricated trace tolerance;
- complex PN7161 port impedance and complete installed S-parameters.

Accordingly, `P(HomeKey success)` remains `null` in every uncalibrated output.

## K. Measurement plan

1. Add a temporary coax/VNA fixture at the antenna isolation point; perform SOL calibration at the fixture plane and sweep at least 10–20 MHz.
2. Test A: PCB in air. Record complex S11/Touchstone, f0, L, series R and Q.
3. Test B: PCB + each 0.2/0.3/0.5 mm ferrite sample. Keep spacer and coverage documented.
4. Test C: PCB on the real door but away from the lock; separates geometry/core from metal.
5. Test D: add the real lock at controlled 2/5/10/20/30/50 mm gaps and at behind/beside/partial-overlap positions.
6. Test E: complete installed assembly. Retune C29/C30 first, then C35/C36, and remeasure bandwidth/Q.
7. HomeKey map: for each phone/watch model, run at least 20 repeats per 0/10/20/30/40 mm offset and 0/10/20/30° tilt. Record response time as well as success count.
8. Feed summary data to `calibrate-vna`; retain Touchstone for a future complex-network fitter. Only after repeated trials, use `calibrate-homekey` to fit an empirical threshold.

## L. Need for further EM simulation

The reduced-order model is adequate for distance, offset, tilt, coil-size comparisons, first-order L/R/Q, material-vs-distance separation, skin-depth screening, tuning direction, and uncertainty bookkeeping.

It is **not** adequate to determine the exact loss from the real lock body, steel-frame edge, hidden reinforcement, ferrite edge/fringing, or partial overlap. These are both high-uncertainty and potentially high-sensitivity. The next EM task should therefore be narrowly scoped to **Rev A AE1 + measured lock geometry + selected ferrite + nearby frame**, first in openEMS/FEM and only then in HFSS/CST/COMSOL if needed. Do not simulate the whole door before Test C confirms that non-metal core behavior is ordinary.

## Sources

1. NXP, [PN7160 antenna design and matching guide, AN13219 Rev. 1.6](https://www.nxp.com/docs/en/application-note/AN13219.pdf).
2. NXP, [PN7160/PN7161 NFC Controller datasheet](https://www.nxp.com/docs/en/data-sheet/PN7160_PN7161.pdf).
3. Texas Instruments, [Antenna Design Guide for the TRF79xxA, SLOA241C](https://www.ti.com/lit/an/sloa241c/sloa241c.pdf).
4. S. S. Mohan et al., [Simple Accurate Expressions for Planar Spiral Inductances](https://web.stanford.edu/~boyd/papers/pdf/inductance_expressions.pdf), IEEE JSSC 34(10), 1999, doi:10.1109/4.792620.
5. TDK, [RFID/NFC magnetic sheet product data](https://www.tdk-electronics.tdk.com/download/140970/f46250e00025633e241b63ef5ee8c174/product-survey-pp.pdf).
6. TDK, [IFQ06 NFC magnetic sheet overview](https://www.tdk-electronics.tdk.com/en/374108/tech-library/articles/products-technologies/products-technologies/tdk-introduces-new-ifq06-ultra-thin-magnetic-sheets-with-high-permeability-for-nfc-applications-/3159212).
7. USDA Forest Products Laboratory, [Dielectric properties of wood and hardboard, 20 Hz–50 MHz, FPL-RP-245](https://www.fpl.fs.usda.gov/documnts/fplrp/fplrp245.pdf).
8. NIST, [Measurement of Materials Dielectric Properties](https://www.nist.gov/publications/measurement-materials-dielectric-properties).

Material ranges and per-field provenance are machine-readable in `tools/nfc_model/data/materials.json`. All reduced-order calibration priors are named in `metal.py` and `ferrite.py`; unknowns use ranges/TODO measurement rather than fabricated precision.
"""
    REPORT_PATH.write_text(report, encoding="utf-8")
    return {
        "report": str(REPORT_PATH),
        "data_directory": str(DATA_DIR),
        "plots": sorted(str(path) for path in PLOT_DIR.glob("*.png")),
        "monte_carlo_samples": monte_carlo_samples,
        "rev_a_L_uH": air.inductance_h * 1e6,
        "metal_gap_target_no_ferrite_mm": gap_no_ferrite,
        "metal_gap_target_ferrite_05_mm": gap_ferrite,
    }
