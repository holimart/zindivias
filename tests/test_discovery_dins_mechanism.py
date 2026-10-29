from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import shapely
from shapely import Point, box

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "discovery_dins_mechanism", ROOT / "scripts/discovery_dins_mechanism.py")
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_average_ranks_uses_midranks_for_ties():
    np.testing.assert_array_equal(module.average_ranks([2, 1, 1, 3]), [3, 1.5, 1.5, 4])


def test_spearman_handles_tied_values_and_perfect_rank_order():
    assert module.spearman([1, 1, 2, 3], [4, 4, 5, 6]) == 1.0
    assert module.spearman([1, 2, 3], [3, 2, 1]) == -1.0
    assert module.spearman([1, 1], [2, 2]) is None


def test_spatial_join_counts_damage_classes_and_tie_breaks_boundary():
    geos = ["001", "002"]
    polygons = [box(0, 0, 1, 1), box(1, 0, 2, 1)]
    geometries = {g: bytes(shapely.to_wkb(p)) for g, p in zip(geos, polygons)}
    features = [
        {"properties": {"DAMAGE": "Destroyed (>50%)"}, "geometry": {"coordinates": [0.5, 0.5]}},
        {"properties": {"DAMAGE": "Major (25-50%)"}, "geometry": {"coordinates": [1.5, 0.5]}},
        {"properties": {"DAMAGE": "No Damage"}, "geometry": {"coordinates": [1.0, 0.5]}},
        {"properties": {"DAMAGE": "Destroyed (>50%)"}, "geometry": {"coordinates": [3.0, 0.5]}},
    ]
    counts, audit = module.join_and_aggregate(features, geos, geometries)
    assert counts["001"]["Destroyed (>50%)"] == 1
    assert counts["001"]["No Damage"] == 1  # boundary assigned to smallest GEOID
    assert counts["002"]["Major (25-50%)"] == 1
    assert audit["matched_to_official_sample_tracts"] == 3
    assert audit["outside_official_sample_tracts"] == 1
    assert audit["points_intersecting_multiple_tracts_tie_broken_by_lexicographic_geoid"] == 1


def test_dins_mechanism_markdown_discloses_negative_result_and_source_provenance():
    report = {
        "external_source": {
            "downloaded_utc": "2026-10-29T07:12:08Z",
            "dataset_url": "https://data.ca.gov/dataset/cal-fire-damage-inspection-dins-data",
            "layer_url": "https://services1.arcgis.com/example/FeatureServer/0",
            "manifest_path": "/workspace/MANIFEST.json", "manifest_sha256": "a" * 64,
            "license": {"catalogue_license_title": "Creative Commons Attribution"},
        },
        "decision": {
            "verdict": "negative", "verdict_summary": "gate not met",
            "focal_relative_error_passes": 0, "spearman_all_2018_tracts": 0.4626,
            "n_all_2018_tracts": 28,
        },
        "dins_source": {"total_camp_fire_records": 10,
                        "matched_damage_counts": {"Destroyed (>50%)": 8,
                                                  "Major (25-50%)": 1,
                                                  "No Damage": 1},
                        "published_limitations": "not a complete census"},
        "spatial_join": {"matched_to_official_sample_tracts": 10},
        "focal_butte_tracts": [{"GEOID": "001", "usfs_minus_overture": 2.0,
                                "dins_destroyed": 1, "dins_major": 0,
                                "difference_over_destroyed": 2.0, "relative_error": 1.0}],
        "all_last_burn_2018_tracts": [{"GEOID": "001", "county_fips": "001",
                                       "usfs_minus_overture": 2.0, "dins_destroyed": 1,
                                       "dins_major": 0, "difference_over_destroyed": 2.0}],
        "scatter": {"sha256": "b" * 64},
        "opposite_direction": {"official_sample_tracts": 591,
                               "tracts_with_overture_and_zero_camp_fire_dins": 1,
                               "overture_buildings_in_those_tracts": 5},
    }
    text = module.make_markdown(report)
    assert "Result: NEGATIVE" in text
    assert "CC BY; version not stated" in text
    assert "not a complete post-fire census" in text
    assert "reports/discovery-dins-mechanism.json" in text
    assert "all 591 official NorCal tracts" in text
    assert "0/6" in text
