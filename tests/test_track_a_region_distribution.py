from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit_track_a_region_distribution.py"
spec=importlib.util.spec_from_file_location("track_a_region_distribution",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_type7_linear_quantiles_are_reproducible():
    values=[0.0,1.0,2.0,3.0]
    assert module.quantile_linear(values,.05)==pytest.approx(.15)
    assert module.quantile_linear(values,.25)==pytest.approx(.75)
    assert module.quantile_linear(values,.5)==pytest.approx(1.5)
    assert module.quantile_linear(values,.95)==pytest.approx(2.85)


def test_finite_share_and_smd_flag_use_pooled_population_standard_deviation():
    pooled=module.pooled_stats([0.0,1.0])
    result=module.regional_distribution([1.0,1.0,None],pooled)
    assert result["scored_ids"]==3
    assert result["finite_ids"]==2
    assert result["finite_share"]==2/3
    assert result["median"]==1.0
    assert result["iqr"]==0.0
    assert result["standardized_mean_difference_from_pooled"]==1.0
    assert result["absolute_smd_exceeds_0_5"] is True


def test_smd_threshold_is_strictly_greater_than_point_five():
    pooled=module.pooled_stats([0.0,1.0])
    result=module.regional_distribution([.75,.75],pooled)
    assert result["standardized_mean_difference_from_pooled"]==.5
    assert result["absolute_smd_exceeds_0_5"] is False


def test_nonfinite_pooled_values_are_rejected():
    try:
        module.pooled_stats([1.0,float("nan")])
    except ValueError as exc:
        assert "finite" in str(exc)
    else:
        raise AssertionError("pooled summary must not accept a nonfinite value")


def test_candidate_reader_checks_order_and_does_not_parse_template_only_scores(tmp_path,monkeypatch):
    repo=tmp_path/"repo";artifact=repo/"reports/candidates/A1.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n"
        "01,0.1,0.2,0.3,0.2\n"
        "02,bad,nan,0.4,0.5\n"
        "template-only,DO_NOT_PARSE,DO_NOT_PARSE,DO_NOT_PARSE,DO_NOT_PARSE\n")
    monkeypatch.setattr(module,"ROOT",repo)
    registration={"artifact":"reports/candidates/A1.csv",
        "sha256":hashlib.sha256(artifact.read_bytes()).hexdigest(),"rows":3,
        "columns":["GEOID","transport_gap","building_gap","poi_gap","coverage_gap_score"]}
    result,meta=module.read_registered_candidate(artifact,registration,{"01":"r","02":"r"},{"r":["01","02"]})
    assert result["01"]["transport_gap"]==.1
    assert result["02"]["transport_gap"] is None
    assert result["02"]["__numeric__transport_gap"]==0.0
    assert result["02"]["building_gap"] is None
    assert meta["scored_rows"]==2
    assert meta["non_scored_rows_excluded_without_numeric_value_parsing"]==1
    assert meta["ordered_scored_ids_match_official_order"] is True
