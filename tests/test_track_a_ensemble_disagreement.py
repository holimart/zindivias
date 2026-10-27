from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit_track_a_ensemble_disagreement.py"
spec=importlib.util.spec_from_file_location("track_a_ensemble_disagreement",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_tract_metrics_are_unscaled_mad_and_linear_p90_minus_p10():
    values=np.array([[0.,.2,.4,.8],[.5,.5,.5,.5]])
    mad,spread=module.per_tract_metrics(values)
    assert mad.tolist()==pytest.approx([.2,0.])
    assert spread.tolist()==pytest.approx([.62,0.])


def test_high_spread_boundary_is_inclusive_at_preregistered_threshold():
    threshold=.1
    spread=np.array([threshold-1e-8,threshold,threshold+1e-8])
    assert (spread>=threshold).tolist()==[False,True,True]


def test_distribution_contains_finite_population_summaries():
    result=module.distribution(np.array([0.,.5,1.]))
    assert result["count"]==3
    assert result["mean"]==pytest.approx(.5)
    assert result["quantiles_linear"]==pytest.approx({"p50":.5,"p90":.9,"p95":.95,"p99":.99,"p100":1.})


def test_final_two_registration_projection_ignores_score_fields(tmp_path):
    path=tmp_path/"manifest.json"
    row={"candidate_id":"C","artifact":"x.csv","sha256":"abc","rows":2,
         "columns":["GEOID","coverage_gap_score"],"public_rmse":12345,"submission_id":"must-not-be-used"}
    path.write_text(json.dumps({"candidate_registrations":[row]}))
    selected,digest=module.load_registrations(path,expected_ids=("C",))
    assert set(selected["C"])=={"candidate_id","artifact","sha256","rows","columns"}
    assert "public_rmse" not in selected["C"]
    assert digest==hashlib.sha256(path.read_bytes()).hexdigest()


def test_candidate_scores_align_exact_official_ids_and_drop_template_only(tmp_path):
    path=tmp_path/"candidate.csv"
    path.write_text("GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n"
                    "0002,0,0,0,0.25\n0001,0,0,0,0.75\nWA_ONLY,0,0,0,0\n")
    values,scope=module.load_candidate_scores(path,hashlib.sha256(path.read_bytes()).hexdigest(),["0001","0002"],expected_count=2)
    assert values=={"0002":.25,"0001":.75}
    assert scope=={"rows_in_artifact":3,"official_rows_used":2,"nonofficial_rows_excluded":1}


def test_candidate_coverage_mismatch_fails(tmp_path):
    path=tmp_path/"candidate.csv"
    path.write_text("GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n0001,0,0,0,0.25\n")
    with pytest.raises(ValueError,match="coverage mismatch"):
        module.load_candidate_scores(path,hashlib.sha256(path.read_bytes()).hexdigest(),["0001","0002"],expected_count=2)
