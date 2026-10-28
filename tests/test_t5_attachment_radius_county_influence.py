from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_t5_attachment_radius_county_influence",
    ROOT / "scripts/audit_t5_attachment_radius_county_influence.py",
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
SOURCE = ROOT / mod.SOURCE_REL


def test_leave_one_county_out_uses_paired_sse_and_counts_sign_reversals():
    folds = [
        {"region": "maricopa-az", "heldout_county_fips": "04001", "heldout_n": 1,
         "baseline_sse": 1.0, "model_sse": 5.0},
        {"region": "maricopa-az", "heldout_county_fips": "04003", "heldout_n": 1,
         "baseline_sse": 2.0, "model_sse": 0.5},
        {"region": "maricopa-az", "heldout_county_fips": "04005", "heldout_n": 1,
         "baseline_sse": 2.0, "model_sse": 0.5},
    ]
    result = mod.calculate_leave_one_county_out(folds)
    assert result["pooled_delta_r2_recomputed"] == pytest.approx(-0.2)
    assert result["sign_reversal_count"] == 1
    loo = result["leave_one_county_out_pooled_deltas"]
    first = next(row for row in loo if row["heldout_county_fips"] == "04001")
    assert first["pooled_delta_r2_without_county"] == pytest.approx(0.75)
    assert first["sign_reversal"] is True
    assert result["county_fold_count"] == 3


def test_duplicate_or_invalid_county_folds_are_rejected():
    base = {"region": "maricopa-az", "heldout_county_fips": "04001", "heldout_n": 2,
            "baseline_sse": 1.0, "model_sse": 0.5}
    with pytest.raises(ValueError, match="duplicate"):
        mod.calculate_leave_one_county_out([base, dict(base)])
    invalid = dict(base, baseline_sse=0.0)
    with pytest.raises(ValueError, match="positive"):
        mod.calculate_leave_one_county_out([invalid])


def test_frozen_source_reproduces_full_and_regional_metrics_and_150m_baseline():
    report = mod.build_report(SOURCE)
    assert report["result"] == "PASS"
    assert report["acceptance"]["audit_checks_passed"] is True
    assert report["measure_before"]["source_nested_screen_pass"] is False
    assert report["measure_after"]["county_fold_count"] == 298
    assert report["measure_after"]["county_fold_counts_by_region"] == mod.EXPECTED_REGION_FOLDS
    assert len(report["measure_after"]["leave_one_county_out_pooled_deltas"]) == 298
    assert report["measure_after"]["pooled_delta_r2_recomputed"] == pytest.approx(-0.005331569462452088, abs=1e-10)
    assert report["measure_after"]["sign_reversal_count"] == 0
    assert report["measure_after"]["max_influence_county"]["region"] == "maricopa-az"
    assert report["measure_after"]["max_influence_county"]["heldout_county_fips"] == "04013"
    assert report["acceptance"]["frozen_T5_150m_exactly_matches_source"] is True
    assert report["safety"]["only_frozen_per_county_fold_sse_report_read"] is True


def test_frozen_source_hash_pin_rejects_modified_report(tmp_path):
    modified = tmp_path / "changed.json"
    data = json.loads(SOURCE.read_text())
    data["measure_after"]["outer_county_folds"][0]["baseline_sse"] *= 1.01
    modified.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        mod.build_report(modified)


def test_frozen_source_reports_each_held_county_once_and_paired_deltas():
    report = mod.build_report(SOURCE)
    rows = report["measure_after"]["leave_one_county_out_pooled_deltas"]
    identities = [(row["region"], row["heldout_county_fips"]) for row in rows]
    assert len(identities) == len(set(identities)) == 298
    assert all(row["pooled_baseline_sse_without_county"] > 0 for row in rows)
    assert all(row["absolute_influence_delta_r2"] == abs(row["influence_delta_r2"])
               for row in rows)
    assert all(row["pooled_delta_r2_without_county"] < 0 for row in rows)


def test_only_source_is_the_frozen_t5_report():
    source_text = (ROOT / "scripts/audit_t5_attachment_radius_county_influence.py").read_text()
    assert "T5_ARTIFACT_PATH" not in source_text
    assert "ProvidedInputs" not in source_text
    assert "sample_score_placeholders" in source_text
