from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit_track_a_region_rank_concordance.py"
spec=importlib.util.spec_from_file_location("track_a_region_rank_concordance",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_average_ranks_use_midrank_for_exact_ties():
    assert module.average_ranks([4.0,1.0,1.0,3.0])==[4.0,1.5,1.5,3.0]


def test_spearman_perfect_and_reverse_order_with_ties():
    assert module.spearman_rho([1,1,2,3],[10,10,20,30])==pytest.approx(1.0)
    assert module.spearman_rho([1,1,2,3],[30,30,20,10])==pytest.approx(-1.0)


def test_tie_rates_report_observations_and_pairs():
    stats=module.tie_summary([2,2,2,3,4,4])
    assert stats["n"]==6
    assert stats["tie_groups"]==2
    assert stats["tied_observations"]==5
    assert stats["tied_observation_fraction"]==pytest.approx(5/6)
    assert stats["tied_observation_pairs"]==4
    assert stats["all_observation_pairs"]==15
    assert stats["tied_pair_fraction"]==pytest.approx(4/15)


def test_spearman_is_undefined_for_constant_rank_vector():
    assert module.spearman_rho([1,1,1],[1,2,3]) is None


def test_candidate_reader_uses_only_scored_rows_and_checks_official_order(tmp_path,monkeypatch):
    repo=tmp_path/"repo";candidate=repo/"reports/candidates/A1.csv"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(
        "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n"
        "02,ignored,ignored,ignored,0.2\n"
        "01,ignored,ignored,ignored,0.8\n"
        "template-only,IGNORE_ME,IGNORE_ME,IGNORE_ME,NOT_NUMERIC\n")
    monkeypatch.setattr(module,"ROOT",repo)
    registration={"artifact":"reports/candidates/A1.csv","sha256":hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "rows":3,"columns":["GEOID","transport_gap","building_gap","poi_gap","coverage_gap_score"]}
    with pytest.raises(ValueError,match="regional GEOID order"):
        module.read_candidate(candidate,registration,{"01":"r","02":"r"},{"r":["01","02"]})
    # Correct the official order, while retaining the unparsed template-only score.
    candidate.write_text(
        "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n"
        "01,ignored,ignored,ignored,0.8\n"
        "02,ignored,ignored,ignored,0.2\n"
        "template-only,IGNORE_ME,IGNORE_ME,IGNORE_ME,NOT_NUMERIC\n")
    registration["sha256"]=hashlib.sha256(candidate.read_bytes()).hexdigest()
    values,meta=module.read_candidate(candidate,registration,{"01":"r","02":"r"},{"r":["01","02"]})
    assert values=={"01":0.8,"02":0.2}
    assert meta["official_scored_rows"]==2
    assert meta["non_scored_rows_excluded"]==1
    assert meta["official_order_matches_by_region"] is True


def test_candidate_reader_rejects_duplicate_template_ids(tmp_path,monkeypatch):
    repo=tmp_path/"repo";candidate=repo/"reports/candidates/A1.csv"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(
        "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n"
        "01,0,0,0,0.1\n01,0,0,0,0.2\n")
    monkeypatch.setattr(module,"ROOT",repo)
    registration={"artifact":"reports/candidates/A1.csv","sha256":hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "rows":2,"columns":["GEOID","transport_gap","building_gap","poi_gap","coverage_gap_score"]}
    with pytest.raises(ValueError,match="duplicate artifact GEOID"):
        module.read_candidate(candidate,registration,{"01":"r"},{"r":["01"]})
