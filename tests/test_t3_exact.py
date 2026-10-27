from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60.ideas.t3_exact import crossing_counts, t3_exact


def roads_fixture(*, shared: bool = False, rail_partner: bool = False):
    geoms = [shapely.LineString([(-.01, 0.), (.01, 0.)]),
             shapely.LineString([(0., -.01), (0., .01)])]
    connectors = [{"a", "junction"} if shared else {"a", "b"},
                  {"junction", "c"} if shared else {"c", "d"}]
    return geoms, ["tertiary", "residential"], connectors, [False, rail_partner], [False, True]


def test_t3_counts_only_unshared_geometric_crossings_with_eligible_road():
    tract = shapely.box(-.02, -.02, .02, .02)
    geoms, classes, connectors, rail, grade = roads_fixture()
    counts, diag, points, marked = crossing_counts(geoms, classes, connectors, rail, grade, [tract],
        eligible_classes={"tertiary", "unclassified"}, named_classes={"motorway", "trunk", "primary", "secondary"})
    assert counts == [1.]
    assert len(points) == 1 and marked == [True]
    assert diag["crossing_pairs_without_shared_connector"] == 1
    assert diag["official_tract_metadata_marked_crossing_assignments"] == 1


def test_t3_excludes_shared_connectors_rail_and_noneligible_road_pairs():
    tract = shapely.box(-.02, -.02, .02, .02)
    geoms, classes, connectors, rail, grade = roads_fixture(shared=True)
    assert crossing_counts(geoms, classes, connectors, rail, grade, [tract],
        eligible_classes={"tertiary", "unclassified"}, named_classes=set())[0] == [0.]
    geoms, classes, connectors, rail, grade = roads_fixture(rail_partner=True)
    assert crossing_counts(geoms, classes, connectors, rail, grade, [tract],
        eligible_classes={"tertiary", "unclassified"}, named_classes=set())[0] == [0.]
    assert crossing_counts(geoms, ["residential", "service"], connectors, [False, False], grade, [tract],
        eligible_classes={"tertiary", "unclassified"}, named_classes=set())[0] == [0.]


def test_t3_estimator_emits_crossing_density_and_reads_only_declared_layers(tmp_path):
    geoms, classes, connectors, rail, grade = roads_fixture()
    road_rows = []
    for geom, cls, conn, rail_flag, grade_flag in zip(geoms, classes, connectors, rail, grade):
        road_rows.append({
            "class": cls,
            "connectors": [{"connector_id": value, "at": i / max(len(conn) - 1, 1)}
                           for i, value in enumerate(sorted(conn))],
            "road_flags": [{"values": ["is_bridge"], "between": [0., 1.]}] if grade_flag else None,
            "rail_flags": [{"values": ["rail"], "between": [0., 1.]}] if rail_flag else None,
            "level_rules": [{"value": 1, "between": [0., 1.]}] if grade_flag else None,
            "geometry": shapely.to_wkb(geom),
        })
    road_path = tmp_path / "roads.parquet"
    pq.write_table(pa.Table.from_pylist(road_rows), road_path)

    class Inputs:
        region = "unit-test"
        geoids = ["00000000001"]
        config = {"seeds": {"T3": {
            "named_classes": ["motorway", "trunk", "primary", "secondary"],
            "eligible_classes": ["tertiary", "unclassified"],
            "grade_separation_flags": ["bridge", "is_bridge", "tunnel", "is_tunnel"],
            "area_column": "ALAND", "unfiltered_layer": "roads-unfiltered"}}}
        def iter_layer_batches(self, layer, columns):
            if layer == "roads-unfiltered":
                yield pq.read_table(road_path, columns=columns)
            elif layer == "infrastructure":
                yield pa.table({"class": pa.array([], type=pa.string()),
                                "subtype": pa.array([], type=pa.string()),
                                "geometry": pa.array([], type=pa.binary())})
            else:
                raise AssertionError(layer)
        def tract_geometries(self):
            return np.asarray([shapely.box(-.02, -.02, .02, .02)], dtype=object)
        def strata(self, columns):
            assert columns == ["ALAND"]
            return {"ALAND": np.array([1_000_000.])}

    output = t3_exact(Inputs())
    emission = output.emissions[0]
    assert output.idea_id == "T3-v2"
    assert emission.defined.tolist() == [True]
    assert emission.values.tolist() == [1.]
    assert output.diagnostics["crossing_pairs_without_shared_connector"] == 1
    assert output.diagnostics["infrastructure_rows_read"] == 0
