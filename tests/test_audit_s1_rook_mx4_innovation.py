from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon, box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_s1_rook_mx4_innovation import (  # noqa: E402
    build_rook_adjacency,
    rook_neighbor_mean,
)
from audit_s1_mx4_lagged_innovation import leave_one_out_bin_mean_residual  # noqa: E402


def test_rook_adjacency_keeps_shared_edges_and_excludes_corner_only_contacts():
    polygons = [
        box(0, 0, 1, 1),  # A
        box(1, 0, 2, 1),  # B shares an edge with A
        box(2, 1, 3, 2),  # C touches B only at a corner
        box(0, 1, 1, 2),  # D shares another edge with A
    ]
    neighbors = build_rook_adjacency(polygons)
    assert [row.tolist() for row in neighbors] == [[1, 3], [0], [], [0]]
    assert all(i not in row for i, row in enumerate(neighbors))
    assert all(i in neighbors[j] for i, row in enumerate(neighbors) for j in row)


def test_rook_neighbor_mean_uses_only_defined_neighbors_and_excludes_self():
    neighbors = [np.array([1]), np.array([0, 2]), np.array([1])]
    values = np.array([0.2, 0.8, np.nan])
    defined = np.array([True, True, False])
    result = rook_neighbor_mean(values, defined, neighbors)
    assert result["lag"].tolist()[:2] == pytest.approx([0.8, 0.2])
    assert result["defined"].tolist() == [True, True, True]
    assert result["neighbor_count"].tolist() == [1, 2, 1]
    assert result["defined_neighbor_count"].tolist() == [1, 1, 1]


def test_rook_lag_can_be_residualized_with_focal_tract_excluded_from_bin_mean():
    ids = ["g0", "g1", "g2", "g3"]
    current = np.array([0.0, 1.0, 2.0, 3.0])
    rook_lag = np.array([1.0, 3.0, 10.0, 30.0])
    result = leave_one_out_bin_mean_residual(ids, current, np.ones(4, bool),
                                             rook_lag, np.ones(4, bool), bins=2)
    assert result["peer_bin_mean"].tolist() == pytest.approx([3.0, 1.0, 30.0, 10.0])
    assert result["innovation"].tolist() == pytest.approx([-2.0, 2.0, -20.0, 20.0])
    altered = rook_lag.copy()
    altered[0] += 100
    again = leave_one_out_bin_mean_residual(ids, current, np.ones(4, bool),
                                            altered, np.ones(4, bool), bins=2)
    assert again["peer_bin_mean"][0] == result["peer_bin_mean"][0]


def test_empty_or_invalid_geometry_and_bad_adjacency_fail_closed():
    with pytest.raises(ValueError, match="nonempty"):
        build_rook_adjacency([])
    with pytest.raises(ValueError, match="valid"):
        build_rook_adjacency([Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])])
    with pytest.raises(ValueError, match="exclude each focal"):
        rook_neighbor_mean(np.array([0.5]), np.array([True]), [np.array([0])])
    with pytest.raises(ValueError, match="unique"):
        rook_neighbor_mean(np.array([0.5, 0.3]), np.ones(2, bool),
                           [np.array([1, 1]), np.array([0])])
