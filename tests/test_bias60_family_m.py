"""Synthetic, label-free tests for the metadata/provenance family M1-M5."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60.contracts import Emission, IdeaOutput  # noqa: E402
from bias60.ideas import IMPLEMENTATIONS  # noqa: E402
from bias60.ideas.m import _address_key  # noqa: E402


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
    def __init__(self, layers, housing=None):
        self.region = "test"
        self.geoids = ["00000000001", "00000000002"]
        self.config = {"vocabulary": {"components": ["R", "B", "F", "Est", "C", "all"],
                                      "gap_bounds": [0.0, 1.0]}}
        self.layers = layers
        self.housing = np.asarray(housing or [10.0, 20.0], dtype=np.float64)
        self.accessed = {"parquet_columns": [], "csv_columns": [], "layers": [], "caches": []}
        self.polygons = np.asarray([shapely.box(-1, -1, 0, 1), shapely.box(0, -1, 1, 1)], dtype=object)

    def iter_layer_batches(self, layer, columns):
        self.accessed["layers"].append(layer)
        for column in columns:
            self.accessed["parquet_columns"].append(f"test-overture-{layer}.parquet:{column}")
        yield Batch(self.layers.get(layer, []))

    def tract_geometries(self):
        return self.polygons

    def acs_housing_units(self):
        return self.housing.copy()


def bbox(x):
    return {"xmin": x - 0.01, "xmax": x + 0.01, "ymin": -0.01, "ymax": 0.01}


def emission(output: IdeaOutput, component: str, channel: str) -> Emission:
    return next(item for item in output.emissions
                if item.component == component and item.channel == channel)


def test_m1_parses_osm_way_versions_and_length_weights_tertiary_share():
    line_both = shapely.LineString([(-0.5, 0), (0.5, 0)]).wkb
    line_right = shapely.LineString([(0.25, 0.2), (0.75, 0.2)]).wkb
    rows = [
        {"class": "secondary", "sources": [{"dataset": "OpenStreetMap", "record_id": "w101@7"}], "geometry": line_both},
        {"class": "tertiary", "sources": [{"dataset": "OpenStreetMap", "record_id": "w102@2"}], "geometry": line_both},
        {"class": "tertiary", "sources": [{"dataset": "OpenStreetMap", "record_id": "w103@5"}], "geometry": line_right},
        {"class": "tertiary", "sources": [{"dataset": "Microsoft", "record_id": "w104@99"}], "geometry": line_right},
        {"class": "tertiary", "sources": [{"dataset": "OpenStreetMap", "record_id": "r105@8"}], "geometry": line_right},
    ]
    out = IMPLEMENTATIONS["M1"](FakeInputs({"roads": rows}))
    assert emission(out, "R", "covariate:named_mean_version").values.tolist() == [7.0, 7.0]
    low = emission(out, "R", "covariate:low_version_tertiary_share")
    assert np.allclose(low.values, [1.0, 0.5], atol=0.001)
    assert low.defined.tolist() == [True, True]
    assert out.diagnostics["non_way_osm_record_ids_not_used"] == 1


def test_m2_link_shares_use_place_brand_and_infrastructure_wikidata():
    layers = {
        "pois": [
            {"bbox": bbox(-0.5), "brand": {"wikidata": "Q1"}, "names": {"primary": "City Fire Station"}},
            {"bbox": bbox(-0.4), "brand": {"wikidata": None}, "names": {"primary": "Market"}},
        ],
        "infrastructure": [{"bbox": bbox(-0.3), "wikidata": "Q2"}],
    }
    out = IMPLEMENTATIONS["M2"](FakeInputs(layers))
    assert np.isclose(emission(out, "Est", "covariate:all_wikidata_link_share").values[0], 0.5)
    assert np.isclose(emission(out, "F", "covariate:facility_wikidata_link_share").values[0], 1.0)
    assert emission(out, "Est", "covariate:all_wikidata_link_share").defined.tolist() == [True, False]


def test_m3_prefers_any_valid_postcode_before_locality_fallback():
    assert _address_key([{"locality": "Alpha"}, {"postcode": "12345-6789", "locality": "Beta"}]) == "zip:12345"


def test_m3_excludes_neighbor_postcodes_from_mismatch_and_reports_facility_subset():
    rows = [
        {"bbox": bbox(-0.5), "addresses": [{"postcode": "11111", "locality": "Alpha"}], "names": {"primary": "Market"}},
        {"bbox": bbox(-0.5), "addresses": [{"postcode": "11111", "locality": "Alpha"}], "names": {"primary": "Shop"}},
        {"bbox": bbox(-0.4), "addresses": [{"postcode": "22222", "locality": "Beta"}], "names": {"primary": "School"}},
        {"bbox": bbox(-0.3), "addresses": [{"postcode": "33333", "locality": "Gamma"}], "names": {"primary": "Fire Station"}},
        {"bbox": bbox(0.5), "addresses": [{"postcode": "22222", "locality": "Beta"}], "names": {"primary": "Shop"}},
    ]
    out = IMPLEMENTATIONS["M3"](FakeInputs({"pois": rows}))
    all_places = emission(out, "Est", "covariate:address_mismatch_share")
    facility = emission(out, "F", "covariate:facility_address_mismatch_share")
    assert np.isclose(all_places.values[0], 0.25)  # only 33333 is foreign to local + adjacent codes
    assert np.isclose(facility.values[0], 0.5)
    assert out.diagnostics["neighbor_foreign_records_diagnostic"] >= 1


def test_m4_emits_chain_share_and_count_per_housing_unit():
    rows = [
        {"bbox": bbox(-0.5), "brand": {"wikidata": "Q1"}, "sources": [{"dataset": "AllThePlaces"}]},
        {"bbox": bbox(-0.4), "brand": None, "sources": [{"dataset": "OpenStreetMap"}]},
    ]
    out = IMPLEMENTATIONS["M4"](FakeInputs({"pois": rows}, housing=[10.0, 0.0]))
    share = emission(out, "Est", "covariate:chain_brand_share")
    density = emission(out, "Est", "covariate:chain_count_per_housing_unit")
    assert np.isclose(share.values[0], 0.5)
    assert np.isclose(density.values[0], 0.1)
    assert density.defined.tolist() == [True, False]
    assert out.diagnostics["no_housing_unit_denominator_floor_used"] is True


def test_m5_uses_distinct_nonempty_property_paths_for_buildings_and_roads():
    layers = {
        "buildings": [
            {"bbox": bbox(-0.5), "sources": [{"property": "/geometry"}, {"property": "/class"}]},
            {"bbox": bbox(-0.4), "sources": [{"property": "/geometry"}, {"property": "/geometry"}]},
        ],
        "roads": [
            {"bbox": bbox(0.5), "sources": [{"property": "/geometry"}, {"property": "/routes"}]},
            {"bbox": bbox(0.4), "sources": [{"property": ""}]},
        ],
    }
    out = IMPLEMENTATIONS["M5"](FakeInputs(layers))
    assert np.isclose(emission(out, "B", "covariate:multi_property_share").values[0], 0.5)
    assert np.isclose(emission(out, "R", "covariate:multi_property_share").values[1], 0.5)


def test_named_covariate_channels_are_valid_but_gap_suffixes_remain_bounded():
    config = {"vocabulary": {"components": ["R"], "gap_bounds": [0.0, 1.0]}}
    valid = Emission("R", "covariate:version_mean", np.array([3.0], dtype=np.float64),
                     np.array([True], dtype=np.bool_))
    valid.validate(n_rows=1, config=config)
    invalid = Emission("R", "gap:too_large", np.array([2.0], dtype=np.float64),
                       np.array([True], dtype=np.bool_))
    try:
        invalid.validate(n_rows=1, config=config)
    except ValueError as error:
        assert "outside" in str(error)
    else:
        raise AssertionError("namespaced gap channel bypassed the fixed [0,1] bound")
