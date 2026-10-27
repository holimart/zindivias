from __future__ import annotations

import importlib.util
import math
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_t1_geometry_stroke_merge.py"
spec = importlib.util.spec_from_file_location("audit_t1_geometry_stroke_merge", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def outward(degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    return (math.cos(angle), math.sin(angle))


def test_synthetic_stroke_joins_match_frozen_strict_turn_oracle_and_respect_identity():
    identities = ["name:main", "name:main", "name:main", "route:other"]
    connectors = [("a", "j"), ("j", "b"), ("j", "c"), ("j", "d")]
    tangents = [((1.0, 0.0), (-1.0, 0.0)),
                (outward(29.0), (1.0, 0.0)),
                (outward(30.0), (1.0, 0.0)),
                (outward(29.0), (1.0, 0.0))]
    diagnostics = module.stroke_diagnostics(
        identities, connectors, tangents, ["g0", "g1", "g2", "g3"])
    labels = diagnostics["component_labels"]
    assert diagnostics["eligible_within_identity_endpoint_joins"] == 1
    assert labels[0] == labels[1]
    assert labels[0] != labels[2]  # exactly 30 degrees is not eligible
    assert labels[0] != labels[3]  # same connector/angle cannot cross identity
    assert diagnostics["cross_identity_compatible_endpoint_pairs_not_merged"] == 1
    assert diagnostics["cross_identity_component_merges"] == 0
    assert diagnostics["ambiguous_junctions"] == 1
    assert diagnostics["ambiguous_junctions_degree_gt_2"] == 1


def test_duplicate_normalized_geometry_is_reported_not_deduplicated():
    diagnostics = module.stroke_diagnostics(
        ["name:a", "route:b", "name:a"],
        [("a", "b"), ("c", "d"), ("e", "f")],
        [((1.0, 0.0), (-1.0, 0.0))] * 3,
        ["duplicate", "duplicate", "unique"],
    )
    assert diagnostics["duplicate_geometry_groups"] == 1
    assert diagnostics["duplicate_geometry_excess_rows"] == 1
    assert diagnostics["duplicate_geometry_groups_spanning_identities"] == 1
    assert diagnostics["cross_identity_component_merges"] == 0


def test_exact_identity_and_merged_aggregation_emit_tract_level_changes():
    identities = ["name:main", "name:main"]
    classes = ["primary", "tertiary"]
    local = [{0: 30.0, 1: 70.0}, {0: 6.0, 1: 4.0}]
    exact = module._exact_identity_t1(
        identities, classes, local, 2, outside_share_threshold=0.7)
    merged = module._aggregate(
        [0, 1], classes, [100.0, 10.0], local, 2, outside_share_threshold=0.7)
    assert exact["defined"].tolist() == [True, True]
    assert math.isclose(exact["gap"][0], 6.0 / 36.0, abs_tol=1e-12)
    assert merged["defined"].tolist() == [False, False]
    assert merged["eligible_named_length_m"].tolist() == [0.0, 0.0]
    assert merged["eligible_drop_length_m"].tolist() == [0.0, 0.0]


def test_exact_30_degree_tangent_is_excluded_with_roundoff_guard():
    assert module.deflection_degrees((-1.0, 0.0), outward(30.0)) >= 30.0 - 1e-12
    diagnostics = module.stroke_diagnostics(
        ["name:a", "name:a"], [("x", "j"), ("j", "y")],
        [((1.0, 0.0), (-1.0, 0.0)), (outward(30.0), (1.0, 0.0))],
        ["g0", "g1"])
    assert diagnostics["eligible_within_identity_endpoint_joins"] == 0
    assert diagnostics["cross_identity_component_merges"] == 0
