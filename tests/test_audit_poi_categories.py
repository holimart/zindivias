from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import shapely

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import audit_poi_categories as audit  # noqa: E402
import estimate_gaps  # noqa: E402


def test_estimator_uses_audited_category_config() -> None:
    assert estimate_gaps.FIRE_CATEGORIES == audit.CORE_SETS["fire"]
    assert estimate_gaps.EMS_CATEGORIES == audit.CORE_SETS["ems"]
    assert estimate_gaps.SCHOOL_CATEGORIES == audit.CORE_SETS["schools"]


def test_category_tags_normalize_and_deduplicate() -> None:
    assert audit.category_tags("Fire_Department", ["fire_department", "Rescue_Service"]) == {
        "fire_department", "rescue_service"
    }


def test_v0_and_broad_category_sets_are_explicit() -> None:
    observed = {
        "fire_department", "fire_protection_service", "fire_alarm_service",
        "ambulance_and_ems_services", "emergency_medicine", "school",
        "preschool", "dance_school", "driving_school", "hospital",
    }
    variants = audit._selected_variants(observed)
    assert variants["core_v0"]["fire"] == {"fire_department", "fire_protection_service"}
    assert variants["core_v0"]["ems"] == {"ambulance_and_ems_services"}
    assert "emergency_medicine" not in variants["core_v0"]["ems"]
    assert "emergency_medicine" in variants["ems_plus_emergency_medicine"]["ems"]
    assert "dance_school" not in variants["core_v0"]["schools"]
    assert "dance_school" in variants["schools_all_school_or_education_categories"]["schools"]
    assert "hospital" not in variants["core_v0"]["schools"]
    assert "day_care_preschool" in audit._selected_variants(
        observed | {"day_care_preschool"}
    )["schools_plus_day_care_preschool"]["schools"]


def test_name_values_support_map_and_rules_variants() -> None:
    assert set(audit.name_values({
        "primary": "Community School",
        "common": [("en", "Local Academy")],
        "rules": [{"value": "École"}],
    })) == {"Community School", "Local Academy", "École"}


def test_point_assignment_is_deterministic_on_boundaries() -> None:
    tracts = np.asarray([shapely.box(0, 0, 1, 1), shapely.box(1, 0, 2, 1)], dtype=object)
    tree = shapely.STRtree(tracts)
    assigned = audit.assign_points(tree, np.asarray([0.5, 1.5, 1.0]), np.asarray([0.5, 0.5, 0.5]))
    assert assigned.tolist() == [0, 1, 0]


def test_sample_reader_only_returns_geoid_field(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["GEOID", "coverage_gap_score", "component"])
        writer.writerow(["04001000100", "SCORE_PLACEHOLDER_DO_NOT_USE", "ignored"])
        writer.writerow(["04001000200", "also-ignored", "ignored"])
    geoids, digest = audit.read_sample_geoids(path)
    assert geoids == ["04001000100", "04001000200"]
    assert len(digest) == 64
