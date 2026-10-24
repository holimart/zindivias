#!/usr/bin/env python3
"""Build unregistered offline estimator-v1 sensitivities from provided inputs.

These outputs are for later review/pre-registration only, not phase-2 LB probes.
No target labels, leaderboard values, removed references, external data, or
sample score placeholders are used. Eastern-WA receives pooled four-region
fills because no allowed feature inputs for it are available.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
SCORES = ("transport_gap", "building_gap", "poi_gap", "coverage_gap_score")
FLAGS = ("transport_defined", "building_defined", "poi_defined")
COLUMNS = ("GEOID", *SCORES)

sys.path.insert(0, str(ROOT / "scripts"))
from estimate_gaps import estimate_one  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_geoid_only(path: Path) -> list[str]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        idx = header.index("GEOID")
        return [row[idx] for row in reader]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def write_candidate(path: Path, official_rows: list[dict], full_order: list[str]) -> dict:
    by_id = {row["GEOID"]: row for row in official_rows}
    if len(by_id) != len(official_rows):
        raise ValueError(f"duplicate official candidate IDs for {path}")
    extras = [geoid for geoid in full_order if geoid not in by_id]
    if len(by_id) != 9379 or len(extras) != 415:
        raise ValueError(f"expected 9,379 official and 415 extra rows, got {len(by_id)} and {len(extras)}")
    pooled = {column: sum(float(row[column]) for row in official_rows) / len(official_rows)
              for column in SCORES}
    rows = []
    for geoid in full_order:
        row = by_id.get(geoid)
        if row is None:
            row = {"GEOID": geoid, **{column: pooled[column] for column in SCORES}}
        rows.append({"GEOID": geoid, **{column: format(float(row[column]), ".17g") for column in SCORES}})
    if [row["GEOID"] for row in rows] != full_order:
        raise AssertionError("template order mismatch")
    for row in rows:
        for column in SCORES:
            value = float(row[column])
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"out-of-range v1 value {row['GEOID']} {column}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return {"path": str(path.relative_to(ROOT)), "sha256": sha256(path),
            "rows": len(rows), "unique_ids": len({row['GEOID'] for row in rows}),
            "ordered_template_ids": [row["GEOID"] for row in rows] == full_order,
            "eastern_wa_fill": {"rows": len(extras), "policy": "unweighted four-region mean per score field",
                                "values": pooled, "scored_target_claim": False}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "reports/estimator-v1")
    parser.add_argument("--out-report", type=Path, default=ROOT / "reports/estimator-v1-audit.json")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    out_dir, out_report = args.out_dir.resolve(), args.out_report.resolve()

    id_region = {}
    sample_hashes = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        ids = read_geoid_only(sample_path)
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate official GEOIDs in {sample_path}")
        for geoid in ids:
            if geoid in id_region:
                raise ValueError(f"duplicate official GEOID across regions: {geoid}")
            id_region[geoid] = region
        sample_hashes[region] = {"rows": len(ids), "sha256": sha256(sample_path)}
    if len(id_region) != 9379:
        raise ValueError(f"expected 9,379 official IDs, got {len(id_region)}")

    # C0 carries the already-audited exact 9,794-row platform-template order.
    c0_path = ROOT / "reports/candidates/C0_all_zero.csv"
    c0_rows = read_rows(c0_path)
    template_order = [row["GEOID"] for row in c0_rows]
    if len(template_order) != 9794 or len(set(template_order)) != 9794 or not set(id_region).issubset(template_order):
        raise ValueError("C0 is not the exact full-ID template order")
    extra_ids = [geoid for geoid in template_order if geoid not in id_region]
    if len(extra_ids) != 415:
        raise ValueError("expected exactly 415 eastern-WA extras")

    estimates_path = ROOT / "reports/gap-estimates.csv"
    estimates_rows = read_rows(estimates_path)
    estimates = {row["GEOID"]: row for row in estimates_rows}
    if len(estimates) != len(estimates_rows) or set(estimates) != set(id_region):
        raise ValueError("gap-estimates must contain exact official IDs")

    alpha = 0.3288136427728255  # Phase-1 C1 global scale; fixed for this offline sensitivity only.
    shrink_rows = []
    for geoid in id_region:
        row = estimates[geoid]
        transformed = {"GEOID": geoid}
        for field, flag in zip(SCORES[:-1], FLAGS):
            value = float(row[field]) if int(row[flag]) else 0.0
            transformed[field] = value * alpha if field in ("building_gap", "poi_gap") else value
        defined = [transformed[field] for field, flag in zip(SCORES[:-1], FLAGS) if int(row[flag])]
        transformed["coverage_gap_score"] = sum(defined) / len(defined) if defined else 0.5
        shrink_rows.append(transformed)
    shrink_path = out_dir / "v1_shrink_building_poi_alpha.csv"
    shrink_result = write_candidate(shrink_path, shrink_rows, template_order)

    # Recompute the estimator at a smaller denominator multiplier; this is a
    # sign-fixed sensitivity direction relative to the harmful x1.25 C3 probe.
    data_root = workspace / "defined-flags-inputs"
    scale075_rows = []
    feature_hashes, strata_hashes = {}, {}
    for region in REGIONS:
        sample_ids = read_geoid_only(data_root / f"{region}-sample-submission.csv")
        feature_path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
        strata_path = data_root / f"{region}-strata-tract-table.parquet"
        features_table = pq.read_table(feature_path)
        features = {str(row["GEOID"]): row for row in features_table.to_pylist()}
        strata_table = pq.read_table(strata_path, columns=["GEOID", "ALAND", "pop_total", "pop_urban", "pct_urban", "ur_class"])
        strata = {str(row["GEOID"]): row for row in strata_table.to_pylist()}
        if set(sample_ids) - set(features) or set(sample_ids) - set(strata):
            raise ValueError(f"provided feature/strata cache misses official IDs for {region}")
        feature_hashes[region] = sha256(feature_path)
        strata_hashes[region] = sha256(strata_path)
        for geoid in sample_ids:
            estimate = estimate_one(features[geoid], strata[geoid], scale=0.75)
            scale075_rows.append({"GEOID": geoid,
                                  **{column: estimate[column] for column in SCORES}})
    if len(scale075_rows) != 9379 or set(row["GEOID"] for row in scale075_rows) != set(id_region):
        raise ValueError("scale-0.75 estimator did not return exact official IDs")
    scale075_path = out_dir / "v1_denominator_scale_0_75.csv"
    scale075_result = write_candidate(scale075_path, scale075_rows, template_order)

    report = {
        "method": "offline estimator-v1 sensitivity candidates for later explicit pre-registration; not uploaded or included in phase-2 probe manifest",
        "no_labels_or_removed_references_used": True,
        "sample_score_placeholders_read": False,
        "official_rows": 9379,
        "template_rows": 9794,
        "eastern_wa_template_only_rows": 415,
        "phase1_alpha_used_for_component_shrink": alpha,
        "candidate_a": {**shrink_result,
                         "formula": "shrink building_gap and poi_gap by phase-1 C1 alpha; keep transport_gap unchanged; recompute composite over v0 defined-component masks",
                         "interpretation": "global calibration sensitivity, not component-specific accuracy evidence"},
        "candidate_b": {**scale075_result,
                         "formula": "recompute estimate_one with expected-reference denominator multiplier 0.75 (v0 uses 1.0; harmful phase-1 C3 used 1.25)",
                         "interpretation": "sign-fixed denominator-direction sensitivity, not target reconstruction"},
        "inputs": {"gap_estimates_sha256": sha256(estimates_path),
                   "estimate_gaps_script_sha256": sha256(ROOT / "scripts/estimate_gaps.py"),
                   "official_samples": sample_hashes,
                   "feature_cache_sha256": feature_hashes,
                   "strata_input_sha256": strata_hashes},
        "limits": "These are offline unregistered candidates for a later phase. No leaderboard score or accuracy is asserted. Only four official regions supplied estimator inputs; eastern-WA rows use pooled four-region field means solely to preserve template compatibility.",
    }
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("PASS offline estimator v1: two unregistered, full-template bounded candidates; no scored labels used")
    print(f"V1 shrink-building/POI: {shrink_result['sha256']}")
    print(f"V1 denominator x0.75: {scale075_result['sha256']}")
    print(f"Audit: {out_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
