from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "preflight_bias60_family_t_exact", ROOT / "scripts/preflight_bias60_family_t_exact.py")
assert spec and spec.loader
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)
sys.path.insert(0, str(ROOT / "src"))
from bias60.ideas.t_exact import deflection_degrees, orphan_link_indices, stroke_components


def test_deflection_uses_zero_degrees_for_straight_continuation():
    assert deflection_degrees((1, 0), (-1, 0)) == 0.0
    assert deflection_degrees((1, 0), (0, 1)) == 90.0


def test_named_stroke_joins_only_when_deflection_is_strictly_under_30_degrees():
    def outward(degrees):
        angle = math.radians(degrees)
        return (math.cos(angle), math.sin(angle))

    connectors = [("a", "j"), ("j", "b"), ("j", "c")]
    tangents = [((1, 0), (-1, 0)), (outward(29), (1, 0)),
                (outward(30), (1, 0))]
    labels = stroke_components(["route:x"] * 3, connectors, tangents)
    assert labels[0] == labels[1]
    assert labels[2] != labels[0]


def test_stroke_does_not_cross_semantic_identity_or_unconnected_junctions():
    labels = stroke_components(
        ["route:x", "route:y", "route:x"],
        [("a", "j"), ("j", "b"), ("far", "end")],
        [((1, 0), (-1, 0)), ((1, 0), (-1, 0)), ((1, 0), (-1, 0))])
    assert len(set(labels)) == 3


def test_orphan_links_require_non_named_neighbors_at_both_endpoints():
    classes = ["motorway_link", "tertiary", "unclassified", "primary", "tertiary"]
    subclasses = ["link", None, None, None, None]
    connectors = [["a", "b"], ["a", "x"], ["b", "y"], ["c", "d"], ["c", "z"]]
    # The first link qualifies; the second is excluded because one endpoint has a named road.
    assert orphan_link_indices(classes, subclasses, connectors,
                               {"motorway", "trunk", "primary", "secondary"}) == [0]


def test_t1_exact_runs_end_to_end_on_a_synthetic_named_stroke(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely
    from bias60.ideas.t_exact import t1_exact

    coordinates = [(-0.02, 0.0), (0.004, 0.0), (0.006, 0.0), (0.02, 0.0)]
    rows = []
    classes = ["primary", "tertiary", "primary"]
    for i, cls in enumerate(classes):
        rows.append({
            "class": cls, "names": {"primary": "Test Highway"}, "routes": [],
            "connectors": [{"connector_id": f"n{i}", "at": 0.0},
                           {"connector_id": f"n{i+1}", "at": 1.0}],
            "geometry": shapely.to_wkb(shapely.LineString(coordinates[i:i+2])),
        })
    road_path = tmp_path / "roads.parquet"
    pq.write_table(pa.Table.from_pylist(rows), road_path)

    class Inputs:
        region = "unit-test"
        geoids = ["00000000001"]
        config = {"seeds": {"T1": {
            "named_classes": ["motorway", "trunk", "primary", "secondary"],
            "outside_named_share_threshold": 0.7,
            "unfiltered_layer": "roads-unfiltered",
        }}}
        filesystem = __import__("pyarrow.fs", fromlist=["LocalFileSystem"]).LocalFileSystem()
        accessed = {"layers": [], "parquet_columns": []}
        def layer_path(self, layer):
            assert layer == "roads-unfiltered"
            return str(road_path)
        def tract_geometries(self):
            import numpy as np
            return np.asarray([shapely.box(0.0, -0.01, 0.01, 0.01)], dtype=object)

    output = t1_exact(Inputs())
    emission = output.emissions[0]
    assert output.idea_id == "T1-v2"
    assert emission.defined.tolist() == [True]
    assert 0.0 < emission.values[0] < 1.0
    assert output.diagnostics["deflection_limit_degrees"] == 30.0


def test_t2_exact_counts_orphan_link_and_measures_attached_roads(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely
    from bias60.ideas.t_exact import t2_exact

    rows = [
        {"class": "tertiary", "subclass": None, "connectors": [{"connector_id": "a", "at": 0.0}, {"connector_id": "x", "at": 1.0}], "geometry": shapely.to_wkb(shapely.LineString([(-0.002, 0), (-0.001, 0)]))},
        {"class": "motorway_link", "subclass": "link", "connectors": [{"connector_id": "a", "at": 0.0}, {"connector_id": "b", "at": 1.0}], "geometry": shapely.to_wkb(shapely.LineString([(-0.001, 0), (0.001, 0)]))},
        {"class": "unclassified", "subclass": None, "connectors": [{"connector_id": "b", "at": 0.0}, {"connector_id": "y", "at": 1.0}], "geometry": shapely.to_wkb(shapely.LineString([(0.001, 0), (0.002, 0)]))},
    ]
    path = tmp_path / "roads.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)

    class Inputs:
        region, geoids = "unit-test", ["00000000001"]
        config = {"seeds": {"T2": {"named_classes": ["motorway", "trunk", "primary", "secondary"], "attached_length_limit_m": 2000.0, "unfiltered_layer": "roads-unfiltered"}}}
        filesystem = __import__("pyarrow.fs", fromlist=["LocalFileSystem"]).LocalFileSystem()
        accessed = {"layers": [], "parquet_columns": []}
        def layer_path(self, layer):
            assert layer == "roads-unfiltered"
            return str(path)
        def tract_geometries(self):
            return __import__("numpy").asarray([shapely.box(-0.01, -0.01, 0.01, 0.01)], dtype=object)

    emission = t2_exact(Inputs()).emissions
    assert emission[0].values.tolist() == [1.0]
    assert 0.25 < emission[1].values[0] < 0.27


def test_recipe_preflight_requires_the_frozen_inputs_without_claiming_conformance():
    roads = {"class", "names", "routes", "connectors", "subclass", "road_flags",
             "rail_flags", "level_rules", "geometry"}
    infrastructure = {"geometry", "subtype", "class"}
    tracts = {"GEOID", "geometry"}
    strata = {"GEOID", "pop_total"}
    assessment = preflight.recipe_input_assessment(roads, infrastructure, tracts, strata)
    assert all(assessment[f"T{i}"]["required_fields_present"] for i in range(1, 5))
    assert assessment["T4"]["centroid_geometry_note"].startswith(
        "The supplied ACS tract table exposes polygon geometry")


def test_missing_geometric_crossing_and_stroke_inputs_are_reported():
    assessment = preflight.recipe_input_assessment(
        {"class", "geometry"}, {"geometry"}, {"GEOID", "geometry"},
        {"GEOID", "pop_total"})
    assert assessment["T1"]["missing_columns"]["roads-unfiltered"] == [
        "connectors", "names", "routes"]
    assert assessment["T3"]["missing_columns"]["roads-unfiltered"] == [
        "connectors", "level_rules", "rail_flags", "road_flags"]


def test_preflight_keeps_all_four_v1_fidelity_gaps_explicit():
    config = __import__("json").loads(
        (ROOT / "config/bias60-harness-preregistered.json").read_text())
    assert set(preflight.KNOWN_V1_GAPS) == {"T1", "T2", "T3", "T4"}
    assert all(config["seeds"][idea] for idea in preflight.KNOWN_V1_GAPS)
    assert all("v1" in value for value in preflight.KNOWN_V1_GAPS.values())


def test_versioned_family_registry_keeps_pending_recipes_visible():
    registry = __import__("json").loads(
        (ROOT / "config/bias60-family-t-exact-registry.json").read_text())
    entries = {entry["id"]: entry for entry in registry["entries"]}
    assert set(entries) == {"T1-v2", "T2-v2", "T3-v2", "T4-v2"}
    assert entries["T1-v2"]["status"] == "implemented_exact_recipe"
    assert entries["T1-v2"]["official_rows"] == 9379
    assert all(entries[f"T{i}-v2"]["status"] == "pending" for i in (2, 3, 4))
