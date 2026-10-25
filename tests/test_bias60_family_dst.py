"""Dst family feasibility reasons are explicit and do not masquerade as computed estimators."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DstFamilyScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads((ROOT / "reports/dst-family-screen.json").read_text())
        cls.registry = json.loads((ROOT / "config/ideas.json").read_text())
        cls.by_id = {entry["id"]: entry for entry in cls.report["idea_dispositions"]}
        cls.registry_by_id = {entry["id"]: entry for entry in cls.registry["ideas"]}

    def test_five_ideas_each_have_a_specific_documented_prerequisite(self) -> None:
        self.assertEqual(set(self.by_id), {f"Dst{i}" for i in range(1, 6)})
        for idea_id, entry in self.by_id.items():
            with self.subTest(idea=idea_id):
                self.assertEqual(entry["status"], "not_computed")
                self.assertGreater(len(entry["reason"]), 100)
                self.assertGreater(len(entry["missing_prerequisite"]), 20)
                self.assertEqual(self.registry_by_id[idea_id]["status"], "declared")
                self.assertIsNone(self.registry_by_id[idea_id]["implementation"])

    def test_no_dsts_are_claimed_as_computed_or_correlated(self) -> None:
        self.assertEqual(self.report["scope"]["computed"], [])
        self.assertIsNone(self.report["scope"]["coverage"])
        self.assertEqual(self.report["orthogonality"]["new_dst_columns"], 0)
        self.assertIn("No Dst correlation or VIF is claimed",
                      self.report["orthogonality"]["interpretation"])

    def test_forbidden_evidence_sources_were_not_used(self) -> None:
        constraints = self.report["constraints"]
        self.assertTrue(constraints["provided_datasets_only"])
        self.assertFalse(constraints["labels_read"])
        self.assertFalse(constraints["reference_layers_read"])
        self.assertFalse(constraints["leaderboard_moments_used"])
        self.assertEqual(constraints["uploads"], 0)

    def test_dst5_uses_only_the_documented_aggregate_path_and_is_not_fabricated(self) -> None:
        reason = self.by_id["Dst5"]["reason"].lower()
        self.assertIn("published aggregates", reason)
        self.assertIn("leaderboard moments", reason)
        self.assertIn("per-tract score", reason)


if __name__ == "__main__":
    unittest.main()
