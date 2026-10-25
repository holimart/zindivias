"""The P family report separates measured estimators from documented non-computations."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PhysicalFamilyReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads((ROOT / "reports/p-family-screen.json").read_text())
        cls.registry = json.loads((ROOT / "config/ideas.json").read_text())
        cls.registry_by_id = {item["id"]: item for item in cls.registry["ideas"]}

    def test_all_five_ideas_are_computed_or_have_a_specific_reason(self) -> None:
        ideas = {item["id"]: item for item in self.report["ideas"]}
        self.assertEqual(set(ideas), {f"P{i}" for i in range(1, 6)})
        for idea_id, item in ideas.items():
            with self.subTest(idea=idea_id):
                if item["status"] == "computed":
                    self.assertEqual(item["rows"], 9379)
                    self.assertEqual(item["official_rows"], 9379)
                    self.assertTrue(item["geoid_partition_exact"])
                    self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                    self.assertEqual(self.registry_by_id[idea_id]["status"], "implemented")
                else:
                    self.assertEqual(item["status"], "blocked_by_rule")
                    self.assertGreater(len(item["reason"]), 80)
                    self.assertGreater(len(item["missing_prerequisite"]), 20)
                    self.assertGreater(len(item["rule_basis"]), 30)
                    self.assertEqual(self.registry_by_id[idea_id]["status"], "declared")

    def test_p2_and_p4_outputs_keep_definedness_denominators_honest(self) -> None:
        ideas = {item["id"]: item for item in self.report["ideas"]}
        p2 = ideas["P2"]
        p4 = ideas["P4"]
        self.assertEqual(p2["rows"], 9379)
        self.assertEqual(sum(r["defined"] for r in p2["regions"].values()), 9377)
        self.assertEqual(p4["rows"], 9379)
        self.assertEqual(sum(r["defined"] for r in p4["regions"].values()), 639)
        self.assertEqual(p4["regions"]["maricopa-az"]["defined"], 0)
        self.assertEqual(p4["regions"]["northern-ca"]["defined"], 0)

    def test_orthogonality_has_both_new_columns_and_flags_instead_of_dropping(self) -> None:
        ortho = self.report["orthogonality"]
        self.assertIn("P1|Est|gap", ortho["g_family_columns_included"])
        self.assertIn("P2|F|gap", ortho["g_family_columns_included"])
        self.assertIn("P3|F|covariate", ortho["g_family_columns_included"])
        self.assertIn("P4|R|gap", ortho["family_p_columns_excluded_by_coverage"])
        self.assertTrue(ortho["summary"]["all_passed"])
        self.assertGreater(ortho["summary"]["columns_included"], 0)
        self.assertTrue(ortho["pairs_exceeding_merge_threshold"])

    def test_prohibitions_are_explicit(self) -> None:
        constraints = self.report["constraints"]
        self.assertTrue(constraints["provided_datasets_only"])
        self.assertFalse(constraints["labels_read"])
        self.assertFalse(constraints["reference_layers_read"])
        self.assertFalse(constraints["leaderboard_accessed"])
        self.assertEqual(constraints["uploads"], 0)


if __name__ == "__main__":
    unittest.main()
