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
                if item["status"] == "computed":
                    self.assertEqual(item["rows"], 9379)
                    self.assertEqual(item["official_rows"], 9379)
                    self.assertTrue(item["geoid_partition_exact"])
                    self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                    self.assertEqual(self.registry_by_id[idea_id]["status"], "implemented")
                else:
                    self.assertEqual(item["status"], "not_computed")
                    self.assertGreater(len(item["reason"]), 80)
                    self.assertGreater(len(item["missing_prerequisite"]), 20)
                    self.assertEqual(self.registry_by_id[idea_id]["status"], "declared")

    def test_scala1_definedness_and_full_study_partition(self) -> None:
        sca1 = next(item for item in self.report["ideas"] if item["id"] == "Sca1")
        self.assertEqual(sca1["rows"], 9379)
        self.assertEqual(sum(r["defined"] for r in sca1["regions"].values()), 9377)
        self.assertEqual(sca1["regions"]["south-central-tx"]["rows"], 6003)
        self.assertEqual(sca1["regions"]["south-central-tx"]["defined"], 6001)

    def test_high_correlation_is_reported_and_explicitly_grouped(self) -> None:
        redundancy = self.report["redundancy"]
        pair = redundancy["pairs_exceeding_abs_rho_0_8"]
        self.assertEqual(len(pair), 1)
        self.assertEqual(pair[0]["left"], "P2|F|gap")
        self.assertEqual(pair[0]["right"], "Sca1|F|gap")
        self.assertAlmostEqual(pair[0]["abs_rho"], 0.995801065928555)
        self.assertIn("one correlated feature group", redundancy["merge_action"])
        self.assertIn("not silently dropped", redundancy["merge_action"])

    def test_orthogonality_includes_scala1_and_reports_all_flags(self) -> None:
        ortho = self.report["orthogonality"]
        self.assertTrue(ortho["summary"]["all_passed"])
        self.assertEqual(ortho["summary"]["columns_evaluated"], 9)
        self.assertEqual(ortho["summary"]["pairs_flagged"], 2)
        self.assertEqual(len(ortho["all_flagged_pairs"]), 2)
        self.assertEqual(len(ortho["vif_flags"]), 4)

    def test_prohibitions_are_explicit(self) -> None:
        constraints = self.report["constraints"]
        self.assertTrue(constraints["provided_datasets_only"])
        self.assertFalse(constraints["labels_read"])
        self.assertFalse(constraints["reference_layers_read"])
        self.assertFalse(constraints["leaderboard_accessed"])
        self.assertEqual(constraints["uploads"], 0)


if __name__ == "__main__":
    unittest.main()
