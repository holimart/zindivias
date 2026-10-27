from __future__ import annotations

import importlib.util
import math
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_t1_cross_identity_join_safety.py"
spec = importlib.util.spec_from_file_location("audit_t1_cross_identity_join_safety", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def row_tangents():
    return ((1.0, 0.0), (-1.0, 0.0))


def test_unique_literal_name_to_route_ref_pair_is_reciprocal_and_one_to_one():
    identities = ["name:highway 1", "route:us:i-1"]
    evidence = [
        module.route_name_evidence({"primary": "Highway 1"}, []),
        module.route_name_evidence({}, [{"network": "US", "ref": " highway   1 "}]),
    ]
    result = module.cross_identity_safety(
        identities, [("a", "j"), ("j", "b")], [row_tangents(), row_tangents()],
        evidence, [0, 1])
    assert result["otherwise_compatible_endpoint_pairs"] == 1
    assert result["strict_route_name_evidence_endpoint_pairs"] == 1
    assert result["unique_safe_endpoint_pairs"] == 1
    assert result["accepted_ambiguous_merges"] == 0
    assert result["accepted_many_to_one_component_merges"] == 0
    labels = module.union_component_labels([0, 1], result["accepted_pair_details"])
    assert labels[0] == labels[1]


def test_candidate_with_no_literal_route_or_name_match_is_not_joined():
    result = module.cross_identity_safety(
        ["name:main", "route:us:i-5"], [("a", "j"), ("j", "b")],
        [row_tangents(), row_tangents()],
        [module.route_name_evidence({"primary": "Main"}, []),
         module.route_name_evidence({}, [{"network": "US", "ref": "I-5"}])],
        [0, 1])
    assert result["otherwise_compatible_endpoint_pairs"] == 1
    assert result["strict_route_name_evidence_endpoint_pairs"] == 0
    assert result["unique_safe_endpoint_pairs"] == 0


def test_collision_with_two_route_partners_is_rejected_not_many_to_one():
    identities = ["name:route 8", "route:us:8", "route:state:8"]
    evidence = [
        module.route_name_evidence({"primary": "Route 8"}, []),
        module.route_name_evidence({}, [{"network": "US", "ref": "route 8"}]),
        module.route_name_evidence({}, [{"network": "State", "ref": "route 8"}]),
    ]
    connectors = [("j1", "j2"), ("b", "j1"), ("j2", "c")]
    tangents = [row_tangents(), row_tangents(), row_tangents()]
    result = module.cross_identity_safety(identities, connectors, tangents, evidence, [0, 1, 2])
    assert result["otherwise_compatible_endpoint_pairs"] == 2
    assert result["strict_route_name_evidence_endpoint_pairs"] == 2
    assert result["evidence_components_with_multiple_candidate_partners"] == 1
    assert result["unique_safe_endpoint_pairs"] == 0
    assert result["accepted_many_to_one_component_merges"] == 0


def test_multiple_endpoint_witnesses_for_one_component_pair_are_ambiguous():
    evidence = [
        module.route_name_evidence({"primary": "State Route 6"}, []),
        module.route_name_evidence({}, [{"network": "State", "ref": "State Route 6"}]),
    ]
    result = module.cross_identity_safety(
        ["name:state route 6", "route:other:6"], [("j", "j"), ("j", "j")],
        [row_tangents(), row_tangents()], evidence, [0, 1])
    assert result["otherwise_compatible_endpoint_pairs"] == 2
    assert result["ambiguous_evidence_component_pairs_multiple_witnesses"] == 1
    assert result["unique_safe_endpoint_pairs"] == 0


def test_exactly_30_degree_endpoint_pair_is_excluded():
    angle = math.radians(30.0)
    at_start = (math.cos(angle), math.sin(angle))
    result = module.cross_identity_safety(
        ["name:x", "route:us:x"], [("a", "j"), ("j", "b")],
        [row_tangents(), (at_start, (1.0, 0.0))],
        [module.route_name_evidence({"primary": "x"}, []),
         module.route_name_evidence({}, [{"network": "US", "ref": "x"}])],
        [0, 1])
    assert result["otherwise_compatible_endpoint_pairs"] == 0
    assert result["unique_safe_endpoint_pairs"] == 0


def test_evidence_normalization_is_literal_not_fuzzy():
    a = module.route_name_evidence({"primary": "  Route   1 "}, [])
    b = module.route_name_evidence({}, [{"network": "N", "ref": "route 1"}])
    c = module.route_name_evidence({}, [{"network": "N", "ref": "Route-1"}])
    assert a & b
    assert not (a & c)
