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
                                   "outside_named_share_threshold": 0.70}}}

    def tract_geometries(self):
        return np.asarray([shapely.box(-112, 32, -111, 33),
                           shapely.box(-111, 32, -110, 33)], dtype=object)


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


def test_family_t_registration_is_explicitly_partial():
    assert "T1" in IMPLEMENTATIONS
    assert family_t.TOPOLOGY_IDS == {f"T{i}" for i in range(1, 6)}
    assert not {"T2", "T3", "T4", "T5"} & set(IMPLEMENTATIONS)
