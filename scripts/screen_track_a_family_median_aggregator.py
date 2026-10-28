#!/usr/bin/env python3
"""Screen a median of four frozen Track A composites, with leave-one-candidate-out sensitivity."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REPORT_REL = "reports/track-a-ensemble-disagreement.json"
SOURCE_REPORT_SHA256 = "87298385d476294208619fb5b920909d712d1494f73aec96681425d98e6080f5"
OUTPUT_CANDIDATE_REL = "reports/track-a-family-median-nonsubmission.csv"
OUTPUT_REPORT_REL = "reports/track-a-family-median-aggregator.json"
CANDIDATES = ("A1_physical_equal", "A2_spectral", "A3_dawid_skene", "V2_model")
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591, "eastern-ok": 1192, "south-central-tx": 6003}
OFFICIAL_ID_COUNT = 9379
SAMPLE_FILES = {region: f"samples/{region}-sample-submission.csv" for region in REGIONS}
STABILITY_THRESHOLD = 0.05


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def linear_quantile(values: Sequence[float], probability: float) -> float:
    """Linear interpolation on (n-1)*p, matching the frozen reports' convention."""
    if not values:
        raise ValueError("quantile requires at least one value")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    ordered = sorted(float(value) for value in values)
    if not all(math.isfinite(value) for value in ordered):
        raise ValueError("quantile inputs must be finite")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(float(value) for value in values)
    if not all(math.isfinite(value) for value in ordered):
        raise ValueError("median inputs must be finite")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def read_official_ids(workspace: Path) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Read only the first CSV field (GEOID) from the authoritative regional samples."""
    ordered_ids: list[str] = []
    region_by_id: dict[str, str] = {}
    projection_hashes: dict[str, str] = {}
    for region in REGIONS:
        path = workspace / SAMPLE_FILES[region]
        if not path.is_file():
            raise FileNotFoundError(f"authoritative GEOID sample missing: {path}")
        ids = []
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            header = stream.readline()
            if header.partition(",")[0].strip() != "GEOID":
                raise ValueError(f"{path}: first header field must be GEOID")
            for line_number, line in enumerate(stream, start=2):
                if not line.strip():
                    continue
                geoid = line.partition(",")[0].strip()
                if len(geoid) != 11 or not geoid.isdigit():
                    raise ValueError(f"{path}:{line_number}: invalid GEOID {geoid!r}")
                ids.append(geoid)
        if len(ids) != REGION_COUNTS[region] or len(set(ids)) != len(ids):
            raise ValueError(f"{region}: official GEOID count/uniqueness mismatch")
        for geoid in ids:
            if geoid in region_by_id:
                raise ValueError(f"duplicate official GEOID across regions: {geoid}")
            region_by_id[geoid] = region
        payload = "\n".join(ids) + "\n"
        projection_hashes[region] = hashlib.sha256(payload.encode("ascii")).hexdigest()
        ordered_ids.extend(ids)
    if len(ordered_ids) != OFFICIAL_ID_COUNT or len(region_by_id) != OFFICIAL_ID_COUNT:
        raise ValueError("official sample projection does not cover exactly 9,379 unique GEOIDs")
    return ordered_ids, region_by_id, projection_hashes


def read_candidate_composites(path: Path, expected_sha256: str, registered_rows: int,
                              official_ids: set[str]) -> tuple[dict[str, float], dict[str, Any]]:
    """Read GEOID and coverage_gap_score only; ignore component fields and nonofficial IDs."""
    if sha256_file(path) != expected_sha256:
        raise ValueError(f"hash mismatch for pinned candidate CSV {path}")
    composites: dict[str, float] = {}
    all_ids: set[str] = set()
    row_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        header_line = stream.readline().rstrip("\r\n")
        header = header_line.split(",")
        if len(header) != len(set(header)) or "GEOID" not in header or "coverage_gap_score" not in header:
            raise ValueError(f"{path}: missing or duplicate GEOID/coverage_gap_score header")
        geoid_index = header.index("GEOID")
        score_index = header.index("coverage_gap_score")
        for line_number, line in enumerate(stream, start=2):
            if not line.strip():
                continue
            fields = line.rstrip("\r\n").split(",")
            if max(geoid_index, score_index) >= len(fields):
                raise ValueError(f"{path}:{line_number}: short CSV row")
            geoid = fields[geoid_index].strip()
            if len(geoid) != 11 or not geoid.isdigit() or geoid in all_ids:
                raise ValueError(f"{path}:{line_number}: invalid or duplicate GEOID {geoid!r}")
            all_ids.add(geoid)
            row_count += 1
            if geoid not in official_ids:
                continue
            try:
                score = float(fields[score_index])
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: invalid composite for official GEOID {geoid}") from exc
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError(f"{path}:{line_number}: official composite outside finite [0,1]")
            composites[geoid] = score
    if row_count != registered_rows:
        raise ValueError(f"{path}: {row_count} rows differs from registered count {registered_rows}")
    if set(composites) != official_ids or len(composites) != len(official_ids):
        raise ValueError(f"{path}: official candidate coverage is incomplete or contains extras")
    return composites, {"path": str(path.relative_to(ROOT)), "sha256": expected_sha256,
                        "registered_rows": row_count, "official_scores_read": len(composites),
                        "official_id_set_sha256": hashlib.sha256("\n".join(sorted(composites)).encode("ascii")).hexdigest()}


def aggregate_median(ordered_ids: Sequence[str], region_by_id: Mapping[str, str],
                     candidate_scores: Mapping[str, Mapping[str, float]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if tuple(candidate_scores) != CANDIDATES:
        raise ValueError("candidate mapping order differs from the frozen four-candidate order")
    expected = set(ordered_ids)
    for candidate in CANDIDATES:
        if set(candidate_scores[candidate]) != expected:
            raise ValueError(f"{candidate}: candidate score ID universe differs from official IDs")
    output_rows = []
    absolute_deltas: dict[str, list[float]] = {region: [] for region in REGIONS}
    candidate_comparison_deltas: dict[str, dict[str, list[float]]] = {
        region: {candidate: [] for candidate in CANDIDATES} for region in REGIONS
    }
    omission_metrics: dict[str, dict[str, dict[str, Any]]] = {}
    full_scores: dict[str, float] = {}
    for geoid in ordered_ids:
        scores = [candidate_scores[candidate][geoid] for candidate in CANDIDATES]
        full_scores[geoid] = median(scores)
        output_rows.append({"GEOID": geoid, "coverage_gap_score": full_scores[geoid]})
        region = region_by_id[geoid]
        for candidate in CANDIDATES:
            candidate_comparison_deltas[region][candidate].append(abs(full_scores[geoid] - candidate_scores[candidate][geoid]))
        for omitted in CANDIDATES:
            remaining = [candidate_scores[candidate][geoid] for candidate in CANDIDATES if candidate != omitted]
            delta = abs(median(remaining) - full_scores[geoid])
            absolute_deltas[region].append(delta)
            omission_metrics.setdefault(region, {}).setdefault(omitted, {"absolute_deltas": [], "changed_tracts": 0})
            metrics = omission_metrics[region][omitted]
            metrics["absolute_deltas"].append(delta)
            metrics["changed_tracts"] += int(delta != 0.0)

    region_summary = {}
    for region in REGIONS:
        all_deltas = absolute_deltas[region]
        per_candidate = {}
        for candidate in CANDIDATES:
            values = omission_metrics[region][candidate]["absolute_deltas"]
            per_candidate[candidate] = {
                "p95_absolute_delta": linear_quantile(values, 0.95),
                "maximum_absolute_delta": max(values),
                "changed_tracts": omission_metrics[region][candidate]["changed_tracts"],
            }
        median_vs_candidate = {
            candidate: {
                "median_absolute_difference": linear_quantile(candidate_comparison_deltas[region][candidate], 0.5),
                "p95_absolute_difference": linear_quantile(candidate_comparison_deltas[region][candidate], 0.95),
                "maximum_absolute_difference": max(candidate_comparison_deltas[region][candidate]),
                "changed_tracts": sum(value != 0.0 for value in candidate_comparison_deltas[region][candidate]),
            }
            for candidate in CANDIDATES
        }
        region_summary[region] = {
            "official_ids": sum(region_by_id[g] == region for g in ordered_ids),
            "median_vs_frozen_candidate_composites": median_vs_candidate,
            "all_four_omissions_p95_absolute_delta": linear_quantile(all_deltas, 0.95),
            "all_four_omissions_maximum_absolute_delta": max(all_deltas),
            "maximum_absolute_delta_by_omitted_candidate": {
                candidate: per_candidate[candidate]["maximum_absolute_delta"] for candidate in CANDIDATES
            },
            "p95_absolute_delta_by_omitted_candidate": {
                candidate: per_candidate[candidate]["p95_absolute_delta"] for candidate in CANDIDATES
            },
            "per_omitted_candidate": per_candidate,
        }
    return output_rows, {
        "region_summary": region_summary,
        "overall_maximum_absolute_delta": max(max(values) for values in absolute_deltas.values()),
        "overall_p95_absolute_delta": linear_quantile([x for values in absolute_deltas.values() for x in values], 0.95),
        "overall_changed_tract_omission_pairs": sum(x != 0.0 for values in absolute_deltas.values() for x in values),
    }


def build_screen(workspace: Path, output_candidate_path: Path,
                 source_report_path: Path = ROOT / SOURCE_REPORT_REL) -> dict[str, Any]:
    source_sha = sha256_file(source_report_path)
    if source_sha != SOURCE_REPORT_SHA256:
        raise ValueError(f"frozen Track A disagreement report hash mismatch: {source_sha}")
    baseline = json.loads(source_report_path.read_text(encoding="utf-8"))
    if baseline["scope"]["candidate_ids_in_fixed_order"] != list(CANDIDATES):
        raise ValueError("baseline report candidate order does not match the four frozen candidates")
    if baseline["scope"]["official_scored_ids"] != 9379 or baseline["acceptance"]["all_four_candidate_hashes_verified"] is not True:
        raise ValueError("baseline report does not certify the four candidate hashes and official scope")

    ordered_ids, region_by_id, projection_hashes = read_official_ids(workspace)
    official_set = set(ordered_ids)
    candidate_scores: dict[str, dict[str, float]] = {}
    candidate_provenance = {}
    for candidate in CANDIDATES:
        registered = baseline["candidate_artifacts"][candidate]
        candidate_path = ROOT / registered["artifact"]
        if registered["columns"][-1] != "coverage_gap_score":
            raise ValueError(f"{candidate}: unexpected registered composite column")
        scores, meta = read_candidate_composites(
            candidate_path, registered["sha256"], registered["registered_rows"], official_set)
        if len(scores) != registered["official_rows_used"]:
            raise ValueError(f"{candidate}: official score count differs from frozen registration")
        candidate_scores[candidate] = scores
        candidate_provenance[candidate] = meta

    output_rows, stability = aggregate_median(ordered_ids, region_by_id, candidate_scores)
    output_candidate_path.parent.mkdir(parents=True, exist_ok=True)
    with output_candidate_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["GEOID", "coverage_gap_score"])
        for row in output_rows:
            writer.writerow([row["GEOID"], format(row["coverage_gap_score"], ".17g")])
    output_hash = sha256_file(output_candidate_path)
    written_ids = []
    with output_candidate_path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        if next(reader, None) != ["GEOID", "coverage_gap_score"]:
            raise ValueError("non-submission median candidate header failed round-trip validation")
        for row in reader:
            if len(row) != 2:
                raise ValueError("non-submission median candidate contains a malformed row")
            geoid, score_text = row
            score = float(score_text)
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError(f"non-submission median candidate has invalid score for {geoid}")
            written_ids.append(geoid)
    if written_ids != ordered_ids:
        raise ValueError("written median candidate does not round-trip exact authoritative GEOID order")

    before_by_region = {}
    for region in REGIONS:
        prior = baseline["per_region"][region]
        before_by_region[region] = {
            "official_tracts": prior["official_tracts"],
            "four_candidate_mad_median": prior["disagreement_mad"]["quantiles_linear"]["p50"],
            "four_candidate_p90_p10_spread_median": prior["disagreement_p90_minus_p10"]["quantiles_linear"]["p50"],
            "tracts_at_or_above_preregistered_0_10_spread": prior["high_spread_count"],
            "high_spread_threshold": baseline["preregistration"]["high_spread_threshold"],
        }
    complete_coverage = (len(output_rows) == 9379
                         and [row["GEOID"] for row in output_rows] == ordered_ids
                         and set(region_by_id.values()) == set(REGIONS))
    bounded = all(math.isfinite(row["coverage_gap_score"]) and 0.0 <= row["coverage_gap_score"] <= 1.0
                  for row in output_rows)
    max_delta = stability["overall_maximum_absolute_delta"]
    stability_pass = max_delta <= STABILITY_THRESHOLD
    return {
        "schema_version": 1,
        "analysis_id": "TRACK_A_FAMILY_MEDIAN_AGGREGATOR_SCREEN_V1",
        "result": "PASS" if complete_coverage and bounded else "FAIL",
        "intent": "Label-free robustness sensitivity of a tract-wise median of four frozen Track A candidate composites; not a candidate evaluation against truth.",
        "method": {
            "candidate_order": list(CANDIDATES),
            "full_aggregator": "median of four finite [0,1] coverage_gap_score values; with four values this is the arithmetic mean of the two middle sorted values",
            "leave_one_candidate_out": "for each candidate omission, take the median of the remaining three candidate composites and compute its absolute difference from the full four-candidate median",
            "regional_quantile": "linear interpolation at position (n-1)*0.95; pooled over all four omission deltas for each official tract in that region",
            "stability_threshold_max_abs_change_inclusive": STABILITY_THRESHOLD,
            "stability_interpretation": "A pass is aggregation sensitivity only, not evidence of target accuracy or candidate promotion eligibility.",
        },
        "source_report": {"path": SOURCE_REPORT_REL, "sha256": source_sha, "expected_sha256": SOURCE_REPORT_SHA256},
        "measure_before": {
            "source_report_scope": {"official_scored_ids": baseline["scope"]["official_scored_ids"],
                                     "candidate_ids_in_fixed_order": baseline["scope"]["candidate_ids_in_fixed_order"]},
            "source_candidate_artifacts": candidate_provenance,
            "four_candidate_disagreement_by_region": before_by_region,
            "source_report_sha256": source_sha,
            "source_scope_diagnostic_only": baseline["audit"],
        },
        "measure_after": {
            "official_id_count": len(ordered_ids),
            "official_id_order_sha256_by_region": projection_hashes,
            "official_id_order_sha256": hashlib.sha256("\n".join(ordered_ids).encode("ascii")).hexdigest(),
            "candidate_artifact": {"path": str(output_candidate_path.relative_to(ROOT)),
                                   "sha256": output_hash, "rows": len(output_rows),
                                   "columns": ["GEOID", "coverage_gap_score"],
                                   "registered_for_submission": False},
            "region_stability": stability["region_summary"],
            "overall_maximum_absolute_delta": stability["overall_maximum_absolute_delta"],
            "overall_p95_absolute_delta": stability["overall_p95_absolute_delta"],
            "overall_changed_tract_omission_pairs": stability["overall_changed_tract_omission_pairs"],
        },
        "acceptance": {
            "all_9379_authoritative_geoids_once_in_exact_order": complete_coverage,
            "all_candidate_hashes_match_frozen_registered_report": True,
            "all_official_candidate_composites_finite_and_bounded": bounded,
            "all_median_output_values_finite_and_bounded": bounded,
            "leave_one_candidate_out_stability_pass": stability_pass,
            "stability_screen_label": "stable_with_respect_to_this_aggregation_screen" if stability_pass else "unstable_retain_current_candidates",
            "screen_execution_passed": complete_coverage and bounded,
        },
        "safety": {
            "candidate_sources_modified": False,
            "existing_candidate_registry_modified": False,
            "new_candidate_registered": False,
            "new_candidate_is_submission": False,
            "sample_values_or_placeholders_read": False,
            "labels_or_removed_or_equivalent_references_read": False,
            "leaderboard_or_public_scores_used": False,
            "external_scored_features_used": False,
            "uploads": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ.get("COMPETITION_WORKSPACE", ".")))
    parser.add_argument("--source-report", type=Path, default=ROOT / SOURCE_REPORT_REL)
    parser.add_argument("--candidate-out", type=Path, default=ROOT / OUTPUT_CANDIDATE_REL)
    parser.add_argument("--report-out", type=Path, default=ROOT / OUTPUT_REPORT_REL)
    args = parser.parse_args()
    report = build_screen(args.workspace, args.candidate_out, args.source_report)
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"],
                      "stability": report["acceptance"]["stability_screen_label"],
                      "max_abs_delta": report["measure_after"]["overall_maximum_absolute_delta"],
                      "output_candidate_sha256": report["measure_after"]["candidate_artifact"]["sha256"],
                      "report": str(args.report_out)}, sort_keys=True))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
