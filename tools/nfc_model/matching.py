"""First-order link between installed antenna L/Q and the Rev A matching network."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import log10, pi, sqrt


@dataclass(frozen=True)
class MatchingNetwork:
    emc_inductor_nh_each: float = 160.0       # L3/L4
    emc_shunt_pf_each: float = 330.0          # C27/C28
    series_match_pf_each: float = 68.0        # C29/C30
    antenna_shunt_pf_each: float = 100.0      # C31/C32
    damping_ohm_each: float = 2.7             # R18/R19
    emc_inductor_esr_ohm_each: float = 0.34   # estimate; verify component at 13.56 MHz
    design_frequency_hz: float = 13.56e6

    @property
    def known_series_loss_ohm(self) -> float:
        return 2.0 * (self.damping_ohm_each + self.emc_inductor_esr_ohm_each)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MatchingResult:
    f0_air_hz: float
    f0_installed_hz: float
    delta_f_hz: float
    installed_loaded_q: float
    detune_amplitude: float
    detune_loss_db: float
    equivalent_series_capacitance_each_pf: float
    series_trim_delta_each_pf: float
    retune_recommended: bool
    retune_instruction: str
    method: str

    def to_dict(self) -> dict:
        return asdict(self)


def installed_matching(
    *,
    inductance_air_h: float,
    inductance_installed_h: float,
    resistance_installed_ohm: float,
    network: MatchingNetwork | None = None,
    l_tolerance_fraction: float = 0.0,
    capacitance_tolerance_fraction: float = 0.0,
) -> MatchingResult:
    network = network or MatchingNetwork()
    # The full differential network and PN7161 output impedance require VNA and
    # controller measurements.  Normalize the as-built air network to its 13.56
    # MHz design target, then propagate L/C changes with the LC square-root law.
    l_ratio = inductance_installed_h * (1.0 + l_tolerance_fraction) / inductance_air_h
    c_ratio = 1.0 + capacitance_tolerance_fraction
    f_air = network.design_frequency_hz / sqrt(max((1.0 + l_tolerance_fraction) * c_ratio, 1e-9))
    f_installed = network.design_frequency_hz / sqrt(max(l_ratio * c_ratio, 1e-9))
    omega = 2.0 * pi * network.design_frequency_hz
    loaded_q = omega * inductance_installed_h / max(
        resistance_installed_ohm + network.known_series_loss_ohm, 1e-9)
    fractional_detune = (f_installed - network.design_frequency_hz) / network.design_frequency_hz
    response = 1.0 / sqrt(1.0 + (2.0 * loaded_q * fractional_detune) ** 2)
    loss_db = 20.0 * log10(max(response, 1e-15))
    target_cap = network.series_match_pf_each / max(l_ratio, 1e-9)
    trim_delta = target_cap - network.series_match_pf_each
    recommend = abs(f_installed - network.design_frequency_hz) > 0.25e6 or loss_db < -0.5
    if trim_delta > 0.5:
        instruction = (f"L fell; add approximately {trim_delta:.1f} pF at each C33/C34 trim position, "
                       "then VNA-tune C35/C36 for impedance/bandwidth")
    elif trim_delta < -0.5:
        instruction = (f"L rose; reduce each C29/C30 by approximately {-trim_delta:.1f} pF equivalent; "
                       "parallel DNP pads cannot subtract capacitance, then VNA-tune C35/C36")
    else:
        instruction = "nominal series-C correction is below 0.5 pF; verify installed f0/Q before changing parts"
    return MatchingResult(f_air, f_installed, f_installed - f_air, loaded_q, response, loss_db,
                          target_cap, trim_delta, recommend, instruction,
                          "air network normalized to 13.56 MHz; first-order LC shift, not full PN7161 S-parameter fit")
