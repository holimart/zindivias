from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "screen_track_a_family_median_aggregator",
    ROOT / "scripts/screen_track_a_family_median_aggregator.py",
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_median_even_and_odd_and_linear_p95():
    assert mod.median([0.4, 0.1, 0.3, 0.2]) == pytest.approx(0.25)
    assert mod.median([3.0, 1.0, 2.0]) == pytest.approx(2.0)
    assert mod.linear_quantile([0.0, 10.0], 0.95) == pytest.approx(9.5)
    with pytest.raises(ValueError, match="at least one"):
        mod.median([])


def test_leave_one_candidate_out_delta_and_regional_maxima():
    candidates = {
        "A1_physical_equal": {"id1": 0.1, "id2": 0.0, "id3": 0.2, "id4": 0.8},
        "A2_spectral": {"id1": 0.2, "id2": 0.1, "id3": 0.3, "id4": 0.7},
        "A3_dawid_skene": {"id1": 0.3, "id2": 0.2, "id3": 0.4, "id4": 0.6},
        "V2_model": {"id1": 0.9, "id2": 0.3, "id3": 0.5, "id4": 0.1},
    }
    ordered = ["id1", "id2", "id3", "id4"]
    region = dict(zip(ordered, mod.REGIONS))
    rows, summary = mod.aggregate_median(ordered, region, candidates)
    assert [row["coverage_gap_score"] for row in rows] == pytest.approx([0.25, 0.15, 0.35, 0.65])
    assert summary["region_summary"]["maricopa-az"]["official_ids"] == 1
    assert summary["region_summary"]["maricopa-az"]["all_four_omissions_maximum_absolute_delta"] == pytest.approx(0.05)
    assert all(0.0 <= row["coverage_gap_score"] <= 1.0 for row in rows)


def test_id_only_reader_does_not_interpret_sample_score_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "REGIONS", ("tiny-region",))
    monkeypatch.setattr(mod, "REGION_COUNTS", {"tiny-region": 2})
    monkeypatch.setattr(mod, "OFFICIAL_ID_COUNT", 2)
    monkeypatch.setattr(mod, "SAMPLE_FILES", {"tiny-region": "samples/ids.csv"})
    path = tmp_path / "samples/ids.csv"
    path.parent.mkdir()
    path.write_text("GEOID,score\n00000000001,not-read-as-label\n00000000002,also-ignored\n")
    ids, region_by_id, digests = mod.read_official_ids(tmp_path)
    assert ids == ["00000000001", "00000000002"]
    assert region_by_id == {ids[0]: "tiny-region", ids[1]: "tiny-region"}
    assert len(digests["tiny-region"]) == 64


def test_nonofficial_candidate_score_is_not_parsed(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    path = tmp_path / "registered.csv"
    path.write_text("GEOID,component,coverage_gap_score\n00000000001,ignored,0.25\n00000000002,x,invalid-extra-score\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    scores, metadata = mod.read_candidate_composites(path, digest, 2, {"00000000001"})
    assert scores == {"00000000001": 0.25}
    assert metadata["official_scores_read"] == 1


def test_registered_candidate_hash_and_official_coverage_are_enforced(tmp_path):
    path = tmp_path / "candidate.csv"
    path.write_text("GEOID,coverage_gap_score\n00000000001,0.5\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        mod.read_candidate_composites(path, "0" * 64, 1, {"00000000001"})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="coverage is incomplete"):
        mod.read_candidate_composites(path, digest, 1, {"00000000001", "00000000002"})


def test_frozen_baseline_source_hash_is_pinned():
    source = ROOT / mod.SOURCE_REPORT_REL
    assert mod.sha256_file(source) == mod.SOURCE_REPORT_SHA256
