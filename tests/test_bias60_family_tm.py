from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.ideas.tm import _version_summaries, tm2, tm3, tm5


class ArrayInputs:
    region = "unit-test"
    geoids = ["00000000001", "00000000002", "00000000003", "00000000004"]
    config = {"seeds": {
        "Tm2": {"strata_columns": ["mtbs_covered", "mtbs_wildfire_burned_pct_land",
                                     "mtbs_wildfire_last_year", "usfs_covered",
                                     "usfs_BuildingCount_sum"],
                "current_column": "overture_buildings", "post_reference_year": 2018},
        "Tm3": {"strata_columns": ["usfs_covered", "usfs_HUCount_sum",
                                     "usfs_BuildingCount_sum", "usfs_PopCount_sum", "pop_total"],
                "current_building_column": "overture_buildings"},
    }}
    def __init__(self, strata, features, housing=None):
        self._strata, self._features = strata, features
        self._housing = housing
    def strata(self, columns):
        return {key: self._strata[key] for key in columns}
    def features(self, columns):
        return {key: self._features[key] for key in columns}
    def acs_housing_units(self):
        return self._housing


def test_tm2_postfire_loss_and_fallback_are_deterministic():
    inputs = ArrayInputs(
        {"mtbs_covered": np.ones(4, bool),
         "mtbs_wildfire_burned_pct_land": np.array([20., 10., 0., 25.]),
         "mtbs_wildfire_last_year": np.array([2020., 2017., np.nan, 2021.]),
         "usfs_covered": np.array([True, True, False, False]),
         "usfs_BuildingCount_sum": np.array([100., 100., 0., 0.])},
        {"overture_buildings": np.array([50., 90., 0., 0.])})
    output = tm2(inputs)
    building, facility = output.emissions
    assert building.component == "B" and building.channel == "gap"
    assert np.allclose(building.values, [0.1, 0.0, 0.0, 0.25])
    assert building.defined.tolist() == [True] * 4
    assert np.allclose(facility.values, [0.2, 0.0, 0.0, 0.25])
    assert output.diagnostics["fallback_rows"] == 1


def test_tm3_emits_separate_housing_building_and_population_growth_indices():
    inputs = ArrayInputs(
        {"usfs_covered": np.ones(4, bool), "usfs_HUCount_sum": np.array([50., 0., 25., 10.]),
         "usfs_BuildingCount_sum": np.array([100., 0., 50., 10.]),
         "usfs_PopCount_sum": np.array([50., 0., 100., 5.]),
         "pop_total": np.array([100., 100., 50., 5.])},
        {"overture_buildings": np.array([200., 10., 25., 5.])},
        housing=np.array([100., 10., 50., 15.]))
    output = tm3(inputs)
    by_key = {(e.component, e.channel): e for e in output.emissions}
    assert np.allclose(by_key[("B", "covariate:acs_housing_growth")].values, [1., 0., 1., 0.5])
    assert by_key[("B", "covariate:acs_housing_growth")].defined.tolist() == [True, False, True, True]
    assert np.allclose(by_key[("B", "covariate:overture_building_growth")].values,
                       [1., 0., -0.5, -0.5])
    assert by_key[("Est", "covariate:population_growth")].defined.tolist() == [True, False, True, True]


def test_tm1_and_tm4_remain_pending_without_using_carbonplan_as_an_active_feature():
    import json
    status = json.loads((ROOT / "config/bias60-family-tm-status.json").read_text())
    assert status["ideas"]["Tm1"]["status"] == "pending_policy_review"
    assert status["ideas"]["Tm4"]["status"] == "pending_policy_review"
    assert "carbonplan_buildings" in status["ideas"]["Tm1"]["reason"]
    assert "carbonplan_buildings" in status["ideas"]["Tm4"]["reason"]


def test_tm5_version_histogram_summary_uses_nearest_rank_p90_and_version_zero_share():
    means, p90, zero_share, defined = _version_summaries([
        {0: 1, 1: 1, 3: 8}, {}, {2: 2, 4: 2}, {0: 4}])
    assert np.allclose(means[[0, 2, 3]], [2.5, 3.0, 0.0])
    assert p90[[0, 2, 3]].tolist() == [3.0, 4.0, 0.0]
    assert np.allclose(zero_share[[0, 2, 3]], [0.1, 0.0, 1.0])
    assert defined.tolist() == [True, False, True, True]


def test_tm5_emits_theme_level_feature_version_metrics(tmp_path):
    import pyarrow as pa
    import shapely
    from bias60.ideas.tm import tm5

    metric = shapely.box(-1.0, -1.0, 1.0, 1.0)
    point = shapely.Point(0.0, 0.0)
    road = shapely.LineString([(-0.5, 0.0), (0.5, 0.0)])
    building = shapely.box(-0.1, -0.1, 0.1, 0.1)
    table_data = {
        "pois": pa.table({"version": [0, 2], "geometry": [shapely.to_wkb(point)] * 2}),
        "buildings": pa.table({"version": [1, 3], "geometry": [shapely.to_wkb(building)] * 2}),
        "roads": pa.table({"version": [0, 4], "geometry": [shapely.to_wkb(road)] * 2}),
    }
    class Inputs:
        region = "unit-test"
        geoids = ["00000000001"]
        config = {"seeds": {"Tm5": {"layers": ["pois", "buildings", "roads"],
                                      "columns": ["version", "geometry"]}}}
        def tract_geometries(self):
            return np.asarray([metric], dtype=object)
        def layer_path(self, layer):
            return f"synthetic/{layer}.parquet"
        def iter_layer_batches(self, layer, columns):
            assert columns == ["version", "geometry"]
            yield table_data[layer]
    output = tm5(Inputs())
    assert len(output.emissions) == 9
    assert {e.component for e in output.emissions} == {"Est", "F", "R"}
    assert all(e.defined.tolist() == [True] for e in output.emissions)
