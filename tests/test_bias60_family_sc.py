"""Semantic and parser fixtures for the provided-data Sc estimators."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.ideas.sc_schema import (  # noqa: E402
    _category_values, _facility_type, _is_ems_station, _is_fire_station, _is_school,
    _named_route, _semantic_type_match, _unknown_segment_selected, normalize_names,
)

CONFIG = json.loads((ROOT / "config/bias60-harness-preregistered.json").read_text())


class NamesAndRoadParserTests(unittest.TestCase):
    def test_names_normalize_null_empty_scalar_list_and_nested_records(self) -> None:
        self.assertEqual(normalize_names(None), ())
        self.assertEqual(normalize_names("  \t"), ())
        self.assertEqual(normalize_names("  US 101 "), ("US 101",))
        self.assertEqual(normalize_names([None, "", "Main St", {"value": "Route 9"}]),
                         ("Main St", "Route 9"))
        self.assertEqual(normalize_names({"primary": "", "common": {"en": "Oak Road"},
                                          "rules": [{"variant": "official", "value": "Oak Rd"}]}),
                         ("Oak Road", "Oak Rd"))
        self.assertEqual(normalize_names({"ref": "I 10", "between": [0.0, 0.5]}), ("I 10",))

    def test_route_name_parser_requires_semantic_route_value(self) -> None:
        self.assertTrue(_named_route([{"ref": "US 101", "network": "US:I"}]))
        self.assertTrue(_named_route({"name": "Old State Highway"}))
        self.assertFalse(_named_route(None))
        self.assertFalse(_named_route({"network": "US:I", "ref": "", "name": None}))

    def test_sc4_uses_unknown_class_and_strict_over_2km_or_route_name(self) -> None:
        seed = CONFIG["seeds"]["Sc4"]
        self.assertTrue(_unknown_segment_selected("unknown", None, None, 2000.01, seed))
        self.assertFalse(_unknown_segment_selected("unknown", None, None, 2000.0, seed))
        self.assertTrue(_unknown_segment_selected("road", None, [{"ref": "OK 9"}], 50.0, seed))
        self.assertTrue(_unknown_segment_selected("road", "Farm Road", None, 25.0, seed))
        self.assertFalse(_unknown_segment_selected("primary", None, [{"ref": "US 1"}], 5000.0, seed))


class SemanticPlaceFixtures(unittest.TestCase):
    def test_clear_facility_names_and_semantic_false_positives(self) -> None:
        self.assertTrue(_is_fire_station("Springfield Fire Department"))
        self.assertTrue(_is_fire_station("County Fire & Rescue"))
        self.assertFalse(_is_fire_station("Fire Station Grill"))
        self.assertFalse(_is_fire_station("Firehouse Subs"))
        self.assertTrue(_is_ems_station("County Ambulance Service"))
        self.assertFalse(_is_ems_station("Emergency Room at Memorial Hospital"))
        self.assertFalse(_is_ems_station("Urgent Care"))
        self.assertTrue(_is_school("Lincoln Elementary School"))
        self.assertFalse(_is_school("School of Rock"))
        self.assertFalse(_is_school("Driving School"))

    def test_sc2_categories_are_not_semantic_name_hits(self) -> None:
        self.assertEqual(_facility_type("Fire Station Grill"), None)
        self.assertEqual(_facility_type("Oak County Ambulance Station"), "ems")
        self.assertEqual(_facility_type("Maple Elementary School"), "schools")

    def test_sc3_four_independent_category_definitions(self) -> None:
        cats = _category_values("school", ["elementary_school"],
                                {"primary": "school", "hierarchy": ["education", "school"],
                                 "alternates": ["public_school"]}, "education")
        self.assertEqual(cats["primary"], {"school"})
        self.assertEqual(cats["primary_alternate"], {"school", "elementary_school"})
        self.assertEqual(cats["taxonomy_hierarchy"], {"education", "school", "public_school"})
        self.assertEqual(cats["basic_category"], {"education"})
        selected = CONFIG["seeds"]["Sc3"]["facility_category_sets"]
        self.assertTrue(_semantic_type_match("schools", cats["primary"], selected))
        self.assertFalse(_semantic_type_match("schools", cats["basic_category"], selected))
        self.assertTrue(_semantic_type_match("schools", cats["taxonomy_hierarchy"], selected))


if __name__ == "__main__":
    unittest.main()
