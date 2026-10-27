from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_shrink_scored_scope_projection",
    ROOT / "scripts/build_shrink_scored_scope_projection.py",
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_sample_reader_uses_only_geoid_field_even_with_non_numeric_placeholders(tmp_path: Path):
    sample = tmp_path / "sample.csv"
    sample.write_text(
        "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n"
        "00123456789,not-a-score,not-a-score,not-a-score,not-a-score\n"
        "00987654321,,,,\n",
        encoding="utf-8",
    )
    assert mod.read_sample_geoids(sample) == ["00123456789", "00987654321"]


def test_source_reader_skips_out_of_scope_score_and_projects_only_official_ids(tmp_path: Path):
    source = tmp_path / "S.csv"
    source.write_text(
        "GEOID,coverage_gap_score\n"
        "00123456789,0.25\n"
        "00987654321,0.75\n"
        "99000000000,not-a-score\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source_ids, scores = mod.read_s_scores(
        source, {"00123456789", "00987654321"}, digest,
        expected_source_rows=3, expected_out_of_scope_rows=1,
    )
    assert source_ids == ["00123456789", "00987654321", "99000000000"]
    assert scores == {"00123456789": 0.25, "00987654321": 0.75}
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_projection_document_preserves_sample_order_and_serializes_deterministically():
    ids = {
        "maricopa-az": ["00123456789", "00987654321"],
        "northern-ca": [], "eastern-ok": [], "south-central-tx": [],
    }
    document = mod.make_projection_document(
        ids, {"00123456789": 0.25, "00987654321": 0.75},
        source_sha256="a" * 64, source_rows=3, source_out_of_scope_rows=1,
        sample_sha256={region: "b" * 64 for region in mod.REGIONS},
        registry_sha256="c" * 64,
        expected_region_counts={"maricopa-az": 2, "northern-ca": 0, "eastern-ok": 0, "south-central-tx": 0},
        expected_total=2,
    )
    assert [row["GEOID"] for row in document["regions"]["maricopa-az"]["rows"]] == ids["maricopa-az"]
    assert document["regions"]["maricopa-az"]["rows"][0]["coverage_gap_score"] == 0.25
    assert document["scope"]["missing_ids"] == document["scope"]["extra_ids"] == 0
    encoded = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    encoded_again = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    assert encoded == encoded_again


def test_rejects_missing_duplicate_and_out_of_range_official_scores():
    base = {"maricopa-az": ["00123456789"], "northern-ca": [], "eastern-ok": [], "south-central-tx": []}
    kwargs = dict(
        source_sha256="a" * 64, source_rows=1, source_out_of_scope_rows=0,
        sample_sha256={region: "b" * 64 for region in mod.REGIONS}, registry_sha256="c" * 64,
        expected_region_counts={"maricopa-az": 1, "northern-ca": 0, "eastern-ok": 0, "south-central-tx": 0},
        expected_total=1,
    )
    with pytest.raises(ValueError, match="score-map IDs"):
        mod.make_projection_document(base, {}, **kwargs)
    with pytest.raises(ValueError, match="unique"):
        duplicate = dict(base)
        duplicate["maricopa-az"] = ["00123456789", "00123456789"]
        mod.make_projection_document(duplicate, {"00123456789": 0.5}, **kwargs)
    for bad in (-0.01, 1.01, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="projected score"):
            mod.make_projection_document(base, {"00123456789": bad}, **kwargs)
