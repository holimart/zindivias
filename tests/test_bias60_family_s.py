"""Synthetic, label-free tests for Family S1-S5."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60.ideas import IMPLEMENTATIONS  # noqa: E402
from bias60.ideas import s as family_s  # noqa: E402


class Column:
    def __init__(self, values):
        self.values = values

    def to_pylist(self):
        return self.values


class Batch:
    def __init__(self, rows):
        self.rows = rows

    def column(self, name):
        return Column([row.get(name) for row in self.rows])

    def __len__(self):
        return len(self.rows)


class FakeInputs:
    def __init__(self, *, region="maricopa-az", geoids=None, polygons=None, config=None,
                 emissions=None, features=None, strata=None, layers=None, workspace=None,
                 idea_output_dir=None):
        self.region = region
        self.geoids = geoids or ["00001", "00002", "00003"]
        self.polygons = np.asarray(polygons if polygons is not None else [
            shapely.box(-1, -1, 0, 1), shapely.box(0, -1, 1, 1), shapely.box(1, -1, 2, 1)
        ], dtype=object)
        self.config = config or {"vocabulary": {"components": ["R", "B", "F", "Est", "C", "all"],
                                                "gap_bounds": [0.0, 1.0]}, "seeds": {}}
        self.emissions = emissions or {}
        self.feature_values = features or {}
        self.strata_values = strata or {}
        self.layers = layers or {}
        self.workspace = Path(workspace or ".")
        self.idea_output_dir = Path(idea_output_dir or workspace or ".")
        self.accessed = {"parquet_columns": [], "csv_columns": [], "layers": [], "caches": [],
                         "idea_artifacts": []}

    def tract_geometries(self):
        return self.polygons

    @staticmethod
    def _require_openable(path):
        return None

    def idea_emissions(self, idea_ids):
        return {idea: self.emissions[idea] for idea in idea_ids}

    def features(self, columns):
        return {column: np.asarray(self.feature_values[column]) for column in columns}

    def strata(self, columns):
        return {column: np.asarray(self.strata_values[column]) for column in columns}

    def iter_layer_batches(self, layer, columns):
        self.accessed["layers"].append(layer)
        self.accessed["parquet_columns"].extend(f"{self.region}-overture-{layer}.parquet:{c}" for c in columns)
        yield Batch(self.layers[layer])


def emission(output, component, channel):
    return next(item for item in output.emissions
                if item.component == component and item.channel == channel)


def test_s1_emits_self_excluded_queen_lags_and_moran_diagnostic():
    config = {"__registry__": {"ideas": [{"id": "D1", "status": "implemented"}]}}
    values = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    inputs = FakeInputs(config=config, emissions={"D1": {("R", "gap"): (values, np.ones(3, bool))}})
    output = IMPLEMENTATIONS["S1"](inputs)
    lag = emission(output, "R", "covariate:lag_d1_r_gap")
    assert np.allclose(lag.values, [1.0, 0.0, 1.0])
    assert lag.defined.tolist() == [True, True, True]
    assert np.isclose(output.diagnostics["moran_i_by_source_channel"]["D1|R|gap"], -1.0)


def test_s2_county_empirical_bayes_uses_only_count_and_county_context():
    config = {"seeds": {"S2": {"count_columns": ["overture_buildings", "overture_poi_all"],
                                  "county_columns": ["STATEFP", "COUNTYFP"]}}}
    inputs = FakeInputs(config=config,
        features={"overture_buildings": [1, 9, 4], "overture_poi_all": [2, 8, 5]},
        strata={"STATEFP": ["04", "04", "04"], "COUNTYFP": ["001", "001", "003"]})
    output = IMPLEMENTATIONS["S2"](inputs)
    buildings = emission(output, "all", "covariate:county_eb_buildings")
    assert buildings.defined.tolist() == [True, True, True]
    assert buildings.values[0] > 1.0 and buildings.values[0] < 5.0
    assert buildings.values[1] < 9.0 and buildings.values[1] > 5.0
    assert np.isclose(buildings.values[2], 4.0)


def test_s3_detects_a_provided_facility_across_a_shared_boundary():
    fire = {"primary": "fire_department", "alternate": []}
    point = shapely.Point(-0.001, 0).wkb
    config = {"seeds": {"S3": {
        "poi_layer": "pois", "poi_columns": ["basic_category", "categories", "taxonomy", "geometry"],
        "types": ["fire", "ems", "schools"], "definition": "primary",
        "category_sets": {"fire": ["fire_department"], "ems": ["ambulance_and_ems_services"],
                           "schools": ["school"]}, "distance_m": 150.0, "decay_m": 50.0
    }}}
    inputs = FakeInputs(geoids=["l", "r"], polygons=[shapely.box(-1, -1, 0, 1), shapely.box(0, -1, 1, 1)],
                        config=config, layers={"pois": [{"basic_category": None, "categories": fire,
                                                         "taxonomy": None, "geometry": point}]})
    output = IMPLEMENTATIONS["S3"](inputs)
    leak = emission(output, "F", "covariate:boundary_leak_fire")
    assert leak.defined.tolist() == [True, True]
    assert leak.values[0] == 0.0
    assert 0.0 < leak.values[1] <= 1.0


def test_s4_peer_difference_uses_only_other_study_regions(monkeypatch, tmp_path):
    regions = ["maricopa-az", "northern-ca", "eastern-ok", "south-central-tx"]
    geoids = {region: [f"{region}-0", f"{region}-1"] for region in regions}
    pairs = [(geoid, region) for region in regions for geoid in geoids[region]]
    monkeypatch.setattr(family_s, "official_scored_pairs", lambda workspace: pairs)
    fixtures = {}
    for i, region in enumerate(regions):
        fixtures[region] = {
            "features": {"overture_buildings": np.array([10 + i, 20 + i], dtype=float),
                         "overture_named_road_km_5070": np.array([2 + i, 4 + i], dtype=float)},
            "strata": {"ALAND": np.array([1e6, 2e6]), "pop_total": np.array([100 + i, 200 + i]),
                       "ruca_primary": np.array([1, 2], dtype=float)},
        }
    class FakeSibling:
        def __init__(self, config, workspace, region, geoid_list):
            self.region, self.geoids = region, geoid_list
            self.accessed = {"parquet_columns": [], "csv_columns": [], "layers": [], "caches": []}
        def features(self, cols): return {key: fixtures[self.region]["features"][key] for key in cols}
        def strata(self, cols): return {key: fixtures[self.region]["strata"][key] for key in cols}
    monkeypatch.setattr(family_s, "ProvidedInputs", FakeSibling)
    table = pa.table({
        "GEOID": [g for r in regions for g in geoids[r]],
        "region": [r for r in regions for _ in geoids[r]],
        "idea": ["D1"] * 8,
        "component": ["R"] * 8,
        "channel": ["gap"] * 8,
        "value": [0.2, 0.4, 0.1, 0.3, 0.4, 0.6, 0.5, 0.7],
        "defined": [True] * 8,
    })
    artifact_dir = tmp_path / "ideas"
    artifact_dir.mkdir()
    pq.write_table(table, artifact_dir / "D1.parquet")
    config = {"regions": regions,
              "seeds": {"S4": {"peer_k": 1, "feature_columns": ["overture_buildings", "overture_named_road_km_5070"],
                                  "strata_columns": ["ALAND", "pop_total", "ruca_primary"]}},
              "__registry__": {"ideas": [{"id": "D1", "status": "implemented"}]}}
    inputs = FakeInputs(region=regions[0], geoids=geoids[regions[0]], config=config,
                        workspace=tmp_path, idea_output_dir=artifact_dir)
    output = IMPLEMENTATIONS["S4"](inputs)
    peer = emission(output, "R", "covariate:peer_gap_delta")
    assert peer.defined.tolist() == [True, True]
    assert np.all(np.isfinite(peer.values))


def test_s5_emits_local_geary_gap_disagreement_and_ignores_undefined_values():
    config = {"__registry__": {"ideas": [{"id": "D1", "status": "implemented"}]}}
    values = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float64)
    mask = np.array([True, True, True, False], dtype=np.bool_)
    polygons = [shapely.box(-1, -1, 0, 1), shapely.box(0, -1, 1, 1),
                shapely.box(1, -1, 2, 1), shapely.box(2, -1, 3, 1)]
    inputs = FakeInputs(geoids=["a", "b", "c", "d"], polygons=polygons, config=config,
                        emissions={"D1": {("R", "gap"): (values, mask)}})
    output = IMPLEMENTATIONS["S5"](inputs)
    disagreement = emission(output, "C", "covariate:local_geary_gap")
    assert disagreement.defined.tolist() == [True, True, True, False]
    assert disagreement.values[0] > 0.0 and disagreement.values[1] > 0.0
    assert disagreement.values[2] > 0.0 and disagreement.values[3] == 0.0


def test_family_s_registration_and_fixed_method_scope():
    assert {f"S{i}" for i in range(1, 6)} <= set(IMPLEMENTATIONS)
    assert family_s.SPATIAL_IDS == {"S1", "S2", "S3", "S4", "S5"}
