from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import estimate_gaps as estimator  # noqa: E402
import discovery_source_provenance as discovery  # noqa: E402


def _sources(*names: str) -> list[dict[str, str]]:
    return [{"dataset": name} for name in names]


def test_building_source_classification_uses_any_osm_source() -> None:
    osm_ms = estimator.classify_building_sources(
        _sources("Microsoft ML Buildings", "OpenStreetMap"))
    assert osm_ms["osm"] and osm_ms["microsoft"]
    assert osm_ms["class"] == "microsoft_osm"
    assert estimator.classify_building_sources(_sources("OpenStreetMap"))["class"] == "osm_only"
    assert estimator.classify_building_sources(_sources("Microsoft ML Buildings"))["class"] == "microsoft_only"
    assert estimator.classify_building_sources(_sources("Esri Community Maps"))["class"] == "esri_only"
    assert estimator.classify_building_sources([])["class"] == "no_dataset"


def test_poi_source_classification_separates_upstream_from_overture_markers() -> None:
    both = estimator.classify_poi_sources(_sources("meta", "Microsoft", "Overture-signals"))
    assert both["meta"] and both["microsoft"]
    assert not both["other_upstream"]
    assert both["class"] == "meta_and_microsoft"
    other = estimator.classify_poi_sources(_sources("BrightQuery", "Overture"))
    assert other["other_upstream"] and other["class"] == "other_without_meta_microsoft"
    wrapper_only = estimator.classify_poi_sources(_sources("Overture", "Overture-signals"))
    assert not wrapper_only["other_upstream"]
    assert wrapper_only["class"] == "no_upstream"


def test_spearman_uses_average_ties_and_reverses_order() -> None:
    assert discovery.spearman([1, 2, 2, 4], [4, 3, 3, 1]) == {"n": 4, "rho": -1.0}


def test_bootstrap_difference_is_deterministic_and_directional() -> None:
    first = discovery.bootstrap_difference_ci([0.2, 0.4, 0.3], [0.5, 0.6, 0.7], seed=7)
    repeated = discovery.bootstrap_difference_ci([0.2, 0.4, 0.3], [0.5, 0.6, 0.7], seed=7)
    assert first == repeated
    assert first["difference"] < 0
    assert first["ci_excludes_zero"]


def test_source_mix_features_do_not_change_scored_estimator() -> None:
    features = {
        "overture_road_km": 2.0, "overture_buildings": 12,
        "overture_poi_all": 15, "overture_poi_fire": 1,
        "overture_poi_ems": 2, "overture_poi_schools": 3,
        "overture_poi_unclassified": 0,
        "overture_unclassified_fire_share": 0.01,
        "overture_unclassified_ems_share": 0.01,
        "overture_unclassified_schools_share": 0.02,
    }
    strata = {"ALAND": 100_000, "pop_total": 500, "pop_urban": 250, "pct_urban": 0.5}
    baseline = estimator.estimate_one(features, strata)
    enriched = {**features, **{name: 0.75 for name in estimator.SOURCE_MIX_FEATURE_COLUMNS}}
    assert estimator.estimate_one(enriched, strata) == baseline


def test_source_mix_merger_preserves_order_and_checks_denominators(tmp_path: Path) -> None:
    cache = tmp_path / "features.parquet"
    base = pa.table({
        "GEOID": ["a", "b"],
        "overture_buildings": [2, 0],
        "overture_poi_all": [1, 3],
    })
    pq.write_table(base, cache)
    features = {name: [0.25, None] for name in estimator.SOURCE_MIX_FEATURE_COLUMNS}
    row_counts = {
        "buildings": np.asarray([2, 0], dtype=np.int64),
        "pois": np.asarray([1, 3], dtype=np.int64),
    }
    result = estimator.merge_source_mix_features(cache, ["a", "b"], features, row_counts)
    extended = pq.read_table(cache)
    assert result["rows"] == 2
    assert extended.column_names == base.column_names + estimator.SOURCE_MIX_FEATURE_COLUMNS
    assert extended.column("overture_building_osm_source_share").to_pylist() == [0.25, None]
    first_hash = result["sha256"]
    second_result = estimator.merge_source_mix_features(cache, ["a", "b"], features, row_counts)
    assert second_result["sha256"] == first_hash

    cache_bad = tmp_path / "features-bad.parquet"
    pq.write_table(base, cache_bad)
    with pytest.raises(ValueError, match="counts differ"):
        estimator.merge_source_mix_features(
            cache_bad, ["a", "b"], features,
            {"buildings": np.asarray([1, 0]), "pois": np.asarray([1, 3])})
