# Autolock NFC reduced-order model

This package models the Rev A 13.56 MHz link as two magnetically coupled coils,
then applies explicit, calibratable installation terms. It is intended for
comparisons and engineering margin tracking. It does **not** use Friis path
loss, claim an exact iPhone antenna, or predict absolute HomeKey range.

## Install and run

```bash
python -m pip install -e '.[report]'
python -m nfc_model extract-antenna
python -m nfc_model simulate \
  --door zetland-solid-core \
  --door-thickness 40 \
  --lock-distance 10 \
  --ferrite 0.5 \
  --phone-gap 2 \
  --phone-offset 10 \
  --phone-angle 10
```

The CLI also accepts `--scenario-file case.json` or YAML. Standard scenarios
are stored in `data/scenarios/`.

## Model boundary

The geometry solver reads
`HomeKey-Lock-RevA-PN7161_RevA_Review_Package.zip` and extracts the embedded
KiCad footprint from the authoritative board. The field calculation integrates
Biot-Savart over the actual four rectangular turn centre-lines. Mutual
inductance uses the Neumann filament integral for arbitrary separation, lateral
offset and tilt:

\[
M={\mu_0\over4\pi}\oint\oint{d\boldsymbol\ell_1\cdot
d\boldsymbol\ell_2\over |\mathbf r_1-\mathbf r_2|},\qquad
k={M\over\sqrt{L_1L_2}}.
\]

Rev A self-inductance is independently estimated with the Mohan current-sheet
expression for a square planar spiral. AC resistance uses copper skin depth and
a documented proximity-effect prior. These analytical values must be replaced
or corrected by the first-board VNA measurement.

Ordinary door cores are non-magnetic. Their dielectric ranges are retained in
`data/materials.json`, while **distance** is solved geometrically and the tiny
material absorption diagnostic is kept separate. Metal is a bounded prior that
combines skin-depth saturation, projected overlap, distance and position. It
outputs separate field, L and Q terms. Ferrite has a finite-thickness saturation
model and an explicit `metal × ferrite` interaction; it is not multiplied as an
independent magic recovery factor.

The matching calculation reads the as-built component values:

- L3/L4: 160 nH each;
- C27/C28: 330 pF to ground;
- C29/C30: 68 pF series;
- C31/C32: 100 pF to ground;
- R18/R19: 2.7 Ω series damping;
- C33-C36: unpopulated trim positions.

Without measured PN7161 port impedance and installed S-parameters, the network
is normalized to the intended 13.56 MHz air state and the installed frequency
is propagated with the first-order LC square-root law. The result is a tuning
direction and starting range, not a substitute for a VNA.

## Output meanings

- `field_loss_db`: `20 log10(H/H_ref)` for the magnetic near field per 1 A.
- `coupling_loss_db`: `20 log10(k/k_ref)`.
- `coupling_only_margin_db`: same coupling comparison.
- `nfc_margin_proxy_db`: coupling plus the first-order detune response and a
  stored-energy Q term. It has no pass/fail threshold until HomeKey trials are
  supplied.

The reference is the same parameterized phone loop centered and parallel at a
2 mm free-air gap. A different verified reference can be adopted after testing.

## Calibration formats

NanoVNA summary JSON/CSV requires `test,f0_MHz,Q,L_uH,S11_min_dB`:

```bash
python -m nfc_model calibrate-vna measurements/vna_summary.csv
```

Full Touchstone fitting is intentionally a TODO because an S11 minimum alone
does not contain the complex network response.

HomeKey threshold fitting requires at least three **measured** rows containing
`model_margin_db,successes,trials`:

```bash
python -m nfc_model calibrate-homekey measurements/homekey_trials.csv
```

No synthetic success data are bundled.

## Sources and priors

Every material range and its URL is recorded in `data/materials.json`. Principal
references are NXP AN13219 for PN7160/PN7161 matching, TI SLOA241C for installed
metal/ferrite tuning practice, Mohan et al. (IEEE JSSC 1999,
doi:10.1109/4.792620) for planar spiral inductance, TDK NFC ferrite data, and
USDA FPL-RP-245 for the wide frequency/moisture dependence of wood products.

The numerical metal/ferrite coefficients in `metal.py` and `ferrite.py` are
named calibration priors. No source provides a geometry-independent “wood door
loss” or “lock body loss”, so none is asserted here.

## Tests

```bash
python -m unittest discover -s tools/nfc_model/tests -v
```
