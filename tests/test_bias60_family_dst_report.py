"""Audit assertions for Dst1-Dst5 measured outputs and organizer-only calibration."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DistributionalShapeReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads((ROOT / "reports/dst-family-screen.json").read_text())
        registry = json.loads((ROOT / "config/ideas.json").read_text())
        cls.registry = {idea["id"]: idea for idea in registry["ideas"]}

    def test_all_five_ideas_have_per_tract_artifacts_and_registry_entries(self) -> None:
        self.assertEqual(self.report["status"], "all_five_computed")
        self.assertEqual(set(self.report["ideas"]), {f"Dst{i}" for i in range(1, 6)})
        for idea_id, item in self.report["ideas"].items():
            with self.subTest(idea=idea_id):
                self.assertEqual(item["status"], "computed")
                self.assertEqual(item["rows"], 9379)
                self.assertEqual(item["official_rows"], 9379)
                self.assertTrue(item["geoid_partition_exact"])
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(self.registry[idea_id]["status"], "implemented")

    def test_dst1_good_turing_and_chao_summary_is_present(self) -> None:
        dst1 = self.report["ideas"]["Dst1"]
        self.assertEqual(sum(r["defined"] for r in dst1["regions"].values()), 9377)
        self.assertTrue(dst1["regions"]["maricopa-az"]["key_diagnostics"]["chao1_defined"] > 0)
        self.assertIsNotNone(dst1["correlation_with_existing_ideas"])
        self.assertIsNotNone(dst1["vif"])

    def test_dst2_zero_capture_overlap_is_explicit_not_filled(self) -> None:
        dst2 = self.report["ideas"]["Dst2"]
        self.assertEqual(sum(r["defined"] for r in dst2["regions"].values()), 0)
        self.assertIsNone(dst2["correlation_with_existing_ideas"])
        self.assertIsNone(dst2["vif"])
        self.assertIn("zero overlap", dst2["measured_limitation"])
        for region in dst2["regions"].values():
            self.assertEqual(region["key_diagnostics"]["capture_counts"]["overlap"], 0)
            self.assertEqual(region["key_diagnostics"]["max_county_overlap"], 0)

    def test_dst3_never_uses_microsoft_and_reports_left_tail_distribution(self) -> None:
        dst3 = self.report["ideas"]["Dst3"]
        self.assertEqual(sum(r["defined"] for r in dst3["regions"].values()), 9376)
        self.assertTrue(all(r["gap_distribution"] is not None for r in dst3["regions"].values()))
        self.assertFalse(self.report["constraints"]["microsoft_building_layer_used"])

    def test_dst4_and_dst5_cover_defined_rows_and_published_aggregate(self) -> None:
        dst4, dst5 = self.report["ideas"]["Dst4"], self.report["ideas"]["Dst5"]
        self.assertEqual(sum(r["defined"] for r in dst4["regions"].values()), 9258)
        self.assertEqual(sum(r["defined"] for r in dst5["regions"].values()), 9258)
        for region in dst4["regions"].values():
            self.assertEqual(region["key_diagnostics"]["source_ideas"],
                             ["Dst1", "Dst2", "Dst3", "Mx4", "P2", "P4", "Sc5", "Sca1", "Tm1"])
        for region, detail in dst5["regions"].items():
            with self.subTest(region=region):
                self.assertAlmostEqual(detail["key_diagnostics"]["calibrated_mean"],
                                       detail["key_diagnostics"]["target_undefined_share"], places=10)
        self.assertIn("organizer-published", self.report["dst5_calibration_rule"])

    def test_orthogonality_reports_full_matrix_and_flags(self) -> None:
        orth = self.report["orthogonality"]
        self.assertTrue(orth["summary"]["all_passed"])
        self.assertEqual(orth["summary"]["columns_evaluated"], 14)
        self.assertEqual(orth["summary"]["pairs_flagged"], 2)
        for idea_id in ("Dst1", "Dst3", "Dst4", "Dst5"):
            with self.subTest(idea=idea_id):
                self.assertEqual(len(self.report["ideas"][idea_id]["correlation_with_existing_ideas"]), 11)
        self.assertEqual(len(orth["excluded_low_coverage_columns"]), 2)

    def test_forbidden_sources_are_explicitly_absent(self) -> None:
        constraints = self.report["constraints"]
        self.assertTrue(constraints["provided_datasets_only"])
        self.assertFalse(constraints["labels_read"])
        self.assertFalse(constraints["removed_reference_layers_read_or_joined"])
        self.assertFalse(constraints["microsoft_building_layer_used"])
        self.assertFalse(constraints["foursquare_membership_used"])
        self.assertFalse(constraints["leaderboard_accessed"])
        self.assertEqual(constraints["uploads"], 0)


if __name__ == "__main__":
    unittest.main()
