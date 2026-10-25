"""G-family geometry feasibility records; unavailable geometry work is not misreported as measured."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GeometryFamilyScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads((ROOT / "reports/g-family-screen.json").read_text())
        cls.registry = json.loads((ROOT / "config/ideas.json").read_text())
        cls.dispositions = {item["id"]: item for item in cls.report["idea_dispositions"]}
        cls.ideas = {item["id"]: item for item in cls.registry["ideas"]}

    def test_each_geometry_idea_has_a_specific_reason_and_prerequisite(self) -> None:
        self.assertEqual(set(self.dispositions), {f"G{i}" for i in range(1, 6)})
        for idea_id, item in self.dispositions.items():
            with self.subTest(idea=idea_id):
                self.assertEqual(item["status"], "not_computed")
                self.assertGreater(len(item["reason"]), 100)
                self.assertGreater(len(item["missing_prerequisite"]), 20)
                self.assertEqual(self.ideas[idea_id]["status"], "declared")
                self.assertIsNone(self.ideas[idea_id]["implementation"])

    def test_no_geometry_idea_is_claimed_as_measured_or_correlated(self) -> None:
        self.assertEqual(self.report["scope"]["computed"], [])
        self.assertIsNone(self.report["scope"]["coverage"])
        self.assertEqual(self.report["orthogonality"]["new_g_columns"], 0)
        self.assertIn("No G correlation or VIF is claimed",
                      self.report["orthogonality"]["interpretation"])

    def test_only_schema_was_inspected_for_input_availability(self) -> None:
        observation = self.report["input_schema_observation"]
        self.assertIn("geometry", observation["roads_layer_columns"])
        self.assertIn("geometry", observation["buildings_layer_columns"])
        self.assertIn("not found", observation["places_layer"])
        self.assertEqual(self.report["constraints"]["study_tract_geometry_computations"], 0)

    def test_reference_layers_labels_leaderboard_and_uploads_were_not_used(self) -> None:
        constraints = self.report["constraints"]
        self.assertTrue(constraints["provided_datasets_only"])
        self.assertFalse(constraints["labels_read"])
        self.assertFalse(constraints["reference_layers_read"])
        self.assertFalse(constraints["leaderboard_accessed"])
        self.assertEqual(constraints["uploads"], 0)

    def test_g3_reason_preserves_the_microsoft_reference_prohibition(self) -> None:
        reason = self.dispositions["G3"]["reason"].lower()
        self.assertIn("microsoft", reason)
        self.assertIn("forbidden", reason)
        self.assertIn("reference copy", reason)


if __name__ == "__main__":
    unittest.main()
