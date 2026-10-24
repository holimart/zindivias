"""Tests for disclosed-statistics-only route variant selection."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import audit_transport_route_variants as variants  # noqa: E402


def test_compare_variant_uses_route_undefined_and_v0_nontransport_flags():
    result = variants.compare_variant(
        route_values=np.array([0.0, 2.0]),
        named_values=np.array([5.0, 1.0]),
        v0_flags=[
            {"transport_defined": 1, "building_defined": 1, "poi_defined": 1},
            {"transport_defined": 1, "building_defined": 0, "poi_defined": 1},
        ],
        disclosed_any=0.5,
    )
    assert result["sum_L_named_over_sum_L_route"] == 3.0
    assert result["route_undefined_share"] == 0.5
    assert result["variant_any_component_undefined_share"] == 1.0
    assert result["any_undefined_abs_error"] == 0.5


def test_selection_prioritizes_ratio_band_coverage_after_any_share_gate():
    summaries = {}
    for variant in variants.VARIANTS:
        summaries[variant["id"]] = {
            region: {
                "any_undefined_within_0_10": True,
                "ratio_within_disclosed_0_71_to_1_59": False,
                "ratio_within_lane_0_60_to_1_70": False,
                "distance_to_disclosed_ratio_band": 1.0,
                "any_undefined_abs_error": 0.02,
            }
            for region in variants.REGIONS
        }
    summaries["state_us_interstate"]["maricopa-az"][
        "ratio_within_disclosed_0_71_to_1_59"] = True
    summaries["plus_fm_rm"]["maricopa-az"][
        "ratio_within_disclosed_0_71_to_1_59"] = True
    summaries["plus_fm_rm"]["northern-ca"][
        "ratio_within_disclosed_0_71_to_1_59"] = True
    for region in variants.REGIONS:
        summaries["plus_fm_rm_cr"][region][
            "ratio_within_disclosed_0_71_to_1_59"] = region != "maricopa-az"
    # Despite a slightly worse share error, the full variant fits more ratios.
    for region in variants.REGIONS:
        summaries["state_us_interstate"][region]["any_undefined_abs_error"] = 0.01
        summaries["plus_fm_rm_cr"][region]["any_undefined_abs_error"] = 0.05
    assert variants.select_variant(summaries)["chosen_variant"] == "plus_fm_rm_cr"


def test_compare_variant_rejects_invalid_lengths():
    with pytest.raises(ValueError, match="negative or non-finite"):
        variants.compare_variant(
            route_values=np.array([np.nan]), named_values=np.array([1.0]),
            v0_flags=[{"building_defined": 1, "poi_defined": 1}], disclosed_any=0.2,
        )
