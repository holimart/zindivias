#!/usr/bin/env python3
"""Compute label-free per-tract disagreement across the frozen Track A candidate cohort."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/track-a-ensemble-disagreement-preregistered.json"
FINAL_TWO = ROOT / "reports/final-two.json"
EXPECTED_CANDIDATES = (
    "A1_physical_equal", "A2_spectral", "A3_dawid_skene", "V2_model"
)
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
CANDIDATE_COLUMNS = ("GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score")
SUMMARY_QUANTILES = (0.5, 0.9, 0.95, 0.99, 1.0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_geoid_column(path: Path, selected: str) -> list[str]:
    """Read only GEOID from official samples; never read placeholder score values."""
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if selected not in header:
            raise ValueError(f"{path}: missing {selected}")
        index = header.index(selected)
        return [row[index] for row in reader]


def load_official_ids(workspace: Path) -> tuple[list[str], dict[str, str]]:
    ids: list[str] = []
    region_of: dict[str, str] = {}
    for region in REGIONS:
        region_ids = read_geoid_column(workspace / "samples" / f"{region}-sample-submission.csv", "GEOID")
        if not region_ids or len(region_ids) != len(set(region_ids)):
            raise ValueError(f"{region}: official GEOIDs are empty or duplicated")
        for geoid in region_ids:
            if geoid in region_of:
                raise ValueError(f"duplicate official GEOID across regions: {geoid}")
            ids.append(geoid)
            region_of[geoid] = region
    if len(ids) != 9379 or set(region_of.values()) != set(REGIONS):
        raise ValueError(f"expected exactly 9,379 official scored GEOIDs, got {len(ids)}")
    return ids, region_of


def load_registrations(path: Path, expected_ids: tuple[str, ...] = EXPECTED_CANDIDATES) -> tuple[dict[str, dict[str, Any]], str]:
    """Use only frozen artifact metadata; public-score/leaderboard fields are ignored."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("candidate_registrations")
    if not isinstance(rows, list):
        raise ValueError("final-two manifest has no candidate_registrations list")
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = row.get("candidate_id")
        if cid not in expected_ids:
            continue
        if cid in selected:
            raise ValueError(f"duplicate registration for {cid}")
        selected[cid] = {key: row.get(key) for key in (
            "candidate_id", "artifact", "sha256", "rows", "columns",
            "preregistered_card_path", "preregistered_card_sha256",
            "official_id_order_verified_against_authoritative_samples", "template_only_rows"
        )}
    if set(selected) != set(expected_ids):
        raise ValueError(f"frozen cohort registration mismatch: {sorted(selected)}")
    return selected, sha256(path)


def load_candidate_scores(path: Path, expected_hash: str, official_ids: list[str], expected_count: int = 9379) -> tuple[dict[str, float], dict[str, Any]]:
    if sha256(path) != expected_hash:
        raise ValueError(f"candidate artifact SHA-256 mismatch: {path}")
    wanted = set(official_ids)
    values: dict[str, float] = {}
    all_ids: set[str] = set()
    template_only = 0
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if tuple(header) != CANDIDATE_COLUMNS:
            raise ValueError(f"{path}: unexpected candidate columns {header}")
        geoid_index = header.index("GEOID")
        score_index = header.index("coverage_gap_score")
        row_count = 0
        for row in reader:
            row_count += 1
            geoid = row[geoid_index]
            if geoid in all_ids:
                raise ValueError(f"{path}: duplicate GEOID {geoid}")
            all_ids.add(geoid)
            if geoid not in wanted:
                template_only += 1
                continue
            score = float(row[score_index])
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError(f"{path}: non-finite or unbounded coverage score for {geoid}")
            values[geoid] = score
    if set(values) != wanted or len(values) != expected_count:
        raise ValueError(f"{path}: official GEOID coverage mismatch ({len(values)}/{expected_count})")
    return values, {"rows_in_artifact": row_count, "official_rows_used": len(values),
                    "nonofficial_rows_excluded": template_only}


def per_tract_metrics(candidate_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(candidate_values, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 4:
        raise ValueError("expected n-by-4 candidate score matrix")
    if not np.isfinite(values).all() or np.any((values < 0) | (values > 1)):
        raise ValueError("candidate score matrix must be finite and bounded in [0,1]")
    center = np.median(values, axis=1)
    mad = np.median(np.abs(values - center[:, None]), axis=1)
    spread = np.quantile(values, 0.9, axis=1, method="linear") - np.quantile(values, 0.1, axis=1, method="linear")
    return mad, spread


def distribution(values: np.ndarray, quantiles: tuple[float, ...] = SUMMARY_QUANTILES) -> dict[str, Any]:
    data = np.asarray(values, dtype=np.float64)
    if data.ndim != 1 or len(data) == 0 or not np.isfinite(data).all():
        raise ValueError("distribution input must be a nonempty finite vector")
    return {
        "count": int(len(data)),
        "mean": float(data.mean()),
        "std_population": float(data.std(ddof=0)),
        "quantiles_linear": {f"p{int(q * 100):02d}": float(np.quantile(data, q, method="linear")) for q in quantiles},
    }


def build_report(workspace: Path, output: Path) -> dict[str, Any]:
    prereg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if prereg.get("created_before_summary") is not True or tuple(prereg.get("scope", {}).get("candidate_ids", ())) != EXPECTED_CANDIDATES:
        raise ValueError("missing or mismatched preregistered cohort/summary definition")
    threshold = float(prereg["high_spread_threshold"])
    if not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("invalid preregistered high-spread threshold")
    ids, region_of = load_official_ids(workspace)
    registrations, final_two_hash = load_registrations(FINAL_TWO)
    scores_by_candidate: dict[str, dict[str, float]] = {}
    candidate_meta: dict[str, Any] = {}
    for candidate in EXPECTED_CANDIDATES:
        registration = registrations[candidate]
        artifact_rel = registration["artifact"]
        if not isinstance(artifact_rel, str) or Path(artifact_rel).is_absolute() or ".." in Path(artifact_rel).parts:
            raise ValueError(f"unsafe artifact path in registration for {candidate}")
        if registration["columns"] != list(CANDIDATE_COLUMNS) or registration["rows"] != 9794:
            raise ValueError(f"unexpected registered artifact schema for {candidate}")
        path = ROOT / artifact_rel
        values, scope = load_candidate_scores(path, registration["sha256"], ids)
        scores_by_candidate[candidate] = values
        candidate_meta[candidate] = {
            "artifact": artifact_rel, "sha256": registration["sha256"],
            "registered_rows": registration["rows"], "columns": registration["columns"],
            "official_id_order_verified_in_manifest": registration["official_id_order_verified_against_authoritative_samples"],
            "template_only_rows_registered": registration["template_only_rows"], **scope,
        }

    matrix = np.column_stack([[scores_by_candidate[c][g] for g in ids] for c in EXPECTED_CANDIDATES])
    mad, spread = per_tract_metrics(matrix)
    high = spread >= threshold
    if len(mad) != 9379 or not np.isfinite(mad).all() or not np.isfinite(spread).all():
        raise ValueError("per-tract metric coverage/finiteness failed")
    per_tract = [{"GEOID": geoid, "region": region_of[geoid], "median_absolute_deviation": float(mad[i]),
                  "p90_minus_p10_spread": float(spread[i]), "high_spread": bool(high[i])}
                 for i, geoid in enumerate(ids)]
    by_region: dict[str, Any] = {}
    for region in REGIONS:
        mask = np.asarray([region_of[g] == region for g in ids], dtype=bool)
        region_data: dict[str, Any] = {
            "official_tracts": int(mask.sum()),
            "disagreement_mad": distribution(mad[mask]),
            "disagreement_p90_minus_p10": distribution(spread[mask]),
            "high_spread_definition": f"per-tract p90_minus_p10_spread >= {threshold:.2f}",
            "high_spread_count": int(np.sum(high & mask)),
            "high_spread_fraction": float(np.mean(high[mask])),
            "high_spread_geoids": [ids[i] for i in np.flatnonzero(high & mask)],
            "candidate_score_distributions": {},
        }
        for j, candidate in enumerate(EXPECTED_CANDIDATES):
            region_data["candidate_score_distributions"][candidate] = distribution(matrix[mask, j])
        by_region[region] = region_data
    if sum(x["official_tracts"] for x in by_region.values()) != 9379:
        raise ValueError("region summary does not cover exact official scope")

    report = {
        "schema_version": 1,
        "audit": "Frozen Track A ensemble disagreement; diagnostic-only, no accuracy or selection claim.",
        "preregistration": {"path": str(CONFIG.relative_to(ROOT)), "sha256": sha256(CONFIG),
                            "high_spread_definition": prereg["metrics"]["high_spread_definition"],
                            "high_spread_threshold": threshold, "created_before_summary": True},
        "scope": {"candidate_ids_in_fixed_order": list(EXPECTED_CANDIDATES), "official_scored_ids": 9379,
                  "regions": list(REGIONS), "template_only_ids_in_scored_summary": 0,
                  "input_sample_values_read": False, "leaderboard_feedback_used": False,
                  "labels_or_removed_reference_layers_used": False, "candidate_modified_or_uploaded": False},
        "metric_definitions": {
            "median_absolute_deviation": "For each tract, median(abs(candidate score - median of the four candidate scores)); unscaled.",
            "p90_minus_p10_spread": "For each tract, linear p90 minus linear p10 across the four frozen coverage_gap_score outputs.",
            "quantile_method": "NumPy linear interpolation.",
            "high_spread_flag": f"p90_minus_p10_spread >= {threshold:.2f}; threshold fixed in the preregistration before this summary.",
        },
        "candidate_artifacts": candidate_meta,
        "alignment": {"official_geoid_count": len(ids), "each_official_geoid_once": True,
                      "candidate_scores_per_geoid": len(EXPECTED_CANDIDATES),
                      "per_tract_rows": len(per_tract), "per_tract_order": "official regional sample order; regions concatenated as listed"},
        "per_region": by_region,
        "per_tract": per_tract,
        "acceptance": {"all_9379_scored_geoids_once": True, "all_four_candidate_hashes_verified": True,
                       "all_metrics_finite_and_bounded": bool(np.all((mad >= 0) & (mad <= .5) & (spread >= 0) & (spread <= 1))),
                       "region_quantiles_reported": True, "high_spread_preregistered": True,
                       "candidate_artifacts_unchanged": True, "diagnostic_only": True},
        "provenance": {"final_two_manifest": {"path": str(FINAL_TWO.relative_to(ROOT)), "sha256": final_two_hash},
                       "official_id_source": "workspace samples/<region>-sample-submission.csv; GEOID column only",
                       "analysis_script_sha256": sha256(Path(__file__).resolve())},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ.get("COMPETITION_WORKSPACE", "")))
    parser.add_argument("--out", type=Path, default=ROOT / "reports/track-a-ensemble-disagreement.json")
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error("--workspace or COMPETITION_WORKSPACE must point to the authorized workspace")
    report = build_report(args.workspace, args.out)
    print(json.dumps({"acceptance": report["acceptance"], "official_scored_ids": report["alignment"]["official_geoid_count"],
                      "high_spread_threshold": report["preregistration"]["high_spread_threshold"],
                      "regions": {region: {"count": summary["official_tracts"], "mad_p95": summary["disagreement_mad"]["quantiles_linear"]["p95"],
                                            "spread_p95": summary["disagreement_p90_minus_p10"]["quantiles_linear"]["p95"],
                                            "high_spread_count": summary["high_spread_count"]}
                                  for region, summary in report["per_region"].items()}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
