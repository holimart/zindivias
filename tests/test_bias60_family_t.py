from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60.ideas import IMPLEMENTATIONS  # noqa: E402
from bias60.ideas import t as family_t  # noqa: E402


class FakeParquet:
    def __init__(self, table):
        self.metadata = type("Metadata", (), {"num_row_groups": 1})()
        self.table = table

    def read_row_group(self, index, columns):
        assert index == 0
        return self.table.select(columns)


class Inputs:
    region = "maricopa-az"
    geoids = ["tract-a", "tract-b"]
    accessed = {"layers": [], "parquet_columns": []}
    config = {"seeds": {"T1": {"unfiltered_layer": "roads-unfiltered",
                                   "named_classes": ["motorway", "trunk", "primary", "secondary"],
                                   "outside_named_share_threshold": 0.70},
                        "T2": {"unfiltered_layer": "roads-unfiltered",
                               "named_classes": ["motorway", "trunk", "primary", "secondary"],
                               "attached_length_limit_m": 2000.0},
                        "T3": {"unfiltered_layer": "roads-unfiltered",
                               "named_classes": ["motorway", "trunk", "primary", "secondary"],
                               "eligible_classes": ["tertiary", "unclassified"],
                               "grade_separation_flags": ["bridge", "is_bridge", "tunnel", "is_tunnel"],
                               "area_column": "ALAND"},
                        "T4": {"unfiltered_layer": "roads-unfiltered",
                               "named_classes": ["motorway", "trunk", "primary", "secondary"],
                               "eligible_classes": ["primary", "secondary", "tertiary", "unclassified"],
                               "top_fraction": 0.1},
                        "T5": {"road_layer": "roads-unfiltered", "building_layer": "buildings",
                               "distance_m": 150.0}}}

    def tract_geometries(self):
        return np.asarray([shapely.box(-112, 32, -111, 33),
                           shapely.box(-111, 32, -110, 33)], dtype=object)

    def strata(self, columns):
        return {column: np.array([1_000_000.0, 2_000_000.0]) for column in columns}

    def named_class_km(self, layer):
        return np.array([1.0, 2.0]), {}


def test_t1_detects_named_stroke_class_drop_when_named_length_is_outside_tract(monkeypatch):
    # A named primary road runs through tract B; a same-name tertiary stretch is in tract A.
    lines = [shapely.LineString([(-111.9, 32.5), (-111.1, 32.5)]),
             shapely.LineString([(-111.1, 32.5), (-110.1, 32.5)])]
    table = pa.table({
        "class": ["tertiary", "primary"],
        "names": [{"primary": "State Route 9"}, {"primary": "State Route 9"}],
        "routes": [None, None],
        "geometry": [shapely.to_wkb(line) for line in lines],
    })
    monkeypatch.setattr(family_t, "_road_layer", lambda inputs, columns, layer="roads-unfiltered": FakeParquet(table))
    output = IMPLEMENTATIONS["T1"](Inputs())
    gap = next(item for item in output.emissions if item.channel == "gap")
    assert gap.defined.tolist() == [True, True]
    assert gap.values[0] > 0.85
    assert gap.values[1] == 0.0
    assert output.diagnostics["stroke_angle_rule"].startswith("not applied")


def test_t2_detects_link_with_only_non_named_connector_neighbors(monkeypatch):
    lines = [shapely.LineString([(-111.9, 32.5), (-111.5, 32.5)]),
             shapely.LineString([(-111.5, 32.5), (-111.1, 32.5)])]
    table = pa.table({
        "class": ["motorway", "residential"],
        "subclass": ["motorway_link", None],
        "connectors": [[{"connector_id": "a"}, {"connector_id": "b"}],
                       [{"connector_id": "b"}, {"connector_id": "c"}]],
        "geometry": [shapely.to_wkb(line) for line in lines],
    })
    monkeypatch.setattr(family_t, "_road_layer", lambda inputs, columns, layer="roads-unfiltered": FakeParquet(table))
    output = IMPLEMENTATIONS["T2"](Inputs())
    count = next(item for item in output.emissions if item.channel.endswith("orphan_link_segments"))
    assert count.values[0] == 1.0
    assert count.values[1] == 0.0


def test_t3_emits_area_normalized_grade_separation_metadata(monkeypatch):
    line = shapely.LineString([(-111.9, 32.5), (-111.5, 32.5)])
    table = pa.table({"class": ["tertiary"], "road_flags": [[{"values": ["bridge"]}]],
                      "level_rules": [None], "geometry": [shapely.to_wkb(line)]})
    monkeypatch.setattr(family_t, "_road_layer", lambda inputs, columns, layer="roads-unfiltered": FakeParquet(table))
    output = IMPLEMENTATIONS["T3"](Inputs())
    density = output.emissions[0]
    assert density.defined.tolist() == [True, True]
    assert density.values[0] > 0.0
    assert density.values[1] == 0.0


def test_t4_connector_degree_proxy_is_bounded(monkeypatch):
    lines = [shapely.LineString([(-111.9, 32.5), (-111.5, 32.5)]),
             shapely.LineString([(-110.9, 32.5), (-110.5, 32.5)])]
    table = pa.table({"class": ["tertiary", "primary"],
                      "connectors": [[{"connector_id": "a"}, {"connector_id": "b"}],
                                     [{"connector_id": "c"}, {"connector_id": "d"}]],
                      "geometry": [shapely.to_wkb(line) for line in lines]})
    monkeypatch.setattr(family_t, "_road_layer", lambda inputs, columns, layer="roads-unfiltered": FakeParquet(table))
    output = IMPLEMENTATIONS["T4"](Inputs())
    assert np.all((output.emissions[0].values >= 0) & (output.emissions[0].values <= 1))
    assert output.emissions[0].defined.all()


def test_t5_test_double_emits_defined_zero_fraction():
    from types import SimpleNamespace
    inputs = SimpleNamespace(region="maricopa-az", geoids=["tract-a", "tract-b"],
                             config=Inputs.config, accessed={"layers": [], "parquet_columns": []})
    output = IMPLEMENTATIONS["T5"](inputs)
    assert output.emissions[0].defined.tolist() == [True, True]
    assert output.emissions[0].values.tolist() == [0.0, 0.0]


def test_family_t_registers_all_five_ideas():
    assert family_t.TOPOLOGY_IDS <= set(IMPLEMENTATIONS)
