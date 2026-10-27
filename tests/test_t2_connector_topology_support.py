from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_t2_connector_topology_support",
    ROOT / "scripts/audit_t2_connector_topology_support.py",
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_connector_ids_keep_first_and_last_distinct_at_order():
    rows = [
        {"connector_id": "b", "at": 0.8},
        {"connector_id": "a", "at": 0.1},
        {"connector_id": "b", "at": 0.9},
        {"connector_id": "middle", "at": 0.5},
        {"connector_id": None, "at": 0.2},
    ]
    assert mod.connector_ids(rows) == ["a", "middle", "b"]


def test_orphan_link_requires_two_non_named_endpoint_neighbors():
    incidence = {"u": (2, 0), "v": (2, 0), "x": (2, 1)}
    links = [
        (7, "motorway_link", ("u", "v"), b"wkb"),
        (8, "primary_link", ("u", "x"), b"wkb"),
    ]
    assert mod.classify_orphan_links(incidence, links) == [(7, "u", "v", b"wkb")]


def test_exact_one_to_one_endpoint_and_road_pair_is_accepted():
    result = mod.resolve_connector_pairs(
        {"link-c": {10}}, {"link-c": {20}}, {20: ("link-c", "outer-c")})
    assert result["accepted_pairs"] == [(10, 20, "link-c", "outer-c")]
    assert result["candidate_pair_count"] == 1
    assert result["ambiguous_pair_count"] == 0
    assert result["accepted_ambiguous_pair_count"] == 0
    assert result["unique_match_precision_proxy"] == 1.0


def test_many_to_many_endpoint_matches_are_counted_but_never_accepted():
    result = mod.resolve_connector_pairs(
        {"c": {10, 11}}, {"c": {20, 21}},
        {20: ("c", "a"), 21: ("c", "b")})
    assert result["candidate_pair_count"] == 4
    assert result["accepted_pair_count"] == 0
    assert result["ambiguous_pair_count"] == 4
    assert result["accepted_ambiguous_pair_count"] == 0
    assert result["ambiguous_pair_rejection_reasons"]["multiple_orphan_link_endpoints_at_connector"] == 4
    assert result["ambiguous_pair_rejection_reasons"]["multiple_eligible_roads_at_connector"] == 4
    assert result["orphan_endpoint_connector_cardinality"]["many_links_many_roads"] == 1


def test_one_road_attached_to_two_target_orphan_connectors_is_rejected():
    result = mod.resolve_connector_pairs(
        {"c1": {10}, "c2": {11}},
        {"c1": {20}, "c2": {20}},
        {20: ("c1", "c2")})
    assert result["candidate_pair_count"] == 2
    assert result["accepted_pair_count"] == 0
    assert result["ambiguous_pair_count"] == 2
    assert result["ambiguous_pair_rejection_reasons"]["road_touches_multiple_orphan_endpoint_connectors"] == 2


def test_frozen_2_5_10km_support_counts_and_hashes_reproduce():
    official = []
    for geoid, region in __import__("bias60.inputs", fromlist=["official_scored_pairs"]).official_scored_pairs(
            Path(__import__("os").environ["COMPETITION_WORKSPACE"])):
        official.append((str(geoid), str(region)))
    result = mod.baseline_support_measure(official)
    assert result["counts_and_hashes_reproduced"] is True
    assert result["official_tracts"] == 9379
    assert result["overall"]["2km"]["support_count"] == 2325
    assert result["overall"]["5km"]["support_count"] == 2328
    assert result["overall"]["10km"]["support_count"] == 2328
    assert all(len(result["overall"][r]["support_geoids_sha256"]) == 64 for r in ("2km", "5km", "10km"))
