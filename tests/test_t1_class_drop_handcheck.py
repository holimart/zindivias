from __future__ import annotations
import importlib.util
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit_t1_class_drop_handcheck.py"
spec=importlib.util.spec_from_file_location("t1_class_drop_handcheck",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_twelve_frozen_t1_synthetic_road_graphs_match_hand_calculation():
    report=module.run_all()
    assert report==module.run_all()
    results=report["results"]
    assert len(results["fixtures"])==12
    assert results["maximum_absolute_fraction_error"]<=1e-12
    assert results["acceptance_pass"] is True
    assert all(row["expected_defined_mask"]==row["actual_defined_mask"] for row in results["fixtures"])
    assert {feature for row in results["fixtures"] for feature in row["features"]} >= {
        "branching", "outside_share_exactly_0.7", "equal_named_and_drop_lengths",
        "mixed_named_classes", "isolated_non_named_identity", "route_identity_fallback_casefold",
        "no_road_segments"}
