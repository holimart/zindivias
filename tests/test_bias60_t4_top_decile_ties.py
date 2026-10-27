from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_t4_top_decile_ties", ROOT / "scripts/audit_t4_top_decile_ties.py"
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_hand_histogram_accounts_for_inclusive_threshold_and_fractional_tie_boundary():
    # Ten edge segments; the exact top 20% is one score-100 edge plus 1/3
    # of the three tied score-90 edges. The frozen descending rank=8 code
    # reaches score 80 and includes all ten edges.
    histogram = {
        100: (1, 10.0, 0.0),
        90: (3, 20.0, 10.0),
        80: (6, 30.0, 15.0),
    }
    result = mod.analyze_histogram(histogram, 0.2)
    current = result["inclusive"]
    fractional = result["fractional"]
    assert current["rank_in_descending_order_1based"] == 8
    assert current["threshold_score"] == 80
    assert current["boundary_tie_edges"] == 6
    assert current["selected_edges"] == 10
    assert current["selected_edge_share"] == pytest.approx(1.0)
    assert current["gap"] == pytest.approx(25 / 60)
    assert fractional["target_edges"] == pytest.approx(2.0)
    assert fractional["boundary_score"] == 90
    assert fractional["edges_strictly_above_boundary"] == 1
    assert fractional["boundary_tie_edges"] == 3
    assert fractional["boundary_weight"] == pytest.approx(1 / 3)
    assert fractional["gap"] == pytest.approx(0.2)
    assert result["edge_share_excess_over_target"] == pytest.approx(0.8)
    assert result["gap_delta_inclusive_minus_fractional"] == pytest.approx(25 / 60 - 0.2)


def test_no_ties_still_matches_the_frozen_descending_rank_selection():
    # This catches the 1-top_fraction rank being accumulated in descending order.
    histogram = {score: (1, float(score), float(score % 2)) for score in range(1, 11)}
    result = mod.analyze_histogram(histogram, 0.2)
    assert result["inclusive"]["threshold_score"] == 3
    assert result["inclusive"]["selected_edges"] == 8
    assert result["inclusive"]["selected_edge_share"] == pytest.approx(0.8)
    assert result["edge_share_excess_over_target"] == pytest.approx(0.6)
    assert result["fractional"]["target_edges"] == pytest.approx(2.0)


def test_boundary_ties_with_single_score_group_are_fully_fractional():
    histogram = {7: (10, 40.0, 12.0)}
    result = mod.analyze_histogram(histogram, 0.1)
    assert result["fractional"]["boundary_tie_edges"] == 10
    assert result["fractional"]["edges_strictly_above_boundary"] == 0
    assert result["fractional"]["boundary_weight"] == pytest.approx(0.1)
    assert result["fractional"]["gap"] == pytest.approx(0.3)
    assert result["inclusive"]["selected_edge_share"] == pytest.approx(1.0)


def test_empty_histogram_is_undefined_without_manufacturing_a_delta():
    result = mod.analyze_histogram({}, 0.1)
    assert result["inclusive"]["defined"] is False
    assert result["fractional"]["defined"] is False
    assert result["gap_delta_inclusive_minus_fractional"] is None
    assert result["edge_share_excess_over_target"] is None


def test_linear_quantiles_are_exact_for_small_hand_computed_values():
    values = [0.0, 0.1, 0.2, 0.3]
    assert mod.linear_quantile(values, 0.5) == pytest.approx(0.15)
    assert mod.linear_quantile(values, 0.95) == pytest.approx(0.285)


def test_json_serialization_is_deterministic():
    payload = {"nominal": 0.1, "histogram": {"10": [2, 4.0, 1.0], "5": [3, 7.0, 2.5]}}
    assert json.dumps(payload, sort_keys=True, allow_nan=False) == json.dumps(payload, sort_keys=True, allow_nan=False)
