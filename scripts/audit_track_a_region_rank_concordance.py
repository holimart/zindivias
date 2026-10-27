#!/usr/bin/env python3
"""Label-free pairwise rank concordance for the frozen Track A candidates."""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
FINAL_TWO = ROOT / "reports/final-two.json"
WORKSPACE_DEFAULT = os.environ.get("COMPETITION_WORKSPACE", "")
CANDIDATES = ("A1_physical_equal", "A2_spectral", "A3_dawid_skene", "V2_model")
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
SCORE_FIELD = "coverage_gap_score"
EXPECTED_HEADER = ("GEOID", "transport_gap", "building_gap", "poi_gap", SCORE_FIELD)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def registered_metadata(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    """Use the existing lexical projector to skip leaderboard fields in final-two."""
    projector_path = ROOT / "scripts/audit_track_a_feature_support.py"
    spec = importlib.util.spec_from_file_location("safe_final_two_projector", projector_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load safe final-two registration projector")
    projector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(projector)
    return projector.load_final_two_registrations(path, expected_ids=CANDIDATES)


def read_official_ids(workspace: Path) -> tuple[dict[str, str], dict[str, list[str]], str]:
    region_by_id: dict[str, str] = {}
    ids_by_region: dict[str, list[str]] = {}
    for region in REGIONS:
        sample = workspace / "samples" / f"{region}-sample-submission.csv"
        with sample.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.reader(stream)
            header = next(reader, [])
            if "GEOID" not in header:
                raise ValueError(f"{sample}: missing GEOID column")
            index = header.index("GEOID")
            ids = [row[index] for row in reader]
        if not ids or any(not geoid for geoid in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"{region}: empty or duplicated official GEOID")
        ids_by_region[region] = ids
        for geoid in ids:
            if geoid in region_by_id:
                raise ValueError(f"GEOID appears in more than one official region: {geoid}")
            region_by_id[geoid] = region
    if len(region_by_id) != 9379:
        raise ValueError(f"expected 9,379 official scored GEOIDs, found {len(region_by_id)}")
    sequence = [geoid for region in REGIONS for geoid in ids_by_region[region]]
    ordered_hash = hashlib.sha256("\n".join(sequence).encode("utf-8")).hexdigest()
    return region_by_id, ids_by_region, ordered_hash


def tie_summary(values: Iterable[float]) -> dict[str, Any]:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        raise ValueError("cannot summarize ties for an empty vector")
    tied_observations = 0
    tied_pairs = 0
    tie_groups = 0
    start = 0
    while start < n:
        end = start + 1
        while end < n and ordered[end] == ordered[start]:
            end += 1
        size = end - start
        if size > 1:
            tie_groups += 1
            tied_observations += size
            tied_pairs += size * (size - 1) // 2
        start = end
    all_pairs = n * (n - 1) // 2
    return {"n": n, "tie_groups": tie_groups, "tied_observations": tied_observations,
            "tied_observation_fraction": tied_observations / n,
            "tied_observation_pairs": tied_pairs,
            "all_observation_pairs": all_pairs,
            "tied_pair_fraction": tied_pairs / all_pairs if all_pairs else 0.0}


def average_ranks(values: list[float]) -> list[float]:
    """Return 1-based average ranks; exact equal numeric values share midranks."""
    if not values:
        raise ValueError("cannot rank an empty vector")
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        value = values[order[start]]
        while end < len(order) and values[order[end]] == value:
            end += 1
        # Occupied 1-based ranks are start+1 through end; assign their average.
        rank = (start + 1 + end) / 2.0
        for offset in range(start, end):
            ranks[order[offset]] = rank
        start = end
    return ranks


def spearman_rho(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or not left:
        raise ValueError("Spearman inputs must be nonempty and equal length")
    x = average_ranks(left)
    y = average_ranks(right)
    n = len(x)
    mean_x = math.fsum(x) / n
    mean_y = math.fsum(y) / n
    dx = [value - mean_x for value in x]
    dy = [value - mean_y for value in y]
    xx = math.fsum(value * value for value in dx)
    yy = math.fsum(value * value for value in dy)
    if xx == 0 or yy == 0:
        return None
    covariance = math.fsum(a * b for a, b in zip(dx, dy))
    rho = covariance / math.sqrt(xx * yy)
    return max(-1.0, min(1.0, rho))


def read_candidate(path: Path, registration: dict[str, Any],
                   region_by_id: dict[str, str], ids_by_region: dict[str, list[str]]) -> tuple[dict[str, float], dict[str, Any]]:
    if registration.get("artifact") != str(path.relative_to(ROOT)):
        raise ValueError(f"registered path mismatch for {path}")
    if tuple(registration.get("columns", ())) != EXPECTED_HEADER:
        raise ValueError(f"registered columns mismatch for {path}")
    artifact_hash = sha256(path)
    if artifact_hash != registration.get("sha256"):
        raise ValueError(f"registered candidate hash mismatch: {path}")
    values: dict[str, float] = {}
    all_seen: set[str] = set()
    order_by_region = {region: [] for region in ids_by_region}
    row_count = 0
    non_scored_rows = 0
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != EXPECTED_HEADER:
            raise ValueError(f"{path}: candidate header/column order mismatch")
        for row in reader:
            row_count += 1
            geoid = row["GEOID"]
            if not geoid or geoid in all_seen:
                raise ValueError(f"{path}: empty or duplicate artifact GEOID {geoid!r}")
            all_seen.add(geoid)
            if geoid not in region_by_id:
                # Ignore all score strings on template-only IDs.
                non_scored_rows += 1
                continue
            raw = row[SCORE_FIELD]
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}/{geoid}: coverage score is not numeric") from exc
            if not math.isfinite(value):
                raise ValueError(f"{path}/{geoid}: non-finite official score")
            values[geoid] = value
            order_by_region[region_by_id[geoid]].append(geoid)
    if row_count != registration.get("rows"):
        raise ValueError(f"{path}: row count differs from final-two registration")
    missing = set(region_by_id) - set(values)
    if missing:
        raise ValueError(f"{path}: {len(missing)} official scored IDs missing")
    order_match = all(order_by_region[region] == ids_by_region[region] for region in ids_by_region)
    if not order_match:
        raise ValueError(f"{path}: official regional GEOID order mismatch")
    if registration.get("rows") - len(region_by_id) != non_scored_rows:
        raise ValueError(f"{path}: unexpected number of non-scored template rows")
    return values, {"artifact": registration["artifact"], "sha256": artifact_hash,
                    "registered_rows": row_count, "official_scored_rows": len(values),
                    "non_scored_rows_excluded": non_scored_rows,
                    "missing_official_rows": 0, "duplicate_ids": 0,
                    "official_order_matches_by_region": order_match,
                    "columns": list(EXPECTED_HEADER)}


def pair_record(left_name: str, right_name: str, ids: list[str],
                values: dict[str, dict[str, float]]) -> dict[str, Any]:
    left = [values[left_name][geoid] for geoid in ids]
    right = [values[right_name][geoid] for geoid in ids]
    return {"left_candidate": left_name, "right_candidate": right_name,
            "n_common_tracts": len(ids), "spearman_rho": spearman_rho(left, right),
            "tie_handling": "average 1-based ranks for exact numeric ties",
            "left_tie_rates": tie_summary(left), "right_tie_rates": tie_summary(right)}


def run(workspace: Path, output: Path) -> dict[str, Any]:
    region_by_id, ids_by_region, ordered_ids_sha = read_official_ids(workspace)
    registrations, final_two_sha = registered_metadata(FINAL_TWO)
    values: dict[str, dict[str, float]] = {}
    artifacts: dict[str, Any] = {}
    manifests: dict[str, Any] = {}
    for candidate in CANDIDATES:
        registration = registrations[candidate]
        artifact_path = ROOT / registration["artifact"]
        values[candidate], artifacts[candidate] = read_candidate(artifact_path, registration, region_by_id, ids_by_region)
        if candidate != "V2_model":
            manifest_path = ROOT / "reports/candidates" / f"{candidate}_manifest.json"
            manifest_hash = sha256(manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_hash != registration.get("parameter_provenance", {}).get("manifest_sha256"):
                raise ValueError(f"{candidate}: manifest hash mismatch against final-two registration")
            if (manifest.get("artifact") != registration["artifact"] or
                    manifest.get("sha256") != registration["sha256"] or
                    manifest.get("rows") != registration["rows"] or
                    manifest.get("official_rows") != len(region_by_id) or
                    manifest.get("template_only_rows") != 415):
                raise ValueError(f"{candidate}: candidate manifest contract mismatch")
            manifests[candidate] = {"path": str(manifest_path.relative_to(ROOT)), "sha256": manifest_hash,
                                    "official_rows": manifest["official_rows"],
                                    "template_only_rows": manifest["template_only_rows"]}
        if sha256(artifact_path) != registration["sha256"]:
            raise ValueError(f"{candidate}: candidate changed while audit ran")

    all_ids = [geoid for region in REGIONS for geoid in ids_by_region[region]]
    scopes = {"overall": all_ids, **ids_by_region}
    pairwise: dict[str, Any] = {}
    candidate_ties: dict[str, Any] = {}
    for scope, ids in scopes.items():
        candidate_ties[scope] = {candidate: tie_summary([values[candidate][geoid] for geoid in ids])
                                 for candidate in CANDIDATES}
        pairwise[scope] = {}
        for left, right in combinations(CANDIDATES, 2):
            key = f"{left}__{right}"
            pairwise[scope][key] = pair_record(left, right, ids, values)

    report = {
        "schema_version": 1,
        "audit": "Pairwise Spearman rank concordance of frozen Track A prediction outputs; label-free and diagnostic only.",
        "scope": {"candidate_ids": list(CANDIDATES), "score_column": SCORE_FIELD,
                  "official_scored_geoids": len(region_by_id), "regions": list(REGIONS),
                  "template_only_rows_in_rank_summaries": False,
                  "target_labels_read": False, "leaderboard_values_used": False,
                  "candidate_weights_or_outputs_changed": False, "candidate_generation_or_upload": False},
        "measure_before": {"four_candidates_have_frozen_registered_artifacts": True,
                           "region_stratified_rank_agreement_report_recorded": False,
                           "target_labels_available": False},
        "method": {"rank_correlation": "Pearson correlation of average ranks; ranks are 1-based and exact numerical ties receive the arithmetic mean of occupied ranks",
                   "tie_definition": "exact equality of parsed finite coverage_gap_score values",
                   "tie_rates": ["tied observations / n", "tied observation pairs / all unordered observation pairs"],
                   "common_tract_rule": "exact intersection of the four official scored GEOID masks; all four candidate values must be finite for every official GEOID",
                   "overall_scope": "concatenation of official IDs from the four fixed regions only"},
        "inputs": {"final_two_metadata_sha256": final_two_sha,
                   "candidate_artifacts": artifacts, "candidate_manifests": manifests,
                   "official_ordered_geoids_sha256": ordered_ids_sha,
                   "official_id_source": "$COMPETITION_WORKSPACE/samples/<region>-sample-submission.csv; GEOID column only"},
        "alignment": {"official_scored_geoids": len(region_by_id),
                      "common_tracts_overall": len(all_ids),
                      "common_tracts_by_region": {region: len(ids_by_region[region]) for region in REGIONS},
                      "official_id_set_exact_for_every_candidate": True,
                      "official_regional_order_exact_for_every_candidate": True,
                      "missing_or_duplicate_ids_by_candidate": {candidate: 0 for candidate in CANDIDATES},
                      "non_scored_rows_excluded_by_candidate": {candidate: artifacts[candidate]["non_scored_rows_excluded"] for candidate in CANDIDATES}},
        "candidate_tie_rates": candidate_ties,
        "pairwise_spearman_by_scope": pairwise,
        "acceptance": {"every_candidate_pair_and_region_reported": all(len(pairwise[r]) == 6 for r in REGIONS),
                       "overall_and_region_common_tract_counts_exact": all(
                           rec["n_common_tracts"] == len(scopes[scope])
                           for scope, records in pairwise.items() for rec in records.values()),
                       "official_9379_id_alignment_verified": len(all_ids) == 9379,
                       "deterministic_tie_handling_declared": True,
                       "candidate_artifact_hashes_verified": True,
                       "deterministic_rerun_supported": True,
                       "no_accuracy_or_candidate_selection_claim": True},
        "limitations": ["High rank agreement describes similarity among submitted estimates; it does not establish target accuracy or validate candidate selection.",
                        "Exact ties are computed on serialized numeric coverage_gap_score values using average ranks.",
                        "The 415 template-only rows are excluded from all rank calculations."],
    }
    report["provenance"] = {"analysis_script_sha256": sha256(Path(__file__).resolve()),
                             "safe_final_two_projector_sha256": sha256(ROOT / "scripts/audit_track_a_feature_support.py")}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(WORKSPACE_DEFAULT))
    parser.add_argument("--out", type=Path, default=ROOT / "reports/track-a-region-rank-concordance.json")
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error("--workspace or COMPETITION_WORKSPACE must be an existing directory")
    report = run(args.workspace, args.out)
    overall = report["pairwise_spearman_by_scope"]["overall"]
    print(json.dumps({"acceptance": report["acceptance"],
                      "overall_rho": {pair: metrics["spearman_rho"] for pair, metrics in overall.items()},
                      "region_counts": report["alignment"]["common_tracts_by_region"],
                      "output": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
