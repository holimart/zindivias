#!/usr/bin/env python3
"""Build a non-mutating S projection onto the four official scored GEOID lists.

Only GEOID cells are consumed from official sample files. Only GEOID and the
registered candidate's coverage_gap_score are consumed from the frozen S file;
score cells on the 415 out-of-scope source rows are ignored. The canonical S
artifact and candidate registry are read-only. This is scope reconciliation,
not model evaluation, target inference, or upload authorization.
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
SOURCE_REL = "reports/candidates/S_crossmethod_reliability_shrink_v1.csv"
SOURCE_SHA256 = "26be20c569f72f94eb21711a41656eea909a92f3fb4495c1ec3b30f7a26a367a"
CANDIDATE_ID = "S_crossmethod_reliability_shrink_v1"
OUTPUT_REL = "reports/candidates/S_scored_scope_projection.json"
REGISTRY_REL = "reports/candidates.json"
SAMPLE_HEADER = "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_sample_geoids(path: Path, expected_header: str = SAMPLE_HEADER) -> list[str]:
    """Read only the first CSV field (GEOID); never parse sample score placeholders."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        header = stream.readline().rstrip("\r\n")
        if header != expected_header:
            raise ValueError(f"unexpected official sample header in {path}")
        ids: list[str] = []
        for line_number, line in enumerate(stream, start=2):
            if not line.strip():
                continue
            first, separator, _ignored = line.partition(",")
            geoid = first.rstrip("\r\n")
            if not separator or len(geoid) != 11 or not geoid.isdigit():
                raise ValueError(f"invalid GEOID-only sample row at {path}:{line_number}")
            ids.append(geoid)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"empty or duplicate official GEOIDs in {path}")
    return ids


def read_s_scores(source_path: Path, official_ids: set[str], expected_sha256: str, *,
                  expected_source_rows: int = 9794,
                  expected_out_of_scope_rows: int = 415) -> tuple[list[str], dict[str, float]]:
    """Read source IDs for scope checks and scores only for official IDs."""
    actual_sha = sha256_file(source_path)
    if actual_sha != expected_sha256:
        raise ValueError(f"canonical S SHA-256 mismatch: {actual_sha}")
    source_ids: list[str] = []
    seen_source_ids: set[str] = set()
    scores: dict[str, float] = {}
    with source_path.open("r", encoding="utf-8-sig", newline="") as stream:
        header = stream.readline().rstrip("\r\n")
        if header != "GEOID,coverage_gap_score":
            raise ValueError(f"unexpected S source header: {header}")
        for line_number, raw_line in enumerate(stream, start=2):
            line = raw_line.rstrip("\r\n")
            geoid, separator, score_text = line.partition(",")
            if not separator or "," in score_text:
                raise ValueError(f"malformed S source row at line {line_number}")
            if not geoid or geoid in seen_source_ids:
                raise ValueError(f"empty or duplicate S source GEOID at line {line_number}")
            source_ids.append(geoid)
            seen_source_ids.add(geoid)
            if geoid not in official_ids:
                # Do not parse or use out-of-scope candidate score values.
                continue
            try:
                score = float(score_text)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid official S score at line {line_number}") from exc
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError(f"official S score outside [0,1] at line {line_number}")
            scores[geoid] = score
    if len(source_ids) != expected_source_rows or len(source_ids) != len(seen_source_ids):
        raise ValueError(f"expected {expected_source_rows} unique canonical S rows; found {len(source_ids)}")
    if set(scores) != official_ids:
        missing = len(official_ids - set(scores))
        extra = len(set(scores) - official_ids)
        raise ValueError(f"official score coverage mismatch: missing={missing}, extra={extra}")
    if len(seen_source_ids - official_ids) != expected_out_of_scope_rows:
        raise ValueError(f"expected exactly {expected_out_of_scope_rows} canonical rows outside official scored scope")
    if sha256_file(source_path) != expected_sha256:
        raise ValueError("canonical S changed while being read")
    return source_ids, scores


def validate_sample_lists(sample_ids: Mapping[str, Sequence[str]],
                          expected_region_counts: Mapping[str, int] = EXPECTED_REGION_COUNTS,
                          expected_total: int = 9379) -> list[str]:
    if tuple(sample_ids) != REGIONS:
        raise ValueError("official sample lists must use the four frozen region order")
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
    scores: Mapping[str, float],
    *,
    source_sha256: str,
    source_rows: int,
    source_out_of_scope_rows: int,
    sample_sha256: Mapping[str, str],
    registry_sha256: str,
    expected_region_counts: Mapping[str, int] = EXPECTED_REGION_COUNTS,
    expected_total: int = 9379,
) -> dict[str, Any]:
    official_ids = validate_sample_lists(sample_ids, expected_region_counts, expected_total)
    if set(scores) != set(official_ids):
        raise ValueError("score-map IDs must match official GEOID scope exactly")
    regions: dict[str, Any] = {}
    for region in REGIONS:
        projected_rows = []
        for geoid in sample_ids[region]:
            score = float(scores[geoid])
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError(f"non-finite or out-of-range projected score for {geoid}")
            projected_rows.append({"GEOID": geoid, "coverage_gap_score": score})
        regions[region] = {
            "official_rows": len(projected_rows),
            "sample_sha256": sample_sha256[region],
            "rows": projected_rows,
        }
    return {
        "schema_version": 1,
        "projection_type": "official_scored_scope_only",
        "candidate_id": CANDIDATE_ID,
        "columns": ["GEOID", "coverage_gap_score"],
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
            "scores_finite_and_bounded": True,
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
        "projection_note": "S's frozen official-region predictions are copied by GEOID into the authoritative per-region sample order. The 415 out-of-scope source rows are excluded; this is format/scope reconciliation only, not accuracy evidence.",
        "regions": regions,
    }


def build_projection(workspace: Path, source_path: Path, registry_path: Path, output_path: Path,
                     *, expected_source_sha256: str = SOURCE_SHA256,
                     expected_region_counts: Mapping[str, int] = EXPECTED_REGION_COUNTS,
                     expected_total: int = 9379) -> tuple[dict[str, Any], str]:
    registry_sha = sha256_file(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    matches = [item for item in registry.get("candidate_set", [])
               if item.get("candidate_id") == CANDIDATE_ID]
    if len(matches) != 1:
        raise ValueError("expected exactly one registered S candidate")
    card = matches[0]
    if (card.get("artifact") != SOURCE_REL or card.get("sha256") != expected_source_sha256
            or card.get("upload_authorized") is not False):
        raise ValueError("registered S identity/hash/authorization mismatch")

    sample_ids: dict[str, list[str]] = {}
    sample_hashes: dict[str, str] = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        sample_ids[region] = read_sample_geoids(sample_path)
        sample_hashes[region] = sha256_file(sample_path)
        if region in EXPECTED_SAMPLE_SHA256 and sample_hashes[region] != EXPECTED_SAMPLE_SHA256[region]:
            raise ValueError(f"official sample hash mismatch for {region}")
    official_ids = validate_sample_lists(sample_ids, expected_region_counts, expected_total)
    source_ids, scores = read_s_scores(source_path, set(official_ids), expected_source_sha256)
    document = make_projection_document(
        sample_ids, scores, source_sha256=expected_source_sha256,
        source_rows=len(source_ids),
        source_out_of_scope_rows=len(set(source_ids) - set(official_ids)),
        sample_sha256=sample_hashes, registry_sha256=registry_sha,
        expected_region_counts=expected_region_counts, expected_total=expected_total,
    )
    encoded = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(encoded)
    if sha256_file(source_path) != expected_source_sha256:
        raise ValueError("canonical S source changed during projection build")
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
