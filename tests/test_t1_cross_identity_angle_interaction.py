from __future__ import annotations

import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_t1_cross_identity_angle_interaction.py"
spec = importlib.util.spec_from_file_location("audit_t1_cross_identity_angle_interaction", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def outward(degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    return math.cos(angle), math.sin(angle)


def test_average_ranks_and_spearman_ties_are_deterministic():
    assert module.average_ranks([1, 1, 2, 3]).tolist() == [1.5, 1.5, 3.0, 4.0]
    assert module.spearman_rho([1, 1, 2, 3], [3, 3, 2, 1]) == -1.0
    assert module.spearman_rho([1, 1, 2, 3], [4, 4, 5, 6]) == 1.0


def test_cross_identity_join_membership_changes_only_at_declared_angle_limit():
    evidence = [
        module.safety_audit.route_name_evidence({"primary": "Road 7"}, []),
        module.safety_audit.route_name_evidence({}, [{"network": "US", "ref": "Road 7"}]),
    ]
    identities = ["name:road 7", "route:us:7"]
    connectors = [("a", "j"), ("j", "b")]
    tangents = [((1.0, 0.0), (-1.0, 0.0)), (outward(20), (1.0, 0.0))]
    counts = {}
    for threshold in (15.0, 30.0, 45.0):
        same = module.stroke_components(identities, connectors, tangents, threshold_degrees=threshold)
        checked = module.safety_audit.cross_identity_safety(
            identities, connectors, tangents, evidence, same, threshold_degrees=threshold)
        counts[threshold] = checked["unique_safe_endpoint_pairs"]
    assert counts == {15.0: 0, 30.0: 1, 45.0: 1}


def test_45_degree_variant_includes_turn_excluded_at_30():
    evidence = [
        module.safety_audit.route_name_evidence({"primary": "Road 9"}, []),
        module.safety_audit.route_name_evidence({}, [{"network": "US", "ref": "Road 9"}]),
    ]
    identities = ["name:road 9", "route:us:9"]
    connectors = [("a", "j"), ("j", "b")]
    tangents = [((1.0, 0.0), (-1.0, 0.0)), (outward(40), (1.0, 0.0))]
    safe_counts = []
    for threshold in (15.0, 30.0, 45.0):
        labels = module.stroke_components(identities, connectors, tangents, threshold_degrees=threshold)
        result = module.safety_audit.cross_identity_safety(
            identities, connectors, tangents, evidence, labels, threshold_degrees=threshold)
        safe_counts.append(result["unique_safe_endpoint_pairs"])
    assert safe_counts == [0, 0, 1]


def test_each_angle_limit_excludes_an_exact_threshold_turn():
    evidence = [
        module.safety_audit.route_name_evidence({"primary": "Road 12"}, []),
        module.safety_audit.route_name_evidence({}, [{"network": "US", "ref": "Road 12"}]),
    ]
    identities = ["name:road 12", "route:us:12"]
    connectors = [("a", "j"), ("j", "b")]
    for threshold in (15.0, 30.0, 45.0):
        tangents = [((1.0, 0.0), (-1.0, 0.0)), (outward(threshold), (1.0, 0.0))]
        labels = module.stroke_components(identities, connectors, tangents, threshold_degrees=threshold)
        result = module.safety_audit.cross_identity_safety(
            identities, connectors, tangents, evidence, labels, threshold_degrees=threshold)
        assert result["otherwise_compatible_endpoint_pairs"] == 0
        assert result["unique_safe_endpoint_pairs"] == 0


def test_frozen_report_hash_validation_detects_no_source_drift():
    safety, angle, hashes = module.verify_frozen_reports()
    assert hashes["cross_identity_safety_report_sha256"] == module.SAFETY_REPORT_SHA256
    assert hashes["angle_only_report_sha256"] == module.ANGLE_REPORT_SHA256
    assert safety["measure_after"]["acceptance"]["all_accepted_pairs_unambiguous_and_one_to_one"]
    assert angle["measure_after"]["acceptance"]["identity_T1_outputs_reproduced"]
