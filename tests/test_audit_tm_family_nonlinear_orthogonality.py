from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_tm_family_nonlinear_orthogonality import (  # noqa: E402
    candidate_pairs,
    encode_feature,
    measure_pair_nmi,
    normalized_mutual_information,
    _load_policy_cleared_ids,
)


def test_low_cardinality_integer_features_are_kept_as_categories():
    codes, meta = encode_feature(np.array([0.0, 1.0, 1.0, np.nan, 0.0]))
    assert meta == {"encoding": "category_integer_low_cardinality", "categories_or_bins": 2,
                    "defined": 4}
    assert codes.tolist() == [0, 1, 1, -1, 0]


def test_continuous_features_use_deterministic_within_region_deciles():
    values = np.arange(100, dtype=np.float64)
    values[7] = np.nan
    left, left_meta = encode_feature(values)
    right, right_meta = encode_feature(values)
    assert left_meta == right_meta
    assert np.array_equal(left, right)
    assert left_meta["encoding"] == "empirical_decile_bins"
    assert left_meta["defined"] == 99
    assert np.all((left[np.isfinite(values)] >= 0) & (left[np.isfinite(values)] <= 9))
    assert left[7] == -1


def test_nmi_detects_deterministic_nonlinear_dependency_and_bounds_results():
    x = np.repeat(np.arange(10), 10)
    y = np.repeat(np.arange(10)[::-1], 10)
    assert normalized_mutual_information(x, y) == pytest.approx(1.0)
    assert normalized_mutual_information(np.zeros(30, dtype=int), np.ones(30, dtype=int)) == 0.0
    independent = normalized_mutual_information(np.tile([0, 1], 30), np.repeat([0, 1], 30))
    assert independent < 1e-12
    assert 0.0 <= normalized_mutual_information(x, x ** 2) <= 1.0


def test_pairwise_mask_and_minimum_overlap_gate_are_enforced():
    left = np.arange(31, dtype=np.int16)
    right = left.copy()
    left[0] = -1
    excluded = measure_pair_nmi(left, right)
    # Default eligibility is >=30, so the 30 jointly defined pairs are measured.
    assert excluded["status"] == "measured"
    assert excluded["joint_defined"] == 30
    left[1] = -1
    below = measure_pair_nmi(left, right)
    assert below["status"] == "excluded_below_minimum_overlap"
    assert below["joint_defined"] == 29 and below["nmi"] is None


def test_candidate_pairs_cover_cross_idea_temporal_comparisons_once():
    keys = ["Tm2|B|gap", "Tm2|F|burn", "Tm3|B|housing", "Tm5|R|version", "M1|R|share"]
    meta = {key: {"idea": key.split("|")[0]} for key in keys}
    pairs = candidate_pairs(keys, meta)
    assert len(pairs) == len(set(pairs))
    assert ("Tm2|B|gap", "Tm3|B|housing") in pairs
    assert ("M1|R|share", "Tm2|F|burn") in pairs
    assert ("Tm2|B|gap", "Tm2|F|burn") not in pairs
    assert ("M1|R|share", "Tm5|R|version") in pairs
    assert all("Tm" in meta[left]["idea"] or "Tm" in meta[right]["idea"]
               for left, right in pairs)


def test_pending_policy_ideas_are_removed_before_harness_artifact_access(tmp_path):
    registry = {"ideas": [{"id": idea, "status": "implemented"}
                          for idea in ("Tm1", "Tm2", "Tm3", "Tm4", "Tm5", "M1")]}
    status = {"ideas": {"Tm1": {"status": "pending_policy_review"},
                        "Tm2": {"status": "implemented"}, "Tm3": {"status": "implemented"},
                        "Tm4": {"status": "pending_policy_review"}, "Tm5": {"status": "implemented"}}}
    harness = {"per_idea": {idea: {} for idea in ("Tm1", "Tm2", "Tm3", "Tm4", "Tm5", "M1")}}
    paths = [tmp_path / name for name in ("registry.json", "status.json", "harness.json")]
    for path, value in zip(paths, (registry, status, harness)):
        path.write_text(json.dumps(value), encoding="utf-8")
    selected, manifest = _load_policy_cleared_ids(*paths)
    assert selected == ["M1", "Tm2", "Tm3", "Tm5"]
    assert manifest["pending_policy_ids"] == ["Tm1", "Tm4"]
    assert "Tm1" not in manifest["selected_idea_ids"]
    assert "Tm4" not in manifest["selected_idea_ids"]


def test_policy_pending_guard_rejects_unreviewed_temporal_input(tmp_path):
    registry = {"ideas": [{"id": idea, "status": "implemented"}
                          for idea in ("Tm1", "Tm2", "Tm3", "Tm4", "Tm5")]}
    status = {"ideas": {"Tm1": {"status": "implemented"},
                        "Tm2": {"status": "implemented"}, "Tm3": {"status": "implemented"},
                        "Tm4": {"status": "pending_policy_review"}, "Tm5": {"status": "implemented"}}}
    harness = {"per_idea": {idea: {} for idea in ("Tm1", "Tm2", "Tm3", "Tm4", "Tm5")}}
    paths = [tmp_path / name for name in ("registry.json", "status.json", "harness.json")]
    for path, value in zip(paths, (registry, status, harness)):
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="Tm1 must remain pending_policy_review"):
        _load_policy_cleared_ids(*paths)
