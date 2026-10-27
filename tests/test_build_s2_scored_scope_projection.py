from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_s2_scored_scope_projection",
    ROOT / "scripts/build_s2_scored_scope_projection.py",
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _sample_ids():
    return {
        "maricopa-az": ["00123456789", "00987654321"],
        "northern-ca": [], "eastern-ok": [], "south-central-tx": [],
    }


def _expected_counts():
    return {"maricopa-az": 2, "northern-ca": 0, "eastern-ok": 0, "south-central-tx": 0}


def _scores():
    return {
        "00123456789": {column: 0.2 for column in mod.SCORE_COLUMNS},
        "00987654321": {column: 0.8 for column in mod.SCORE_COLUMNS},
    }


def test_sample_reader_uses_only_geoid_field_even_with_non_numeric_placeholders(tmp_path: Path):
    sample = tmp_path / "sample.csv"
    sample.write_text(
        "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n"
        "00123456789,not-a-score,not-a-score,not-a-score,not-a-score\n"
        "00987654321,,,,\n",
        encoding="utf-8",
    )
    assert mod.read_sample_geoids(sample) == ["00123456789", "00987654321"]


def test_source_reader_ignores_out_of_scope_scores_and_keeps_official_component_values(tmp_path: Path):
    source = tmp_path / "S2.csv"
    source.write_text(
        "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n"
        "00123456789,0.1,0.2,0.3,0.4\n"
        "00987654321,0.5,0.6,0.7,0.8\n"
        "99000000000,not-a-score,not-a-score,not-a-score,not-a-score\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source_ids, scores = mod.read_s2_scores(
        source, {"00123456789", "00987654321"}, digest,
        expected_source_rows=3, expected_out_of_scope_rows=1,
    )
    assert source_ids == ["00123456789", "00987654321", "99000000000"]
    assert scores["00123456789"] == dict(zip(mod.SCORE_COLUMNS, (0.1, 0.2, 0.3, 0.4)))
    assert scores["00987654321"] == dict(zip(mod.SCORE_COLUMNS, (0.5, 0.6, 0.7, 0.8)))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_projection_preserves_sample_order_and_is_deterministic():
    document = mod.make_projection_document(
        _sample_ids(), _scores(), source_sha256="a" * 64, source_rows=3,
        source_out_of_scope_rows=1, sample_sha256={region: "b" * 64 for region in mod.REGIONS},
        registry_sha256="c" * 64, expected_region_counts=_expected_counts(), expected_total=2,
    )
    rows = document["regions"]["maricopa-az"]["rows"]
    assert [row["GEOID"] for row in rows] == _sample_ids()["maricopa-az"]
    assert list(rows[0]) == ["GEOID", *mod.SCORE_COLUMNS]
    assert rows[0]["transport_gap"] == rows[0]["coverage_gap_score"] == 0.2
    assert document["scope"]["missing_ids"] == document["scope"]["extra_ids"] == 0
    encoded = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    again = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    assert encoded == again


def test_rejects_missing_ids_duplicate_ids_and_bad_values():
    kwargs = dict(
        source_sha256="a" * 64, source_rows=2, source_out_of_scope_rows=0,
        sample_sha256={region: "b" * 64 for region in mod.REGIONS}, registry_sha256="c" * 64,
        expected_region_counts=_expected_counts(), expected_total=2,
    )
    with pytest.raises(ValueError, match="score-map IDs"):
        mod.make_projection_document(_sample_ids(), {}, **kwargs)
    duplicate = _sample_ids()
    duplicate["maricopa-az"] = ["00123456789", "00123456789"]
    with pytest.raises(ValueError, match="count/uniqueness"):
        mod.make_projection_document(duplicate, _scores(), **kwargs)
    for bad in (-0.01, 1.01, float("nan"), float("inf")):
        values = _scores()
        values["00123456789"]["poi_gap"] = bad
        with pytest.raises(ValueError, match="out-of-range"):
            mod.make_projection_document(_sample_ids(), values, **kwargs)
