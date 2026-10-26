from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import shapely
from shapely.geometry import LineString, Polygon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60.ideas.mx import mx1, mx2, mx3, mx4, mx5  # noqa: E402

CONFIG = json.loads((ROOT / "config/bias60-harness-preregistered.json").read_text())


class FakeInputs:
    def __init__(self, batches=None, *, geoids=None, polygons=None, config=None, features=None, strata=None):
        self.config = config or CONFIG
        self.region = "synthetic"
        self.geoids = list(geoids or ["00000000001"])
        self._batches = batches or {}
        self._polygons = np.asarray(polygons or [Polygon([(-100, 40), (-99.9, 40), (-99.9, 40.1), (-100, 40.1)])], dtype=object)
        self._features = features or {}
        self._strata = strata or {}

    def tract_geometries(self):
        return self._polygons

    def iter_layer_batches(self, layer, columns):
        yield from self._batches.get(layer, [])

    def features(self, columns):
        return {column: self._features[column] for column in columns}

    def strata(self, columns):
        return {column: self._strata[column] for column in columns}


def table(rows):
    return pa.Table.from_pylist(rows)


def bbox(x=-99.95, y=40.05):
    return {"xmin": x - 0.001, "xmax": x + 0.001, "ymin": y - 0.001, "ymax": y + 0.001}


def em(output, component, channel):
    return next(row for row in output.emissions if row.component == component and row.channel == channel)


def test_mx1_tomtom_length_share_named_and_tertiary():
    roads = table([
        {"class": "primary", "sources": [{"dataset": "TomTom"}], "geometry": LineString([(-99.99, 40.02), (-99.91, 40.02)]).wkb},
        {"class": "primary", "sources": [{"dataset": "OpenStreetMap"}], "geometry": LineString([(-99.99, 40.04), (-99.91, 40.04)]).wkb},
        {"class": "tertiary", "sources": [{"dataset": "TomTom"}], "geometry": LineString([(-99.99, 40.06), (-99.91, 40.06)]).wkb},
    ])
    result = mx1(FakeInputs({"roads": [roads]}))
    named = em(result, "R", "covariate:tomtom_named_length_share")
    tertiary = em(result, "R", "covariate:tomtom_tertiary_length_share")
    assert named.defined.tolist() == [True]
    assert np.isclose(named.values[0], 0.5, atol=1e-3)
    assert tertiary.defined.tolist() == [True]
    assert np.isclose(tertiary.values[0], 1.0, atol=1e-6)


def test_mx2_microsoft_only_and_facility_name_shares():
    places = table([
        {"bbox": bbox(), "sources": [{"dataset": "Microsoft"}, {"dataset": "Overture"}], "names": {"primary": "Fire Station A"}},
        {"bbox": bbox(), "sources": [{"dataset": "Microsoft"}, {"dataset": "Meta"}, {"dataset": "Overture"}], "names": {"primary": "Main Street Shop"}},
        {"bbox": bbox(), "sources": [{"dataset": "Meta"}, {"dataset": "Overture"}], "names": {"primary": "Elementary School"}},
    ])
    result = mx2(FakeInputs({"pois": [places]}))
    all_places = em(result, "Est", "covariate:microsoft_only_place_share")
    facilities = em(result, "F", "covariate:facility_microsoft_only_place_share")
    assert np.isclose(all_places.values[0], 1 / 3)
    assert np.isclose(facilities.values[0], 1 / 2)


def test_mx3_foursquare_only_count_over_assigned_places():
    places = table([
        {"bbox": bbox(), "sources": [{"dataset": "Foursquare"}, {"dataset": "Overture"}]},
        {"bbox": bbox(), "sources": [{"dataset": "Meta"}, {"dataset": "Overture"}]},
        {"bbox": bbox(), "sources": [{"dataset": "Foursquare"}, {"dataset": "Microsoft"}, {"dataset": "Overture"}]},
    ])
    result = mx3(FakeInputs({"pois": [places]}))
    share = em(result, "Est", "covariate:foursquare_only_place_share")
    assert np.isclose(share.values[0], 1 / 3)


def test_mx4_uses_building_weighted_county_share_and_tract_share():
    cfg = json.loads(json.dumps(CONFIG))
    inputs = FakeInputs(
        geoids=["00000000001", "00000000002"],
        features={"overture_building_esri_source_share": np.array([0.25, 0.75]),
                  "overture_buildings": np.array([1.0, 3.0])},
        strata={"COUNTYFP": np.array(["001", "001"])},
        config=cfg,
    )
    result = mx4(inputs)
    county = em(result, "F", "covariate")
    tract = em(result, "B", "covariate")
    assert np.allclose(county.values, [0.625, 0.625])
    assert np.allclose(tract.values, [0.25, 0.75])


def test_mx5_flags_provider_specific_education_category_mismatch():
    places = table([
        {"bbox": bbox(), "sources": [{"dataset": "Microsoft"}, {"dataset": "Overture"}], "names": {"primary": "North School"},
         "categories": {"primary": "education"}},
        {"bbox": bbox(), "sources": [{"dataset": "Meta"}, {"dataset": "Overture"}], "names": {"primary": "West Elementary School"},
         "categories": {"primary": "elementary_school"}},
        {"bbox": bbox(), "sources": [{"dataset": "Foursquare"}, {"dataset": "Overture"}], "names": {"primary": "East Academy"},
         "categories": {"primary": "university"}},
    ])
    result = mx5(FakeInputs({"pois": [places]}))
    microsoft = em(result, "F", "covariate:school_category_mismatch_share_microsoft")
    meta = em(result, "F", "covariate:school_category_mismatch_share_meta")
    foursquare = em(result, "F", "covariate:school_category_mismatch_share_foursquare")
    assert microsoft.values[0] == 1.0
    assert meta.values[0] == 0.0
    assert foursquare.values[0] == 1.0
