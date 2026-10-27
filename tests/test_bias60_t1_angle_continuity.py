from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_t1_angle_continuity_sensitivity.py"
spec = importlib.util.spec_from_file_location("screen_t1_angle_continuity_sensitivity", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_strict_angle_rule_changes_toy_covariate_only_below_30_degrees():
    below = module.run_toy_geometry_case(29.0)
    exact = module.run_toy_geometry_case(30.0)
    above = module.run_toy_geometry_case(31.0)
    assert below["exact_identity_drop_m"] == [0.0, 0.0]
    assert abs(below["angle_continuity_drop_m"][0] - 10.0) <= 1e-9
    assert exact["angle_continuity_drop_m"] == [0.0, 0.0]
    assert above["angle_continuity_drop_m"] == [0.0, 0.0]
    assert all(case["within_1e-9_m"] for case in (below, exact, above))
    assert below["merged_components"] == 2
    assert exact["merged_components"] == 3
    assert above["merged_components"] == 3


def test_toy_geometry_report_has_exact_length_tolerance_and_threshold_controls():
    result = module.toy_geometry_checks()
    assert result["strict_threshold_behavior"] is True
    assert result["all_lengths_within_1e-9_m"] is True
    assert result["cases"]["29_degrees"]["angle_continuity_drop_m"][0] > 0
    assert result["cases"]["30_degrees"]["angle_continuity_drop_m"][0] == 0


def test_summaries_report_quantiles_for_all_official_ids_including_zero_changes():
    import numpy as np

    summary = module.summarize(np.asarray([0.0, 0.0, 1.0, 2.0]))
    assert summary["n"] == 4
    assert summary["min"] == 0.0
    assert summary["median"] == 0.5
    assert summary["max"] == 2.0
