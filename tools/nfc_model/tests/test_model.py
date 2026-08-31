from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest

from nfc_model.calibration import calibrate_vna
from nfc_model.coil import estimate_electrical, magnetic_field, skin_depth
from nfc_model.coupling import mutual_inductance
from nfc_model.kicad_extract import extract_rev_a, find_review_zip
from nfc_model.matching import installed_matching
from nfc_model.model import simulate
from nfc_model.scenarios import get_scenario


class RevAGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = find_review_zip(Path(__file__).resolve())
        cls.extracted = extract_rev_a(cls.source)

    def test_actual_kicad_geometry(self):
        coil = self.extracted.coil
        self.assertEqual(coil.turns, 4)
        self.assertAlmostEqual(coil.outer_width_mm, 40.0, places=6)
        self.assertAlmostEqual(coil.trace_width_mm, 0.4, places=6)
        self.assertAlmostEqual(coil.spacing_mm, 0.3, places=6)
        self.assertAlmostEqual(self.extracted.raw_trace_length_mm, 602.6560, places=3)
        self.assertEqual(coil.metadata["antenna_layer"], "F.Cu with one B.Cu crossover")

    def test_independent_inductance_recalculation(self):
        electrical = estimate_electrical(self.extracted.coil)
        self.assertGreater(electrical.inductance_h * 1e6, 1.4)
        self.assertLess(electrical.inductance_h * 1e6, 1.9)
        self.assertGreater(electrical.q_air, 50)
        self.assertLess(electrical.q_air, 180)

    def test_field_and_mutual_decrease_with_distance(self):
        coil = self.extracted.coil
        near_h = abs(magnetic_field(coil, (0, 0, 5))[2])
        far_h = abs(magnetic_field(coil, (0, 0, 40))[2])
        self.assertGreater(near_h, far_h)
        phone = __import__("nfc_model.geometry", fromlist=["phone_case"]).phone_case("Phone-M")
        near_m = mutual_inductance(coil, phone, separation_mm=5, quadrature_order=4)
        far_m = mutual_inductance(coil, phone, separation_mm=40, quadrature_order=4)
        self.assertGreater(near_m, far_m)


class EnvironmentTests(unittest.TestCase):
    def test_copper_skin_depth(self):
        self.assertAlmostEqual(skin_depth(13.56e6, 5.8e7) * 1e6, 17.95, delta=0.2)

    def test_free_air_is_reference(self):
        result = simulate(get_scenario("FREE_AIR"))
        self.assertAlmostEqual(result.coupling["relative_coupling"], 1.0, places=8)
        self.assertAlmostEqual(result.margin["nfc_margin_proxy_db"], 0.0, places=6)
        self.assertIsNone(result.margin["success_probability"])

    def test_ferrite_reduces_metal_coupling_penalty(self):
        bare = simulate(get_scenario("ZETLAND_LOCK_NEAR"))
        ferrite = simulate(get_scenario("ZETLAND_LOCK_FERRITE_05"))
        self.assertGreater(ferrite.coupling["relative_coupling"], bare.coupling["relative_coupling"])

    def test_matching_direction_tracks_inductance(self):
        high_l = installed_matching(inductance_air_h=1.5e-6, inductance_installed_h=1.65e-6,
                                    resistance_installed_ohm=1.5)
        low_l = installed_matching(inductance_air_h=1.5e-6, inductance_installed_h=1.35e-6,
                                   resistance_installed_ohm=1.5)
        self.assertLess(high_l.f0_installed_hz, 13.56e6)
        self.assertLess(high_l.series_trim_delta_each_pf, 0)
        self.assertGreater(low_l.f0_installed_hz, 13.56e6)
        self.assertGreater(low_l.series_trim_delta_each_pf, 0)

    def test_acceptance_case_keeps_success_uncalibrated(self):
        base = get_scenario("ZETLAND_LOCK_FERRITE_05")
        result = simulate(base.with_updates(offset_x_mm=10.0, tilt_y_deg=10.0))
        self.assertGreater(result.antenna_installed["L_uH"], result.antenna_air["L_uH"])
        self.assertTrue(result.matching["retune_recommended"])
        self.assertIsNone(result.margin["success_probability"])

    def test_vna_summary_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vna.csv"
            path.write_text(
                "test,f0_MHz,Q,L_uH,S11_min_dB\n"
                "air,13.56,20,1.60,-18\n"
                "installed,12.90,15,1.82,-8\n",
                encoding="utf-8",
            )
            result = calibrate_vna(path)
        installed = result["normalized_measurements"][1]
        self.assertAlmostEqual(installed["L_ratio_to_air"], 1.1375)
        self.assertAlmostEqual(installed["Q_ratio_to_air"], 0.75)


if __name__ == "__main__":
    unittest.main()
