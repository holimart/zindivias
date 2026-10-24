from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import audit_building_source_mix as source_mix  # noqa: E402
from estimator_v2_building import (  # noqa: E402
    BUILDING_GAP_V2,
    estimate_building_component_v2,
)


def test_v2_building_component_is_fixed_for_positive_overture_counts() -> None:
    assert BUILDING_GAP_V2 == 0.01
    assert estimate_building_component_v2(1) == {
        "building_gap": 0.01,
        "building_defined": 1,
    }
    assert estimate_building_component_v2(9876) == {
        "building_gap": 0.01,
        "building_defined": 1,
    }


def test_zero_overture_count_is_undefined_not_a_fabricated_gap() -> None:
    assert estimate_building_component_v2(0) == {
        "building_gap": 0.0,
        "building_defined": 0,
    }


@pytest.mark.parametrize("count", [-1, float("nan"), float("inf"), "not-a-count"])
def test_v2_building_rejects_invalid_overture_counts(count: object) -> None:
    with pytest.raises(ValueError):
        estimate_building_component_v2(count)  # type: ignore[arg-type]


def test_row_group_record_sample_is_seeded_unique_and_nonempty() -> None:
    selected, seed = source_mix.sample_row_indices("northern-ca", 4, 1000)
    selected_again, seed_again = source_mix.sample_row_indices("northern-ca", 4, 1000)
    assert selected == selected_again
    assert seed == seed_again
    assert len(selected) == 100
    assert len(selected) == len(set(selected))
    assert min(selected) >= 0 and max(selected) < 1000


def test_building_source_classes_keep_overlap_distinct_from_partition() -> None:
    names, cls = source_mix.classify_building_sources([
        {"dataset": "Microsoft ML Buildings"}, {"dataset": "OpenStreetMap"},
    ])
    assert names == {"microsoft ml buildings", "openstreetmap"}
    assert cls == "microsoft_osm"
    assert source_mix.classify_building_sources([{"dataset": "Esri Community Maps"}])[1] == "esri_only"
    assert source_mix.classify_building_sources([])[1] == "no_dataset"
    assert source_mix.classify_building_sources([{"dataset": "Microsoft ML Buildings"},
                                                 {"dataset": "Unknown Provider"}])[1] == "other_or_mixed"
