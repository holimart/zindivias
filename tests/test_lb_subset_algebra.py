"""Tests for score-algebra subset moments and the committed calibration replay."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import lb_subset_algebra as algebra  # noqa: E402


def test_subset_formula_recovers_synthetic_residual_cell_moment_to_1e_12() -> None:
    y = [0.13, 0.22, 0.41, 0.07, 0.35, 0.19, 0.28]
    base = [0.04, 0.18, 0.21, 0.10, 0.31, 0.08, 0.25]
    selected = [True, False, True, False, False, True, False]
    delta = 0.075
    probe = [p + (delta if inside else 0.0) for p, inside in zip(base, selected)]
    mse_base = sum((target - pred) ** 2 for target, pred in zip(y, base)) / len(y)
    mse_probe = sum((target - pred) ** 2 for target, pred in zip(y, probe)) / len(y)
    p_share = sum(selected) / len(selected)
    expected = sum((target - pred) * inside
                   for target, pred, inside in zip(y, base, selected)) / len(y)
    observed = algebra.inferred_subset_moment(mse_base, mse_probe, delta, p_share)
    assert abs(observed - expected) <= 1e-12


def test_composition_bound_matches_relative_share_perturbation() -> None:
    delta, share, eps = 0.08, 0.17, 0.12
    shifted_share = share * (1 + eps)
    actual_error = abs(delta * (shifted_share - share) / 2)
    assert algebra.composition_error_bound(delta, share, eps) == pytest.approx(actual_error)


def test_template_score_placeholder_cannot_define_a_cell() -> None:
    with pytest.raises(ValueError, match="score placeholders"):
        algebra.read_template_projection(Path("not-read.csv"), {"coverage_gap_score"})


def test_cell_size_gate_requires_250_official_rows() -> None:
    algebra.validate_cell_size("large-cell", 250, 250)
    with pytest.raises(ValueError, match="at least 250"):
        algebra.validate_cell_size("small-cell", 500, 249)
    with pytest.raises(ValueError, match="only 249"):
        algebra.validate_cell_size("small-cell", 249, 250)


def test_subset_formula_rejects_zero_delta_and_bad_share() -> None:
    with pytest.raises(ValueError, match="delta"):
        algebra.inferred_subset_moment(0.1, 0.2, 0.0, 0.5)
    with pytest.raises(ValueError, match=r"P\(S\)"):
        algebra.inferred_subset_moment(0.1, 0.2, 0.1, 1.1)


def test_current_phase1_phase2_moments_replay_exactly(tmp_path: Path) -> None:
    workspace = os.environ.get("COMPETITION_WORKSPACE")
    if not workspace:
        pytest.skip("COMPETITION_WORKSPACE is needed for official-sample integration replay")
    output = tmp_path / "lb-subset-moments.json"
    command = [
        sys.executable,
        str(REPO / "scripts/lb_subset_algebra.py"),
        "--workspace", workspace,
        "--cells", str(REPO / "reports/region-subset-cell-definitions.json"),
        "--out", str(output),
    ]
    subprocess.run(command, cwd=REPO, check=True, capture_output=True, text=True)
    result = json.loads(output.read_text(encoding="utf-8"))
    reference = json.loads((REPO / "reports/lb-score-algebra.json").read_text(encoding="utf-8"))
    assert result["legacy_reproduction"]["phase1_and_phase2_inferred_moments_equal"] is True
    assert result["legacy_reproduction"]["moments"]["phase1"] == reference["phase1"]
    assert result["legacy_reproduction"]["moments"]["P5_residual_moments"] == reference["P5_residual_moments"]
    assert [row["status"] for row in result["subset_probe_moments"]] == [
        "awaiting_public_score"] * 4
