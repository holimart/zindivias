#!/usr/bin/env python3
"""Compare globally pooled and region-specific C2 component-rate normalizers.

This is a deterministic sensitivity screen, not target validation. It reads
only authoritative sample GEOIDs, C2's provided-data estimator output/audit,
and hashes of the cached permitted feature inputs. It never reads sample score
placeholders, labels, or removed reference layers.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
SAMPLE_COLUMNS = ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]
COMPONENTS = ("transport", "building", "poi")
SCORE_COLUMNS = ("transport_gap", "building_gap", "poi_gap", "coverage_gap_score")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        return list(reader), list(reader.fieldnames or [])


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot summarize an empty sequence")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def distribution(values: list[float]) -> dict:
    if not values:
        raise ValueError("cannot summarize an empty sequence")
    return {
        "n": len(values),
        "mean": sum(values) / len(values),
        "min": min(values),
        "q05": quantile(values, 0.05),
        "q25": quantile(values, 0.25),
        "q50": quantile(values, 0.50),
        "q75": quantile(values, 0.75),
        "q95": quantile(values, 0.95),
        "max": max(values),
    }


def clip01(value: float) -> float:
    return min(1.0, max(0.0, value))


def candidate_validity(path: Path, expected_ids: list[str], id_regions: dict[str, str]) -> dict:
    rows, columns = read_csv(path)
    actual_ids = [row.get("GEOID", "") for row in rows]
    ids_exact = actual_ids == expected_ids and len(actual_ids) == len(set(actual_ids))
    row_valid = []
    for row in rows:
        valid = list(row) == SAMPLE_COLUMNS
        for column in SCORE_COLUMNS:
            try:
                value = float(row[column])
            except (KeyError, TypeError, ValueError):
                valid = False
                continue
            valid = valid and math.isfinite(value) and 0.0 <= value <= 1.0
        row_valid.append(valid)
    by_region = {}
    for region in REGIONS:
        indices = [i for i, geoid in enumerate(actual_ids) if id_regions.get(geoid) == region]
        by_region[region] = {
            "rows": len(indices),
            "valid_rows": sum(row_valid[i] for i in indices),
            "validity_rate": sum(row_valid[i] for i in indices) / len(indices) if indices else 0.0,
        }
    return {
        "artifact": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "sha256": sha256(path),
        "rows": len(rows),
        "columns": columns,
        "exact_ordered_ids_once": ids_exact,
        "valid_rows": sum(row_valid),
        "validity_rate": sum(row_valid) / len(row_valid) if row_valid else 0.0,
        "per_region": by_region,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True,
                        help="authorized workspace containing samples and cached estimator inputs")
    parser.add_argument("--estimates", type=Path, default=ROOT / "reports/gap-estimates.csv")
    parser.add_argument("--estimator-audit", type=Path,
                        default=ROOT / "reports/gap-estimator-audit.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "reports/estimator-sensitivity")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/estimator-sensitivity.json")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    estimates_path = args.estimates.resolve()
    audit_path = args.estimator_audit.resolve()
    out_dir = args.out_dir.resolve()
    report_path = args.report.resolve()

    expected_ids: list[str] = []
    id_regions: dict[str, str] = {}
    sample_inputs = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        sample_rows, columns = read_csv(sample_path)
        if columns != SAMPLE_COLUMNS:
            raise ValueError(f"unexpected authoritative sample schema: {sample_path}: {columns}")
        ids = [row["GEOID"] for row in sample_rows]
        if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"empty or duplicate official GEOID in {sample_path}")
        for geoid in ids:
            if geoid in id_regions:
                raise ValueError(f"duplicate GEOID across official samples: {geoid}")
            id_regions[geoid] = region
        expected_ids.extend(ids)
        sample_inputs[region] = {
            "path": f"$COMPETITION_WORKSPACE/samples/{sample_path.name}",
            "sha256": sha256(sample_path),
            "rows": len(ids),
            "columns": columns,
            "read_values": ["GEOID only; score placeholders are ignored"],
        }
    if len(expected_ids) != 9379:
        raise ValueError(f"expected 9,379 official sample IDs, got {len(expected_ids)}")

    estimates, estimate_columns = read_csv(estimates_path)
    estimate_by_id = {row.get("GEOID", ""): row for row in estimates}
    if (len(estimate_by_id) != len(estimates) or
            [row.get("GEOID") for row in estimates] != expected_ids):
        raise ValueError("C2 estimator rows must match the authoritative ordered GEOID list exactly")
    required = {"GEOID"}
    required.update(f"{name}_gap" for name in COMPONENTS)
    required.update(f"{name}_defined" for name in COMPONENTS)
    if not required.issubset(estimate_columns):
        raise ValueError(f"C2 estimator output missing columns: {sorted(required - set(estimate_columns))}")

    # Component rates are arithmetic means of per-tract (1 - C2 gap) values,
    # considering only rows whose corresponding C2 defined flag is 1. These
    # rates use no labels and are not reference-layer ratios.
    global_rates = {}
    regional_rates = {region: {} for region in REGIONS}
    defined_counts = {region: {} for region in REGIONS}
    for component in COMPONENTS:
        gap_column, flag_column = f"{component}_gap", f"{component}_defined"
        values = []
        regional_values = {region: [] for region in REGIONS}
        for geoid in expected_ids:
            row = estimate_by_id[geoid]
            if row[flag_column] not in ("0", "1"):
                raise ValueError(f"nonbinary {flag_column} for {geoid}: {row[flag_column]}")
            gap = float(row[gap_column])
            if not math.isfinite(gap) or not 0.0 <= gap <= 1.0:
                raise ValueError(f"invalid C2 {gap_column} for {geoid}: {gap}")
            if row[flag_column] == "1":
                rate = 1.0 - gap
                values.append(rate)
                regional_values[id_regions[geoid]].append(rate)
        if not values or any(not regional_values[region] for region in REGIONS):
            raise ValueError(f"no defined C2 rate observations for {component} in one or more regions")
        global_rates[component] = sum(values) / len(values)
        for region in REGIONS:
            regional_rates[region][component] = (
                sum(regional_values[region]) / len(regional_values[region])
            )
            defined_counts[region][component] = len(regional_values[region])
        if global_rates[component] <= 0 or any(
                regional_rates[region][component] <= 0 for region in REGIONS):
            raise ValueError(f"nonpositive normalizer rate for {component}")

    variants: dict[str, list[dict[str, str]]] = {"C0": [], "C1": []}
    for geoid in expected_ids:
        row = estimate_by_id[geoid]
        region = id_regions[geoid]
        pair = {}
        for candidate_id, rate_map in (("C0", global_rates), ("C1", regional_rates[region])):
            components_out = {}
            for component in COMPONENTS:
                gap_column, flag_column = f"{component}_gap", f"{component}_defined"
                if row[flag_column] == "1":
                    rate_ratio = (1.0 - float(row[gap_column])) / rate_map[component]
                    components_out[f"{component}_gap"] = clip01(1.0 - rate_ratio)
                else:
                    # Preserve the existing estimator's undefined placeholder;
                    # it is excluded from the composite below by the same flag.
                    components_out[f"{component}_gap"] = 0.0
            defined_gaps = [components_out[f"{component}_gap"] for component in COMPONENTS
                            if row[f"{component}_defined"] == "1"]
            if not defined_gaps:
                raise ValueError(f"no defined top-level component for authoritative GEOID {geoid}")
            pair[candidate_id] = {
                "GEOID": geoid,
                "transport_gap": components_out["transport_gap"],
                "building_gap": components_out["building_gap"],
                "poi_gap": components_out["poi_gap"],
                "coverage_gap_score": sum(defined_gaps) / len(defined_gaps),
            }
        for candidate_id in ("C0", "C1"):
            candidate_row = pair[candidate_id]
            included = []
            for component in COMPONENTS:
                gap_column = f"{component}_gap"
                if row[f"{component}_defined"] == "1":
                    included.append(candidate_row[gap_column])
                elif candidate_row[gap_column] != 0.0:
                    raise ValueError(f"undefined placeholder changed for {candidate_id} {geoid} {component}")
            recomputed = sum(included) / len(included)
            if abs(recomputed - candidate_row["coverage_gap_score"]) > 1e-12:
                raise ValueError(f"defined-component composite mismatch for {candidate_id} {geoid}")
            variants[candidate_id].append(candidate_row)

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "C0": out_dir / "C0_global_pooled_component_rates.csv",
        "C1": out_dir / "C1_region_specific_component_rates.csv",
    }
    for candidate_id, path in paths.items():
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=SAMPLE_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(variants[candidate_id])

    measures = {
        candidate_id: candidate_validity(paths[candidate_id], expected_ids, id_regions)
        for candidate_id in ("C0", "C1")
    }
    if not all(measure["exact_ordered_ids_once"] and measure["validity_rate"] == 1.0
               and measure["rows"] == 9379 for measure in measures.values()):
        raise ValueError("sensitivity candidate failed authoritative ID/range validation")

    deltas = {}
    for region in REGIONS:
        region_ids = [geoid for geoid in expected_ids if id_regions[geoid] == region]
        deltas[region] = {}
        for column in SCORE_COLUMNS:
            values = [float(variants["C1"][i][column]) - float(variants["C0"][i][column])
                      for i, geoid in enumerate(expected_ids) if id_regions[geoid] == region]
            deltas[region][column] = distribution(values)
    paired = {}
    for column in SCORE_COLUMNS:
        values = [float(variants["C1"][i][column]) - float(variants["C0"][i][column])
                  for i in range(len(expected_ids))]
        paired[column] = distribution(values)

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("rows") != 9379 or audit.get("no_accuracy_claim") is not True:
        raise ValueError("C2 estimator audit missing expected row count/no-label caveat")
    feature_lineage = {}
    for region in REGIONS:
        feature_info = audit["feature_aggregation"][region]
        feature_path = Path(feature_info["feature_cache"])
        strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        strata_sha = sha256(strata_path)
        expected_strata_sha = feature_info["source_hashes"][strata_path.name]
        if strata_sha != expected_strata_sha:
            raise ValueError(f"strata hash differs from C2 audit for {region}")
        feature_sha = sha256(feature_path)
        if feature_sha != feature_info["feature_cache_sha256"]:
            raise ValueError(f"feature-cache hash differs from C2 audit for {region}")
        feature_lineage[region] = {
            "permitted_feature_cache": str(feature_path),
            "feature_cache_sha256": feature_sha,
            "strata_file": f"$COMPETITION_WORKSPACE/defined-flags-inputs/{strata_path.name}",
            "strata_sha256": strata_sha,
            "feature_source_layers": ["provided Overture roads", "provided Overture buildings",
                                      "provided Overture POIs"],
            "strata_covariates": ["ALAND", "pop_total", "pop_urban", "pct_urban"],
            "excluded": ["sample score placeholders", "organiser labels", "removed references",
                         "external data", "ACS housing_units values"],
        }

    # Preserve the distinct, already frozen public-probe C0/C1 identities.
    frozen_manifest_path = ROOT / "reports/candidates.json"
    frozen_manifest = json.loads(frozen_manifest_path.read_text(encoding="utf-8"))
    frozen = {row["candidate_id"]: row for row in frozen_manifest["candidate_set"]}
    frozen_refs = {}
    for candidate_id in ("C0", "C1"):
        item = frozen[candidate_id]
        path = ROOT / item["artifact"]
        artifact_hash = sha256(path)
        if artifact_hash != item["sha256"]:
            raise ValueError(f"frozen candidate hash mismatch: {candidate_id}")
        frozen_refs[candidate_id] = {
            "artifact": item["artifact"],
            "sha256": artifact_hash,
            "meaning": item["description"],
            "validity": candidate_validity(path, expected_ids, id_regions),
            "note": "These existing probe candidates are unchanged; C0/C1 IDs in this sensitivity bundle are bundle-local variants.",
        }

    report = {
        "screen": "global pooled vs region-specific top-level component-rate normalization",
        "purpose": "deterministic no-label sensitivity screen for later steward-authorized calibration; not an accuracy measurement",
        "variant_id_scope": "C0/C1 names below are local to this sensitivity bundle and do not overwrite frozen public-probe C0/C1 candidates",
        "method": {
            "input_component_rate": "bounded retained fraction 1 - C2 component gap (the C2 gaps are already clipped to [0,1]), only where that component's C2 defined flag is 1; this is not a raw observed/reference ratio",
            "global_rate": "unweighted arithmetic mean of defined per-tract bounded retained fractions across all 9,379 authoritative IDs",
            "region_rate": "unweighted arithmetic mean of defined per-tract bounded retained fractions within each authoritative region",
            "normalized_gap": "clip(1 - input_component_rate / selected_normalizer_rate, 0, 1)",
            "candidate_composite": "mean of normalized top-level component gaps whose original C2 defined flags equal 1; undefined components remain excluded (same exact mask as C2)",
            "C0": "use one fixed globally pooled normalizer for each of transport, building, and POI",
            "C1": "use the corresponding region-specific normalizer for each of transport, building, and POI",
        },
        "rates": {
            "C0_global_pooled_component_rates": global_rates,
            "C1_region_specific_component_rates": regional_rates,
            "defined_tract_counts_by_region": defined_counts,
        },
        "measure_before_C0": measures["C0"],
        "measure_after_C1": measures["C1"],
        "paired_delta_C1_minus_C0": {
            "all_regions": paired,
            "per_region": deltas,
        },
        "defined_component_semantics_check": {
            "source": "C2 gap-estimator-audit flags",
            "counts_by_region": {
                region: {
                    component: defined_counts[region][component]
                    for component in COMPONENTS
                }
                for region in REGIONS
            },
            "same_flag_mask_used_for_C0_and_C1": True,
        },
        "feature_lineage": {
            "C2_estimates": {"path": str(estimates_path), "sha256": sha256(estimates_path),
                             "read_columns": estimate_columns},
            "C2_audit": {"path": str(audit_path), "sha256": sha256(audit_path)},
            "provided_inputs_by_region": feature_lineage,
            "official_samples_by_region": sample_inputs,
            "frozen_probe_C0_C1_unchanged": frozen_refs,
            "removed_or_external_reference_layers_used": [],
        },
        "no_label_caveat": "All rates derive from C2's heuristic gaps over permitted provided Overture and strata inputs. No target labels, removed reference layers, external data, public leaderboard values, or sample score placeholders were used. Differences measure normalization sensitivity only, not accuracy or true regional target rates.",
        "upload_status": "local screen only; no upload performed; existing platform-contract stop remains in force",
        "environment": {"python": platform.python_version()},
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("PASS C0/C1 sensitivity outputs: 9,379/9,379 exact IDs; "
          f"validity rates {measures['C0']['validity_rate']:.6f}/{measures['C1']['validity_rate']:.6f}")
    print(f"C0 SHA-256 {measures['C0']['sha256']}; C1 SHA-256 {measures['C1']['sha256']}")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
