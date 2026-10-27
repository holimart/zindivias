#!/usr/bin/env python3
"""Hand-check the frozen T1 class-drop aggregation on 12 synthetic road graphs."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
import numpy as np
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
T1_SOURCE = ROOT / "src/bias60/ideas/t.py"
NAMED_CLASSES = ("motorway", "trunk", "primary", "secondary")
OUTSIDE_THRESHOLD = 0.7


def edge(cls: str, lengths: list[float], primary: str | None = None,
         route: dict[str, str] | None = None) -> dict[str, Any]:
    names = {"primary": primary} if primary is not None else None
    routes = [route] if route is not None else []
    return {"class": cls, "names": names, "routes": routes, "lengths": lengths}


def fixtures() -> list[dict[str, Any]]:
    return [
        {"id":"G01_branching_identity", "n":3,
         "edges":[edge("primary",[10,0,0],"Main"),edge("primary",[0,60,0],"main"),edge("primary",[0,0,30]," main "),edge("residential",[2,0,0],"MAIN")],
         "expected_gap":[2/12,0,0],"expected_defined":[True,True,True],"features":["branching","identity_casefold"]},
        {"id":"G02_exact_threshold", "n":2,
         "edges":[edge("primary",[30,70],"threshold"),edge("residential",[6,4],"threshold")],
         "expected_gap":[6/36,0],"expected_defined":[True,True],"features":["outside_share_exactly_0.7"]},
        {"id":"G03_below_threshold", "n":2,
         "edges":[edge("primary",[31,69],"below"),edge("residential",[3,4],"below")],
         "expected_gap":[0,0],"expected_defined":[True,True],"features":["outside_share_below_0.7"]},
        {"id":"G04_equal_named_and_drop", "n":2,
         "edges":[edge("trunk",[10,90],"equal"),edge("unclassified",[10,0],"equal")],
         "expected_gap":[0.5,0],"expected_defined":[True,True],"features":["equal_named_and_drop_lengths"]},
        {"id":"G05_mixed_named_classes", "n":2,
         "edges":[edge("motorway",[10,0],"mix"),edge("primary",[0,90],"mix"),edge("secondary",[0,5],"mix"),edge("tertiary",[5,0],"mix")],
         "expected_gap":[5/15,0],"expected_defined":[True,True],"features":["mixed_named_classes","secondary_is_named"]},
        {"id":"G06_two_identities_shared_tracts", "n":2,
         "edges":[edge("primary",[10,90],"A"),edge("residential",[5,0],"A"),edge("trunk",[0,80],"B"),edge("service",[0,8],"B")],
         "expected_gap":[1/3,0],"expected_defined":[True,True],"features":["multiple_strokes_per_tract"]},
        {"id":"G07_three_tract_branch", "n":3,
         "edges":[edge("primary",[5,0,0],"branch"),edge("primary",[0,5,0],"branch"),edge("primary",[0,0,90],"branch"),edge("residential",[2,3,5],"branch")],
         "expected_gap":[2/7,3/8,0],"expected_defined":[True,True,True],"features":["branching","three_tract_stroke"]},
        {"id":"G08_primary_name_beats_route", "n":2,
         "edges":[edge("primary",[10,90],"Road X",{"network":"US","ref":"9"}),edge("residential",[5,0],"road x",{"network":"CA","ref":"other"})],
         "expected_gap":[1/3,0],"expected_defined":[True,True],"features":["primary_name_identity_precedes_route"]},
        {"id":"G09_route_fallback_identity", "n":2,
         "edges":[edge("primary",[10,90],None,{"network":"US","ref":"A1"}),edge("residential",[5,0],None,{"network":"us","ref":"a1"})],
         "expected_gap":[1/3,0],"expected_defined":[True,True],"features":["route_identity_fallback_casefold"]},
        {"id":"G10_isolated_non_named_road", "n":2,
         "edges":[edge("residential",[8,0],"isolated"),edge("primary",[0,10],"separate")],
         "expected_gap":[0,0],"expected_defined":[False,True],"features":["isolated_non_named_identity"]},
        {"id":"G11_drop_in_tract_without_named_length", "n":2,
         "edges":[edge("primary",[100,0],"through"),edge("tertiary",[0,10],"through")],
         "expected_gap":[0,1],"expected_defined":[True,True],"features":["stroke_class_drop_in_other_tract"]},
        {"id":"G12_empty_graph", "n":2,
         "edges":[],"expected_gap":[0,0],"expected_defined":[False,False],"features":["no_road_segments"]},
    ]


class Vector:
    def __init__(self, values: list[int]): self.values = list(values)
    def __getitem__(self, indexes: Any) -> "Vector":
        if isinstance(indexes, (int, np.integer)): return Vector([self.values[int(indexes)]])
        return Vector([self.values[int(i)] for i in indexes])
    def __len__(self) -> int: return len(self.values)


class FakeTransformer:
    def transform(self, x: Any, y: Any) -> tuple[Any, Any]: return x, y


class FakeTree:
    def __init__(self, tracts: Vector, lengths: list[list[float]]):
        self.tracts = tracts
        self.lengths = lengths
    def query(self, lines: Vector, predicate: str) -> np.ndarray:
        assert predicate == "intersects"
        pairs = [(i, tract) for i, edge_id in enumerate(lines.values)
                 for tract, length in enumerate(self.lengths[edge_id]) if length > 0]
        return np.asarray(pairs, dtype=np.int64).T if pairs else np.empty((2, 0), dtype=np.int64)


class Column:
    def __init__(self, values: list[Any]): self.values = values
    def to_pylist(self) -> list[Any]: return list(self.values)


class Table:
    def __init__(self, edges: list[dict[str, Any]]): self.edges = edges
    def column(self, name: str) -> Column:
        if name == "geometry": return Column(list(range(len(self.edges))))
        if name == "lengths": raise AssertionError("T1 must not read synthetic lengths as a road attribute")
        return Column([record[name] for record in self.edges])


class Parquet:
    def __init__(self, edges: list[dict[str, Any]]):
        self.edges = edges
        self.metadata = type("Metadata", (), {"num_row_groups": 1})()
    def read_row_group(self, group: int, columns: list[str]) -> Table:
        assert group == 0
        assert columns == ["class", "names", "routes", "geometry"]
        return Table(self.edges)


class Inputs:
    def __init__(self, n: int):
        self.geoids = [f"000000000{i:02d}" for i in range(n)]
        self.region = "synthetic-handcheck"
        self.config = {"seeds":{"T1":{"named_classes":list(NAMED_CLASSES),
                                     "outside_named_share_threshold":OUTSIDE_THRESHOLD,
                                     "unfiltered_layer":"roads-unfiltered"}}}
    def tract_geometries(self) -> list[int]: return list(range(len(self.geoids)))


def run_graph(graph: dict[str, Any]) -> dict[str, Any]:
    from bias60.ideas import t as t_module
    edges = graph["edges"]
    lengths = [record["lengths"] for record in edges]
    inputs = Inputs(graph["n"])
    original_metric_tracts = t_module._metric_tracts
    original_road_layer = t_module._road_layer
    originals = {name:getattr(shapely,name) for name in ("STRtree","from_wkb","transform","intersection","length")}
    try:
        t_module._metric_tracts = lambda _: (Vector(list(range(graph["n"]))), FakeTransformer())
        t_module._road_layer = lambda *_args, **_kwargs: Parquet(edges)
        shapely.STRtree = lambda _tracts: FakeTree(Vector(list(range(graph["n"]))), lengths)
        shapely.from_wkb = lambda wkb: Vector([int(value) for value in wkb])
        shapely.transform = lambda geometries, *_args, **_kwargs: geometries
        def intersection(line_vector: Vector, tract_vector: Vector) -> list[tuple[int,int]]:
            return list(zip(line_vector.values, tract_vector.values))
        shapely.intersection = intersection
        shapely.length = lambda pairs: np.asarray([lengths[edge_id][tract_id] for edge_id,tract_id in pairs],dtype=np.float64)
        output = t_module.t1(inputs)
    finally:
        t_module._metric_tracts = original_metric_tracts
        t_module._road_layer = original_road_layer
        for name, value in originals.items(): setattr(shapely,name,value)
    gap = output.emissions[0]
    defined = np.asarray(gap.defined,dtype=bool)
    values = np.asarray(gap.values,dtype=np.float64)
    if len(values)!=graph["n"] or len(defined)!=graph["n"]:
        raise ValueError(f"{graph['id']}: unexpected emission length")
    if not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1):
        raise ValueError(f"{graph['id']}: frozen T1 emitted invalid gap values")
    actual = [float(values[i]) if defined[i] else 0.0 for i in range(len(values))]
    errors = [abs(actual[i]-graph["expected_gap"][i]) for i in range(len(actual))]
    if defined.tolist() != graph["expected_defined"]:
        raise AssertionError(f"{graph['id']}: expected defined mask {graph['expected_defined']}, got {defined.tolist()}")
    return {"fixture_id":graph["id"],"tract_count":graph["n"],"features":graph["features"],
            "expected_gap_fractions":graph["expected_gap"],"actual_gap_fractions":actual,
            "expected_defined_mask":graph["expected_defined"],"actual_defined_mask":defined.tolist(),
            "absolute_errors":errors,"maximum_absolute_error":max(errors,default=0.0)}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_all() -> dict[str, Any]:
    graph_specs = fixtures()
    graph_results = [run_graph(graph) for graph in graph_specs]
    fixture_bytes = json.dumps(graph_specs, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    max_error = max(result["maximum_absolute_error"] for result in graph_results)
    return {"schema_version":1,
            "audit":"Synthetic hand-check of the frozen T1 class-drop aggregation; no competition labels or features.",
            "scope":{"synthetic_road_graphs":len(graph_results),"graph_ids":[result["fixture_id"] for result in graph_results],
                     "frozen_T1_function_exercised":"bias60.ideas.t.t1 aggregation; road I/O and geometric clipping stubbed with hand-specified tract lengths",
                     "labels_or_targets_read":False,"removed_references_read":False,"external_features_read":False,
                     "leaderboard_feedback_used":False,"candidate_artifacts_or_outputs_changed":False,"uploads":0},
            "method":{"identity_rule":"frozen _identity: stripped/casefolded names.primary; fallback casefolded route network/ref",
                      "named_classes":list(NAMED_CLASSES),"outside_named_share_threshold":OUTSIDE_THRESHOLD,
                      "expected_values":"fixture-by-fixture hand-calculated class-drop length / (named + eligible class-drop length); undefined tracts represented by emission zero with defined=false",
                      "test_domains":["branching", "exact and below-threshold outside shares", "equal named/drop lengths", "mixed named classes", "multiple identities", "isolated roads", "route fallback", "empty graph"]},
            "results":{"fixtures":graph_results,"maximum_absolute_fraction_error":max_error,
                       "acceptance_tolerance":1e-12,"acceptance_pass":max_error<=1e-12,
                       "all_defined_masks_match":True},
            "provenance":{"frozen_T1_source_sha256":file_sha256(T1_SOURCE),
                          "analysis_script_sha256":file_sha256(Path(__file__).resolve()),
                          "test_file_sha256":file_sha256(ROOT/"tests/test_t1_class_drop_handcheck.py"),
                          "fixture_spec_sha256":hashlib.sha256(fixture_bytes).hexdigest()}}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,default=ROOT/"reports/t1-class-drop-handcheck.json")
    args=parser.parse_args()
    result=run_all()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps({"fixtures":len(result["results"]["fixtures"]),
                      "maximum_absolute_fraction_error":result["results"]["maximum_absolute_fraction_error"],
                      "acceptance_pass":result["results"]["acceptance_pass"],"output":str(args.out)},sort_keys=True))
    return 0 if result["results"]["acceptance_pass"] else 1


if __name__=="__main__":
    raise SystemExit(main())
