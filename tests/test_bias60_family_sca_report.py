"""Measured evidence and blockers for Sca1-Sca5."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ScaleMathFamilyReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads((ROOT / "reports/sca-family-screen.json").read_text())
        cls.registry = json.loads((ROOT / "config/ideas.json").read_text())
        cls.registry_by_id = {item["id"]: item for item in cls.registry["ideas"]}

    def test_five_ideas_have_measurements_or_individual_reasons(self) -> None:
        ideas = {item["id"]: item for item in self.report["ideas"]}
        self.assertEqual(set(ideas), {f"Sca{i}" for i in range(1, 6)})
        for idea_id, item in ideas.items():
            with self.subTest(idea=idea_id):
                self.assertEqual(item["status"], "computed")
                self.assertEqual(item["official_rows"], 9379)
                self.assertGreaterEqual(item["rows"], 9379)
                self.assertEqual(item["rows"] % item["official_rows"], 0)
                self.assertTrue(item["geoid_partition_exact"])
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(self.registry_by_id[idea_id]["status"], "implemented")

    def test_scala1_definedness_and_full_study_partition(self) -> None:
        sca1 = next(item for item in self.report["ideas"] if item["id"] == "Sca1")
        self.assertEqual(sca1["rows"], 9379)
        self.assertEqual(sum(r["defined"] for r in sca1["regions"].values()), 9377)
        self.assertEqual(sca1["regions"]["south-central-tx"]["rows"], 6003)
        self.assertEqual(sca1["regions"]["south-central-tx"]["defined"], 6001)

    def test_high_correlation_is_reported_and_explicitly_grouped(self) -> None:
        redundancy = self.report["redundancy"]
        pair = redundancy["pairs_exceeding_abs_rho_0_8"]
        self.assertEqual(len(pair), 3)
        p2_sca1 = next(item for item in pair if {item["left"], item["right"]}
                       == {"P2|F|gap", "Sca1|F|gap"})
        self.assertAlmostEqual(p2_sca1["abs_rho"], 0.995801065928555)
        self.assertIn("no estimator is silently dropped", redundancy["merge_action"])

    def test_orthogonality_includes_scala1_and_reports_all_flags(self) -> None:
        ortho = self.report["orthogonality"]
        self.assertTrue(ortho["summary"]["all_passed"])
        self.assertEqual(ortho["summary"]["columns_evaluated"], 38)
        self.assertEqual(ortho["summary"]["columns_included"], 35)
        self.assertEqual(ortho["summary"]["pairs_flagged"], 3)
        self.assertEqual(len(ortho["all_flagged_pairs"]), 3)
        self.assertEqual(len(ortho["vif_flags"]), 13)

    def test_scala2_scala3_scala4_scala5_evidence_and_probability_inputs(self) -> None:
        ideas = {item["id"]: item for item in self.report["ideas"]}
        sca2 = ideas["Sca2"]
        self.assertTrue(all(region["diagnostics"]["count_conservation"]["tract_observed_sum"]
                            == region["diagnostics"]["count_conservation"]["county_observed_sum_once"]
                            for region in sca2["regions"].values()))
        sca3 = ideas["Sca3"]
        for region in sca3["regions"].values():
            conservation = region["diagnostics"]["count_conservation"]
            self.assertTrue(conservation["largest_overlap_building_conserved"])
            self.assertTrue(conservation["largest_overlap_poi_conserved"])
        self.assertEqual(ideas["Sca4"]["emissions"][0]["component"], "Est")
        sca5 = ideas["Sca5"]
        self.assertEqual(sca5["emissions"][0]["component"], "C")
        for region in sca5["regions"].values():
            probability_inputs = region["diagnostics"]["probability_inputs"]
            self.assertTrue(probability_inputs["P3_soft_count_used"])
            self.assertTrue(probability_inputs["X1_probability_used"])
            self.assertLessEqual(region["diagnostics"]["pattern_probability_mass"]["min"], 1.0)
            self.assertTrue(region["diagnostics"]["no_hard_definedness_flags"])

    def test_prohibitions_are_explicit(self) -> None:
        constraints = self.report["constraints"]
        self.assertTrue(constraints["provided_datasets_only"])
        self.assertFalse(constraints["labels_read"])
        self.assertFalse(constraints["reference_layers_read"])
        self.assertFalse(constraints["leaderboard_accessed"])
        self.assertEqual(constraints["uploads"], 0)


if __name__ == "__main__":
    unittest.main()
