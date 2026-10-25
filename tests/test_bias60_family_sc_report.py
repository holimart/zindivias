"""Per-idea evidence and prerequisite tests for family Sc."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SchemaCompletenessFamilyReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads((ROOT / "reports/sc-family-screen.json").read_text())
        cls.registry = json.loads((ROOT / "config/ideas.json").read_text())
        cls.registry_by_id = {item["id"]: item for item in cls.registry["ideas"]}

    def test_each_sc_idea_is_measured_or_has_a_specific_missing_prerequisite(self) -> None:
        ideas = {item["id"]: item for item in self.report["ideas"]}
        self.assertEqual(set(ideas), {f"Sc{i}" for i in range(1, 6)})
        for idea_id, item in ideas.items():
            with self.subTest(idea=idea_id):
                if item["status"] == "computed":
                    self.assertEqual(item["rows"], 9379 * len(item["emissions"]))
                    self.assertEqual(item["official_rows"], 9379)
                    self.assertTrue(item["geoid_partition_exact"])
                    self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                    self.assertEqual(self.registry_by_id[idea_id]["status"], "implemented")
                else:
                    self.assertEqual(item["status"], "not_computed")
                    self.assertGreater(len(item["reason"]), 80)
                    self.assertGreater(len(item["missing_prerequisite"]), 20)
                    self.assertEqual(self.registry_by_id[idea_id]["status"], "declared")

    def test_sc1_to_sc4_are_computed_and_sc3_has_four_explicit_definitions(self) -> None:
        ideas = {item["id"]: item for item in self.report["ideas"]}
        for idea_id in ("Sc1", "Sc2", "Sc3", "Sc4"):
            self.assertEqual(ideas[idea_id]["status"], "computed")
            self.assertEqual(ideas[idea_id]["official_rows"], 9379)
            self.assertTrue(ideas[idea_id]["geoid_partition_exact"])
        self.assertEqual(ideas["Sc3"]["regions"]["eastern-ok"]["definitions"],
                         ["primary", "primary_alternate", "taxonomy_hierarchy", "basic_category"])
        self.assertIn("schema sensitivity proxy only",
                      ideas["Sc3"]["regions"]["eastern-ok"]["interpretation"])

    def test_sc5_measured_null_is_not_sold_as_a_positive_pipeline_gap(self) -> None:
        sc5 = next(item for item in self.report["ideas"] if item["id"] == "Sc5")
        self.assertEqual(sum(region["defined"] for region in sc5["regions"].values()), 9247)
        for region in sc5["regions"].values():
            self.assertEqual(region["named_length_km"]["roads"],
                             region["named_length_km"]["roads-unfiltered"])
            self.assertLessEqual(region["gap_distribution"]["max"], 1e-9)
            self.assertLessEqual(region["excluded_share_distribution"]["max"], 1e-9)
        self.assertIn("measured negative", sc5["measured_interpretation"])

    def test_orthogonality_refresh_retains_sc5_null_and_other_estimator_flags(self) -> None:
        orthogonality = self.report["orthogonality"]
        self.assertRegex(orthogonality["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(orthogonality["summary"]["report_sha256"], r"^[0-9a-f]{64}$")
        null_columns = {item["column"] for item in orthogonality["numerically_null_columns"]}
        self.assertIn("Sc5|R|gap", null_columns)
        self.assertIn("Sc5|R|covariate", null_columns)
        self.assertTrue(orthogonality["summary"]["all_passed"])

    def test_prohibitions_are_explicit(self) -> None:
        constraints = self.report["constraints"]
        self.assertTrue(constraints["provided_datasets_only"])
        self.assertFalse(constraints["labels_read"])
        self.assertFalse(constraints["reference_layers_read"])
        self.assertFalse(constraints["leaderboard_accessed"])
        self.assertEqual(constraints["uploads"], 0)


if __name__ == "__main__":
    unittest.main()
