#!/usr/bin/env python3
"""Project frozen S2 predictions to the authoritative four-region ID scope.

Only GEOID fields are consumed from official sample files. The frozen S2 source
is read-only: score fields are parsed only for official IDs and are copied into
a distinct deterministic JSON projection. The 415 template-only rows are
excluded. This is format/scope validation, not score inference or upload
authorization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_REGION_COUNTS = {
    "maricopa-az": 1593,
    "northern-ca": 591,
    "eastern-ok": 1192,
    "south-central-tx": 6003,
}
EXPECTED_SAMPLE_SHA256 = {
    "maricopa-az": "1640b801d56ab308e68c65583b88cfb9e1555b05a6d0b922eaba22ac09960a31",
    "northern-ca": "f38017b5284667b80b451685fd146388c108e1b30308301289c7a96ecfe60902",
    "eastern-ok": "56cb40fba4008d646bc63c62c91e211871459224b3de8b3a886ff813803fd0d4",
    "south-central-tx": "afa59c0eb30c40be25ce82098f144447591b79cb9692e669b2f2fe7b7b1ee58e",
}
CANDIDATE_ID = "S2_h1b_assumption_bound_v1"
SOURCE_REL = f"reports/candidates/{CANDIDATE_ID}.csv"
SOURCE_SHA256 = "44ba76abcd9e1fb0aa7194e3c0fadf567debce40e29ed4bdf93ef02ae9514577"
OUTPUT_REL = "reports/candidates/S2_scored_scope_projection.json"
REGISTRY_REL = "reports/candidates.json"
SOURCE_HEADER = ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]
SCORE_COLUMNS = ("transport_gap", "building_gap", "poi_gap", "coverage_gap_score")
SAMPLE_HEADER = "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_sample_geoids(path: Path, expected_header: str = SAMPLE_HEADER) -> list[str]:
    """Read only the first CSV field; sample score placeholders are ignored."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        header = stream.readline().rstrip("\r\n")
        if header != expected_header:
            raise ValueError(f"unexpected official sample header in {path}")
        ids: list[str] = []
        for line_number, line in enumerate(stream, start=2):
            if not line.strip():
                continue
            geoid, separator, _ignored = line.partition(",")
            geoid = geoid.rstrip("\r\n")
            if not separator or len(geoid) != 11 or not geoid.isdigit():
                raise ValueError(f"invalid GEOID-only sample row at {path}:{line_number}")
            ids.append(geoid)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"empty or duplicate official GEOIDs in {path}")
    return ids


def read_s2_scores(source_path: Path, official_ids: set[str], expected_sha256: str, *,
                   expected_source_rows: int = 9794,
                   expected_out_of_scope_rows: int = 415) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Read all source GEOIDs, but score cells only for official IDs."""
    actual_sha = sha256_file(source_path)
    if actual_sha != expected_sha256:
        raise ValueError(f"canonical S2 SHA-256 mismatch: {actual_sha}")
    source_ids: list[str] = []
    seen: set[str] = set()
    scores: dict[str, dict[str, float]] = {}
    with source_path.open("r", encoding="utf-8-sig", newline="") as stream:
        header = stream.readline().rstrip("\r\n").split(",")
        if header != SOURCE_HEADER:
            raise ValueError(f"unexpected S2 source header: {header}")
        for line_number, raw_line in enumerate(stream, start=2):
            geoid, separator, remaining = raw_line.rstrip("\r\n").partition(",")
            if not separator or not geoid or geoid in seen:
                raise ValueError(f"empty/duplicate GEOID or malformed row at S2 line {line_number}")
            seen.add(geoid)
            source_ids.append(geoid)
            if geoid not in official_ids:
                # The four out-of-scope component/composite values are never parsed or used.
                continue
            fields = remaining.split(",")
            if len(fields) != len(SCORE_COLUMNS):
                raise ValueError(f"malformed official S2 score row at line {line_number}")
            row_values: dict[str, float] = {}
            for column, raw_value in zip(SCORE_COLUMNS, fields):
                try:
                    value = float(raw_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid official S2 {column} at line {line_number}") from exc
                if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise ValueError(f"official S2 {column} outside [0,1] at line {line_number}")
                row_values[column] = value
            scores[geoid] = row_values
    if len(source_ids) != expected_source_rows:
        raise ValueError(f"expected {expected_source_rows} unique canonical S2 rows; found {len(source_ids)}")
    if set(scores) != official_ids:
        raise ValueError(f"S2 official ID coverage mismatch: missing={len(official_ids - set(scores))}, extra={len(set(scores) - official_ids)}")
    if len(seen - official_ids) != expected_out_of_scope_rows:
        raise ValueError(f"expected exactly {expected_out_of_scope_rows} source rows outside official scope")
    if sha256_file(source_path) != expected_sha256:
        raise ValueError("canonical S2 changed while being read")
    return source_ids, scores


def validate_sample_lists(sample_ids: Mapping[str, Sequence[str]],
                          expected_region_counts: Mapping[str, int] = EXPECTED_REGION_COUNTS,
                          expected_total: int = 9379) -> list[str]:
    if tuple(sample_ids) != REGIONS:
        raise ValueError("official sample lists must use the frozen four-region order")
    all_ids: list[str] = []
    for region in REGIONS:
        ids = list(sample_ids[region])
        if len(ids) != expected_region_counts[region] or len(ids) != len(set(ids)):
            raise ValueError(f"official GEOID count/uniqueness failure for {region}")
        all_ids.extend(ids)
    if len(all_ids) != expected_total or len(all_ids) != len(set(all_ids)):
        raise ValueError("official GEOID lists must contain exactly 9,379 globally unique IDs")
    return all_ids


def make_projection_document(
    sample_ids: Mapping[str, Sequence[str]],
    scores: Mapping[str, Mapping[str, float]],
    *, source_sha256: str, source_rows: int, source_out_of_scope_rows: int,
    sample_sha256: Mapping[str, str], registry_sha256: str,
    expected_region_counts: Mapping[str, int] = EXPECTED_REGION_COUNTS,
    expected_total: int = 9379,
) -> dict[str, Any]:
    official_ids = validate_sample_lists(sample_ids, expected_region_counts, expected_total)
    if set(scores) != set(official_ids):
        raise ValueError("score-map IDs must match official GEOID scope exactly")
    regions: dict[str, Any] = {}
    for region in REGIONS:
        rows: list[dict[str, Any]] = []
        for geoid in sample_ids[region]:
            score_row = scores[geoid]
            if set(score_row) != set(SCORE_COLUMNS):
                raise ValueError(f"S2 score columns do not match expected columns for {geoid}")
            projected = {"GEOID": geoid}
            for column in SCORE_COLUMNS:
                value = float(score_row[column])
                if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise ValueError(f"non-finite or out-of-range {column} for {geoid}")
                projected[column] = value
            rows.append(projected)
        regions[region] = {
            "official_rows": len(rows),
            "sample_sha256": sample_sha256[region],
            "rows": rows,
        }
    return {
        "schema_version": 1,
        "projection_type": "official_scored_scope_only",
        "candidate_id": CANDIDATE_ID,
        "columns": ["GEOID", *SCORE_COLUMNS],
        "source": {
            "artifact": SOURCE_REL,
            "sha256": source_sha256,
            "rows": source_rows,
            "out_of_scope_rows_excluded": source_out_of_scope_rows,
            "canonical_source_modified": False,
        },
        "scope": {
            "official_regions_in_output_order": list(REGIONS),
            "official_rows": expected_total,
            "unique_geoids": expected_total,
            "missing_ids": 0,
            "extra_ids": 0,
            "duplicate_ids": 0,
            "all_score_columns_finite_and_bounded": True,
            "target_accuracy_claim": False,
            "upload_authorized": False,
        },
        "candidate_registry": {
            "path": REGISTRY_REL,
            "sha256": registry_sha256,
            "candidate_upload_authorized": False,
            "registry_modified": False,
        },
        "sample_score_placeholders_read": False,
        "projection_note": "Frozen S2 component and composite predictions are copied by GEOID in authoritative per-region sample order. The 415 out-of-scope formatting rows are excluded. This projection is scope reconciliation only, not accuracy evidence or a changed candidate.",
        "regions": regions,
    }


def build_projection(workspace: Path, source_path: Path, registry_path: Path, output_path: Path,
                     *, expected_source_sha256: str = SOURCE_SHA256,
                     expected_region_counts: Mapping[str, int] = EXPECTED_REGION_COUNTS,
                     expected_total: int = 9379) -> tuple[dict[str, Any], str]:
    registry_sha = sha256_file(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    cards = [item for item in registry.get("candidate_set", []) if item.get("candidate_id") == CANDIDATE_ID]
    if len(cards) != 1:
        raise ValueError("expected exactly one registered S2 candidate")
    card = cards[0]
    if (card.get("artifact") != SOURCE_REL or card.get("sha256") != expected_source_sha256
            or card.get("upload_authorized") is not False):
        raise ValueError("registered S2 identity/hash/authorization mismatch")

    sample_ids: dict[str, list[str]] = {}
    sample_hashes: dict[str, str] = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        sample_ids[region] = read_sample_geoids(sample_path)
        sample_hashes[region] = sha256_file(sample_path)
        if region in EXPECTED_SAMPLE_SHA256 and sample_hashes[region] != EXPECTED_SAMPLE_SHA256[region]:
            raise ValueError(f"official sample hash mismatch for {region}")
    official_ids = validate_sample_lists(sample_ids, expected_region_counts, expected_total)
    source_ids, scores = read_s2_scores(source_path, set(official_ids), expected_source_sha256)
    document = make_projection_document(
        sample_ids, scores, source_sha256=expected_source_sha256,
        source_rows=len(source_ids), source_out_of_scope_rows=len(set(source_ids) - set(official_ids)),
        sample_sha256=sample_hashes, registry_sha256=registry_sha,
        expected_region_counts=expected_region_counts, expected_total=expected_total,
    )
    encoded = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(encoded)
    if sha256_file(source_path) != expected_source_sha256:
        raise ValueError("canonical S2 source changed during projection build")
    if sha256_file(registry_path) != registry_sha:
        raise ValueError("candidate registry changed during projection build")
    return document, hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--source", type=Path, default=ROOT / SOURCE_REL)
    parser.add_argument("--registry", type=Path, default=ROOT / REGISTRY_REL)
    parser.add_argument("--out", type=Path, default=ROOT / OUTPUT_REL)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    document, projection_sha = build_projection(args.workspace, args.source, args.registry, args.out)
    source_sha_after = sha256_file(args.source)
    print(json.dumps({
        "output": str(args.out),
        "projection_sha256": projection_sha,
        "source_sha256_before_and_after": source_sha_after,
        "source_unchanged": source_sha_after == SOURCE_SHA256,
        "official_rows": document["scope"]["official_rows"],
        "unique_geoids": document["scope"]["unique_geoids"],
        "missing_ids": document["scope"]["missing_ids"],
        "extra_ids": document["scope"]["extra_ids"],
        "duplicate_ids": document["scope"]["duplicate_ids"],
        "upload_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
