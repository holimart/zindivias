from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "screen_t4_top_decile_rank_direction_fix",
    ROOT / "scripts/screen_t4_top_decile_rank_direction_fix.py",
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_no_tie_corrected_rank_is_exact_top_decile_and_matches_fractional_score():
    histogram = {
        100: (1, 4.0, 0.0),
        90: (1, 6.0, 6.0),
        80: (8, 40.0, 20.0),
    }
    inclusive = mod.corrected_rank_inclusive(histogram, 0.2)
    exact = mod.corrected_exact_fractional(histogram, 0.2)
    frozen = mod.prior.inclusive_selection(histogram, 0.2)
    assert inclusive["rank_1based"] == 2
    assert inclusive["threshold_score"] == 90
    assert inclusive["selected_edges"] == 2
    assert inclusive["selected_edge_share"] == pytest.approx(0.2, abs=1e-12)
    assert exact["selected_edge_share"] == pytest.approx(0.2, abs=1e-12)
    assert exact["edge_share_error"] == pytest.approx(0.0, abs=1e-12)
    assert exact["gap"] == pytest.approx(0.6)
    assert frozen["rank_in_descending_order_1based"] == 8
    assert frozen["selected_edge_share"] == pytest.approx(1.0)


def test_tie_expansion_is_reported_separately_from_fixed_rank_direction():
    histogram = {
        100: (1, 10.0, 0.0),
        90: (3, 20.0, 10.0),
        80: (6, 30.0, 15.0),
    }
    inclusive = mod.corrected_rank_inclusive(histogram, 0.2)
    exact = mod.corrected_exact_fractional(histogram, 0.2)
    frozen = mod.prior.inclusive_selection(histogram, 0.2)
    assert inclusive["rank_1based"] == 2
    assert inclusive["threshold_score"] == 90
    assert inclusive["selected_edges"] == 4
    assert inclusive["selected_edge_share"] == pytest.approx(0.4)
    assert inclusive["tie_expansion_edges"] == pytest.approx(2.0)
    assert inclusive["tie_expansion_share"] == pytest.approx(0.2)
    assert exact["edges_strictly_above_boundary"] == 1
    assert exact["boundary_tie_edges"] == 3
    assert exact["boundary_weight"] == pytest.approx(1 / 3)
    assert exact["selected_edge_share"] == pytest.approx(0.2, abs=1e-12)
    assert exact["gap"] == pytest.approx(0.2)
    assert frozen["selected_edge_share"] == pytest.approx(1.0)


def test_empty_histogram_stays_undefined():
    assert mod.corrected_rank_inclusive({}, 0.1)["defined"] is False
    assert mod.corrected_exact_fractional({}, 0.1)["defined"] is False
