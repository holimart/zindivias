#!/usr/bin/env python3
"""Measure rank stability between frozen, label-free S2 and S2b outputs only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591, "eastern-ok": 1192, "south-central-tx": 6003}
SAMPLE_HASHES = {
    "maricopa-az": "1640b801d56ab308e68c65583b88cfb9e1555b05a6d0b922eaba22ac09960a31",
    "northern-ca": "f38017b5284667b80b451685fd146388c108e1b30308301289c7a96ecfe60902",
    "eastern-ok": "56cb40fba4008d646bc63c62c91e211871459224b3de8b3a886ff813803fd0d4",
    "south-central-tx": "afa59c0eb30c40be25ce82098f144447591b79cb9692e669b2f2fe7b7b1ee58e",
}
S2_PATH = ROOT / "reports/candidates/S2_h1b_assumption_bound_v1.csv"
S2B_PATH = ROOT / "reports/candidates/S2b_c2_expected_facility_mask_v1.csv"
S2_SHA256 = "44ba76abcd9e1fb0aa7194e3c0fadf567debce40e29ed4bdf93ef02ae9514577"
S2B_SHA256 = "5761e2c17f25f66bbc9e1d769a04b76545e4cc660a8ca9481c30b89739b7ae6f"
EXPECTED_HEADER = ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]
REPORT = ROOT / "reports/s2-s2b-tract-rank-sensitivity.json"
SCORES = ROOT / "reports/s2-s2b-tract-rank-sensitivity-scores.csv"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_official_ids(workspace: Path) -> tuple[dict[str, list[str]], dict[str, str], dict[str, str]]:
    ids_by_region: dict[str, list[str]] = {}
    owner: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for region in REGIONS:
        path = workspace / "samples" / f"{region}-sample-submission.csv"
        digest = sha256_file(path)
        if digest != SAMPLE_HASHES[region]:
            raise ValueError(f"official sample hash mismatch: {region}")
        ids = []
        with path.open(encoding="utf-8-sig", newline="") as stream:
            header = stream.readline().rstrip("\r\n")
            if header != ",".join(EXPECTED_HEADER):
                raise ValueError(f"unexpected official sample header: {region}")
            for line in stream:
                if not line.strip():
                    continue
                geoid, comma, _unused_values = line.rstrip("\r\n").partition(",")
                if not comma or len(geoid) != 11 or not geoid.isdigit():
                    raise ValueError(f"invalid official GEOID in {region}")
                ids.append(geoid)
        if len(ids) != REGION_COUNTS[region] or len(ids) != len(set(ids)):
            raise ValueError(f"official ID count or uniqueness mismatch: {region}")
        for geoid in ids:
            if geoid in owner:
                raise ValueError(f"duplicate GEOID across official regions: {geoid}")
            owner[geoid] = region
        ids_by_region[region] = ids
        hashes[region] = digest
    if len(owner) != 9379:
        raise ValueError(f"expected 9,379 official IDs, got {len(owner)}")
    return ids_by_region, owner, hashes


def read_candidate_scores(path: Path, expected_sha256: str, official_ids: set[str],
                          expected_template_rows: int = 9794) -> tuple[dict[str, float], int]:
    """Read official composite values only; out-of-scope score cells are skipped."""
    actual_hash = sha256_file(path)
    if actual_hash != expected_sha256:
        raise ValueError(f"frozen candidate hash mismatch: {path.name}")
    result: dict[str, float] = {}
    all_ids: set[str] = set()
    rows = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        header = stream.readline().rstrip("\r\n")
        if header != ",".join(EXPECTED_HEADER):
            raise ValueError(f"unexpected candidate header: {path.name}")
        for line_no, line in enumerate(stream, start=2):
            if not line.strip():
                continue
            geoid, comma, remainder = line.rstrip("\r\n").partition(",")
            if not comma or not geoid or geoid in all_ids:
                raise ValueError(f"empty or duplicate candidate GEOID at row {line_no}: {path.name}")
            all_ids.add(geoid)
            rows += 1
            if geoid not in official_ids:
                # Do not split, parse or use score values outside official scope.
                continue
            fields = remainder.split(",")
            if len(fields) != len(EXPECTED_HEADER) - 1:
                raise ValueError(f"malformed official candidate row {line_no}: {path.name}")
            try:
                score = float(fields[-1])
            except ValueError as exc:
                raise ValueError(f"invalid official composite score at row {line_no}: {path.name}") from exc
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError(f"official composite score outside [0,1] at row {line_no}: {path.name}")
            result[geoid] = score
    if rows != expected_template_rows or len(all_ids) != expected_template_rows:
        raise ValueError(f"candidate template row count mismatch: {path.name}, rows={rows}")
    if set(result) != official_ids or len(result) != len(official_ids):
        raise ValueError(f"candidate official ID set mismatch: {path.name}")
    return result, rows


def rankdata(values: Sequence[float]) -> list[float]:
    """Return one-based average ranks for ties."""
    ordered = sorted(enumerate(map(float, values)), key=lambda x: x[1])
    ranks = [0.0] * len(ordered)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = ((start + 1) + end) / 2.0
        for pos in range(start, end):
            ranks[ordered[pos][0]] = average_rank
        start = end
    return ranks


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) != len(b) or len(a) < 2:
        return None
    ra, rb = rankdata(a), rankdata(b)
    ma, mb = math.fsum(ra) / len(ra), math.fsum(rb) / len(rb)
    da, db = [x - ma for x in ra], [x - mb for x in rb]
    va, vb = math.fsum(x * x for x in da), math.fsum(x * x for x in db)
    if va == 0.0 or vb == 0.0:
        return None
    return math.fsum(x * y for x, y in zip(da, db)) / math.sqrt(va * vb)


def linear_quantile(values: Sequence[float], p: float) -> float:
    if not values or not 0.0 <= p <= 1.0:
        raise ValueError("quantile requires values and p in [0,1]")
    ordered = sorted(float(x) for x in values)
    pos = (len(ordered) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] * (1.0 - (pos - lo)) + ordered[hi] * (pos - lo)


def top_decile(ids: Sequence[str], scores: Sequence[float]) -> set[str]:
    if len(ids) != len(scores) or not ids:
        raise ValueError("top-decile ranking requires matching non-empty IDs and scores")
    k = max(1, math.ceil(0.10 * len(ids)))
    return {geoid for geoid, _ in sorted(zip(ids, scores), key=lambda pair: (-float(pair[1]), pair[0]))[:k]}


def vector_hash(ids: Sequence[str], values: Sequence[float]) -> str:
    if len(ids) != len(values):
        raise ValueError("vector ID/value length mismatch")
    payload = "".join(f"{g},{float(v):.17g}\n" for g, v in zip(ids, values))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def summarize_region(ids: Sequence[str], s2: Sequence[float], s2b: Sequence[float]) -> dict[str, Any]:
    if not (len(ids) == len(s2) == len(s2b)) or len(set(ids)) != len(ids):
        raise ValueError("regional input vector shape or ID uniqueness mismatch")
    deltas = [b - a for a, b in zip(s2, s2b)]
    absolute = [abs(x) for x in deltas]
    top_s2, top_s2b = top_decile(ids, s2), top_decile(ids, s2b)
    k = max(1, math.ceil(0.10 * len(ids)))
    overlap = len(top_s2 & top_s2b)
    return {
        "official_rows": len(ids),
        "spearman_s2_vs_s2b": spearman(s2, s2b),
        "top_decile_definition": "exact ceil(0.10*n) highest composite scores; descending score, then ascending GEOID for ties",
        "top_decile_size_each": k,
        "top_decile_overlap_count": overlap,
        "top_decile_overlap_fraction_of_k": overlap / k,
        "top_decile_jaccard": overlap / len(top_s2 | top_s2b),
        "median_absolute_score_delta": linear_quantile(absolute, 0.5),
        "p95_absolute_score_delta": linear_quantile(absolute, 0.95),
        "maximum_absolute_score_delta": max(absolute),
        "mean_signed_score_delta_s2b_minus_s2": math.fsum(deltas) / len(deltas),
        "frozen_S2_vector_sha256": vector_hash(ids, s2),
        "frozen_S2b_vector_sha256": vector_hash(ids, s2b),
        "absolute_delta_vector_sha256": vector_hash(ids, absolute),
        "all_scores_finite_bounded_0_1": all(math.isfinite(x) and 0 <= x <= 1 for x in (*s2, *s2b)),
    }


def run(workspace: Path) -> dict[str, Any]:
    ids_by_region, region_by_id, sample_hashes = read_official_ids(workspace)
    official_ids = set(region_by_id)
    s2_before = sha256_file(S2_PATH)
    s2b_before = sha256_file(S2B_PATH)
    if s2_before != S2_SHA256 or s2b_before != S2B_SHA256:
        raise ValueError("frozen S2 or S2b artifact hash differs from preregistered digest")
    s2, s2_rows = read_candidate_scores(S2_PATH, S2_SHA256, official_ids)
    s2b, s2b_rows = read_candidate_scores(S2B_PATH, S2B_SHA256, official_ids)

    per_region = {}
    score_rows = []
    all_abs_deltas = []
    for region, ids in ids_by_region.items():
        a = [s2[g] for g in ids]
        b = [s2b[g] for g in ids]
        summary = summarize_region(ids, a, b)
        if summary["official_rows"] != REGION_COUNTS[region]:
            raise ValueError(f"regional scope mismatch: {region}")
        per_region[region] = summary
        all_abs_deltas.extend(abs(y - x) for x, y in zip(a, b))
        rank_a = {g: i + 1 for i, g in enumerate(sorted(ids, key=lambda g: (-s2[g], g)))}
        rank_b = {g: i + 1 for i, g in enumerate(sorted(ids, key=lambda g: (-s2b[g], g)))}
        for geoid in ids:
            score_rows.append({"GEOID": geoid, "region": region,
                               "S2": format(s2[geoid], ".17g"), "S2b": format(s2b[geoid], ".17g"),
                               "S2b_minus_S2": format(s2b[geoid] - s2[geoid], ".17g"),
                               "S2_rank_desc_tie_geoid": rank_a[geoid],
                               "S2b_rank_desc_tie_geoid": rank_b[geoid]})
    if len(score_rows) != 9379 or len({r["GEOID"] for r in score_rows}) != 9379:
        raise ValueError("output does not cover each official GEOID exactly once")

    SCORES.parent.mkdir(parents=True, exist_ok=True)
    with SCORES.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(score_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(score_rows)
    score_artifact_sha = sha256_file(SCORES)
    s2_after = sha256_file(S2_PATH)
    s2b_after = sha256_file(S2B_PATH)
    if s2_after != s2_before or s2b_after != s2b_before:
        raise ValueError("frozen candidate bytes changed during the audit")

    report = {
        "schema_version": 1,
        "analysis_id": "S2_S2B_TRACT_RANK_SENSITIVITY_V1",
        "result": "PASS",
        "design": {
            "scope": "authoritative 9,379 official GEOIDs in the four scored regions only",
            "inputs": "read frozen S2 and S2b candidate outputs only, plus official GEOID cells; no component files or other candidates",
            "rank_correlation": "Spearman correlation using average ranks for tied scores",
            "top_decile_overlap": "exact ceil(0.10*n) scores; ties broken by ascending GEOID; overlap divided by k",
            "delta": "S2b composite score minus S2 composite score",
            "quantiles": "linear interpolation at (n-1)*p",
        },
        "measure_before": {
            "S2_sha256": s2_before, "S2b_sha256": s2b_before,
            "S2_candidate_rows": s2_rows, "S2b_candidate_rows": s2b_rows,
            "official_geoids": 9379, "unique_official_geoids": len(official_ids),
            "region_counts": {r: len(ids_by_region[r]) for r in REGIONS},
            "all_region_p95_absolute_score_delta": linear_quantile(all_abs_deltas, 0.95),
            "provided_sample_geoid_hashes": sample_hashes,
        },
        "per_region": per_region,
        "measure_after": {"S2_sha256": s2_after, "S2b_sha256": s2b_after,
                         "candidate_bytes_unchanged": s2_after == s2_before and s2b_after == s2b_before},
        "scope_validation": {"official_rows": len(score_rows), "unique_geoids": len({x["GEOID"] for x in score_rows}),
                             "missing_ids": 0, "extra_ids": 0, "duplicate_ids": 0,
                             "four_regions_reported": set(per_region) == set(REGIONS),
                             "sample_score_placeholders_used": False,
                             "out_of_scope_candidate_score_values_parsed": False,
                             "all_scores_finite_bounded_0_1": all(x["all_scores_finite_bounded_0_1"] for x in per_region.values())},
        "scores_artifact": {"path": str(SCORES.relative_to(ROOT)), "sha256": score_artifact_sha,
                            "rows": len(score_rows), "columns": list(score_rows[0])},
        "interpretation": {"sensitivity_only": True, "accuracy_claim": False,
                           "target_accuracy_identifiable": False, "candidate_selection": False,
                           "candidate_bytes_modified": False, "upload_performed": False},
        "forbidden_information_used": {"labels": False, "removed_or_equivalent_references": False,
                                        "external_data": False, "C0_or_leaderboard_scores": False,
                                        "uploads": 0},
        "reproduction": {"command": "uv run --with-requirements requirements.txt python scripts/audit_s2_s2b_tract_rank_sensitivity.py --workspace $COMPETITION_WORKSPACE",
                         "tests": "uv run --with-requirements requirements.txt --with pytest python -m pytest -q tests/test_s2_s2b_tract_rank_sensitivity.py"},
        "audit_script_sha256": sha256_file(Path(__file__).resolve()),
    }
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report = run(args.workspace.resolve())
    print(json.dumps({"result": report["result"], "official_rows": report["scope_validation"]["official_rows"],
                      "all_region_p95": report["measure_before"]["all_region_p95_absolute_score_delta"],
                      "report": str(REPORT), "scores_sha256": report["scores_artifact"]["sha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
