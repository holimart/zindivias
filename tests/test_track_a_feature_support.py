from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit_track_a_feature_support.py"
spec=importlib.util.spec_from_file_location("track_a_feature_support",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_mask_stats_reports_missingness_and_ordered_hash():
    ids=["a","b","c"]
    result=module.mask_stats(ids,{"a","c"})
    assert result["defined_ids"]==2
    assert result["missing_ids"]==1
    assert result["defined_fraction"]==pytest.approx(2/3)
    assert result["missing_fraction"]==pytest.approx(1/3)
    assert result["defined_mask_sha256_official_order"]==hashlib.sha256(bytes([1,0,1])).hexdigest()


def test_pairwise_mask_coverage_is_explicit_and_directional():
    result=module.pair_stats(["a","b","c","d"],{"a","b"},{"b","c"})
    assert result["both_defined_ids"]==1
    assert result["union_defined_ids"]==3
    assert result["both_defined_fraction_of_region"]==pytest.approx(.25)
    assert result["left_covered_by_right_fraction"]==pytest.approx(.5)
    assert result["right_covered_by_left_fraction"]==pytest.approx(.5)
    assert result["jaccard_intersection_over_union"]==pytest.approx(1/3)


def test_final_two_projection_skips_unneeded_score_fields(tmp_path):
    path=tmp_path/"final-two.json"
    row={"candidate_id":"A1","artifact":"candidate.csv","sha256":"abc","rows":9794,
         "columns":["GEOID","coverage_gap_score"],"parameter_provenance":{"manifest_sha256":"def"},
         "public_rmse":123.0,"submission_id":"not-used"}
    path.write_text(json.dumps({"candidate_registrations":[row],"interim_public_ranking":["ignored"]}))
    result,digest=module.load_final_two_registrations(path,expected_ids=("A1",))
    assert set(result)=={"A1"}
    assert "public_rmse" not in result["A1"]
    assert result["A1"]["parameter_provenance"]=={"manifest_sha256":"def"}
    assert digest==hashlib.sha256(path.read_bytes()).hexdigest()


def test_idea_loader_counts_explicit_undefined_and_rejects_nonzero_placeholder(tmp_path):
    path=tmp_path/"G3.parquet"
    table=pa.table({"GEOID":["a","b"],"region":["r","r"],"idea":["G3","G3"],
        "family":["G","G"],"component":["B","B"],"channel":["gap","gap"],
        "value":[.25,0.],"defined":[True,False]})
    pq.write_table(table,path)
    mask,meta=module.load_idea_mask(path,hashlib.sha256(path.read_bytes()).hexdigest(),"G3","B",["a","b"],{"a":"r","b":"r"})
    assert mask=={"a"}
    assert meta["rows"]==2 and meta["missing_emission_rows"]==0
    assert meta["undefined_placeholder_nonzero_rows"]==0


def test_idea_loader_fails_on_unexplained_missing_official_id(tmp_path):
    path=tmp_path/"G3.parquet"
    pq.write_table(pa.table({"GEOID":["a"],"region":["r"],"idea":["G3"],"family":["G"],
        "component":["B"],"channel":["gap"],"value":[.2],"defined":[True]}),path)
    with pytest.raises(ValueError,match="missing=1"):
        module.load_idea_mask(path,hashlib.sha256(path.read_bytes()).hexdigest(),"G3","B",["a","b"],{"a":"r","b":"r"})
