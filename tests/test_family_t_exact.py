from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "preflight_bias60_family_t_exact", ROOT / "scripts/preflight_bias60_family_t_exact.py")
assert spec and spec.loader
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def test_recipe_preflight_requires_the_frozen_inputs_without_claiming_conformance():
    roads = {"class", "names", "routes", "connectors", "subclass", "road_flags",
             "rail_flags", "level_rules", "geometry"}
    infrastructure = {"geometry", "subtype", "class"}
    tracts = {"GEOID", "geometry"}
    strata = {"GEOID", "pop_total"}
    assessment = preflight.recipe_input_assessment(roads, infrastructure, tracts, strata)
    assert all(assessment[f"T{i}"]["required_fields_present"] for i in range(1, 5))
    assert assessment["T4"]["centroid_geometry_note"].startswith(
        "The supplied ACS tract table exposes polygon geometry")


def test_missing_geometric_crossing_and_stroke_inputs_are_reported():
    assessment = preflight.recipe_input_assessment(
        {"class", "geometry"}, {"geometry"}, {"GEOID", "geometry"},
        {"GEOID", "pop_total"})
    assert assessment["T1"]["missing_columns"]["roads-unfiltered"] == [
        "connectors", "names", "routes"]
    assert assessment["T3"]["missing_columns"]["roads-unfiltered"] == [
        "connectors", "level_rules", "rail_flags", "road_flags"]


def test_preflight_keeps_all_four_v1_fidelity_gaps_explicit():
    config = __import__("json").loads(
        (ROOT / "config/bias60-harness-preregistered.json").read_text())
    assert set(preflight.KNOWN_V1_GAPS) == {"T1", "T2", "T3", "T4"}
    assert all(config["seeds"][idea] for idea in preflight.KNOWN_V1_GAPS)
    assert all("v1" in value for value in preflight.KNOWN_V1_GAPS.values())
