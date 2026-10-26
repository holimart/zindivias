#!/usr/bin/env python3
"""Screen pooled Mx5 school-taxonomy mismatch coverage on official study GEOIDs only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.mx import _bbox_assignments, _education_name, _source_set  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
POI_COLUMNS = ["bbox", "sources", "names", "categories"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def value_distribution(values: np.ndarray, defined: np.ndarray) -> dict[str, Any]:
    x = np.asarray(values, dtype=np.float64)[np.asarray(defined, dtype=bool)]
    if not x.size:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None, "mean": None}
    if not np.isfinite(x).all() or ((x < 0.0) | (x > 1.0)).any():
        raise ValueError("defined pooled mismatch shares must be finite and bounded in [0,1]")
    return {"n": int(x.size), "min": float(x.min()),
            "p50": float(np.quantile(x, 0.5)), "p95": float(np.quantile(x, 0.95)),
            "max": float(x.max()), "mean": float(x.mean())}


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return {"__nonfinite_float__": repr(value)}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def aggregate_batches(batches: Iterable[Any], tree: Any, geoids: list[str], seed: dict[str, Any]
                      ) -> dict[str, Any]:
    """Count each assigned education-name POI once; provider buckets are diagnostics only."""
    import json as json_module

    n = len(geoids)
    positions = {geoid: i for i, geoid in enumerate(geoids)}
    if len(positions) != n:
        raise ValueError("duplicate official GEOID")
    eligible = np.zeros(n, dtype=np.int64)
    mismatches = np.zeros(n, dtype=np.int64)
    providers = list(seed["source_buckets"])
    recognized = set(providers[:-1])
    provider_denominator = {provider: np.zeros(n, dtype=np.int64) for provider in providers}
    provider_mismatch = {provider: np.zeros(n, dtype=np.int64) for provider in providers}
    school_categories = {str(value).casefold() for value in seed["school_categories"]}
    education_pattern = re.compile(seed["education_name_regex"], re.IGNORECASE)
    rows_scanned = assigned_rows = outside_rows = education_rows = 0
    multisource_education_rows = 0
    provider_memberships = 0
    row_digest = hashlib.sha256()

    for batch in batches:
        missing = set(POI_COLUMNS) - set(batch.schema.names)
        if missing:
            raise ValueError(f"provided POI batch missing declared columns {sorted(missing)}")
        bboxes = batch.column("bbox").to_pylist()
        sources = batch.column("sources").to_pylist()
        names = batch.column("names").to_pylist()
        categories = batch.column("categories").to_pylist()
        assigned = _bbox_assignments(bboxes, tree, len(bboxes))
        rows_scanned += len(bboxes)
        outside_rows += int(np.count_nonzero(assigned < 0))
        assigned_rows += int(np.count_nonzero(assigned >= 0))
        for i in range(len(bboxes)):
            row_fingerprint = {"bbox": bboxes[i], "sources": sources[i],
                               "names": names[i], "categories": categories[i]}
            encoded = json_module.dumps(_json_safe(row_fingerprint), sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":"), allow_nan=False).encode("utf-8")
            row_digest.update(len(encoded).to_bytes(8, "big"))
            row_digest.update(encoded)
            tract = int(assigned[i])
            if tract < 0:
                continue
            if tract >= n:
                raise ValueError("bbox assigned outside the official tract index")
            if not _education_name(names[i], education_pattern):
                continue
            education_rows += 1
            primary = categories[i].get("primary") if isinstance(categories[i], dict) else None
            mismatch = str(primary or "").strip().casefold() not in school_categories
            eligible[tract] += 1
            mismatches[tract] += int(mismatch)

            source_names = _source_set(sources[i])
            buckets = sorted(source_names & recognized)
            if len(buckets) > 1:
                multisource_education_rows += 1
            if not buckets:
                buckets = [providers[-1]]
            provider_memberships += len(buckets)
            for provider in buckets:
                provider_denominator[provider][tract] += 1
                provider_mismatch[provider][tract] += int(mismatch)

    defined = eligible > 0
    pooled_share = np.full(n, np.nan, dtype=np.float64)
    pooled_share[defined] = mismatches[defined] / eligible[defined]
    if not np.array_equal(defined, eligible > 0) or np.any(pooled_share[~defined] == 0.0):
        raise AssertionError("zero-denominator tracts must remain undefined, not zero-filled")
    if not np.isfinite(pooled_share[defined]).all() or np.any((pooled_share[defined] < 0) | (pooled_share[defined] > 1)):
        raise ValueError("pooled mismatch share escaped [0,1]")
    return {
        "eligible_places": eligible, "category_mismatches": mismatches,
        "defined": defined, "pooled_share": pooled_share,
        "provider_denominator": provider_denominator,
        "provider_mismatch": provider_mismatch,
        "diagnostics": {
            "rows_scanned": rows_scanned, "assigned_rows": assigned_rows,
            "outside_or_unassigned_rows": outside_rows,
            "education_name_places": education_rows,
            "multisource_education_places": multisource_education_rows,
            "provider_bucket_memberships": provider_memberships,
            "pooled_places_counted_once": int(eligible.sum()),
            "pooled_category_mismatches": int(mismatches.sum()),
            "selected_input_rows_sha256": row_digest.hexdigest(),
        },
    }


def parquet_bytes(geoids: list[str], regions: list[str], aggregate: dict[str, Any]) -> bytes:
    defined = aggregate["defined"]
    share = aggregate["pooled_share"]
    if len(geoids) != len(regions) or len(geoids) != len(defined):
        raise ValueError("per-tract output length mismatch")
    table = pa.table({
        "GEOID": pa.array(geoids, type=pa.string()),
        "region": pa.array(regions, type=pa.string()),
        "education_name_place_count": pa.array(aggregate["eligible_places"], type=pa.int64()),
        "category_mismatch_count": pa.array(aggregate["category_mismatches"], type=pa.int64()),
        "pooled_taxonomy_mismatch_share": pa.array(
            [float(share[i]) if defined[i] else None for i in range(len(geoids))], type=pa.float64()),
        "defined": pa.array(defined, type=pa.bool_()),
    })
    stream = pa.BufferOutputStream()
    pq.write_table(table, stream, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return stream.getvalue().to_pybytes()


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    config_path = ROOT / "config/bias60-harness-preregistered.json"
    harness_path = ROOT / "reports/ideas-harness.json"
    implementation_path = ROOT / "src/bias60/ideas/mx.py"
    config = load_config(config_path)
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    seed = config["seeds"]["Mx5"]
    mx5_entry = harness["per_idea"]["Mx5"]
    if mx5_entry["path"] != "ideas/Mx5.parquet" or not mx5_entry["geoid_partition_exact"]:
        raise ValueError("frozen Mx5 harness entry does not certify exact official ID coverage")
    declared = mx5_entry["accessed_inputs"]["parquet_columns"]
    for region in REGIONS:
        expected = {f"{region}-overture-pois.parquet:{column}" for column in POI_COLUMNS}
        if not expected.issubset(set(declared)):
            raise ValueError(f"Mx5 harness does not declare required provided POI columns for {region}")

    pairs = official_scored_pairs(workspace)
    if len(pairs) != 9379 or len({geoid for geoid, _ in pairs}) != 9379:
        raise ValueError("official scored GEOID list is not an exact 9,379-ID partition")
    if set(region for _, region in pairs) != set(REGIONS):
        raise ValueError("official scored ID list has unexpected regions")
    geoids_by_region = {region: [g for g, r in pairs if r == region] for region in REGIONS}
    if {region: len(geoids_by_region[region]) for region in REGIONS} != config["inputs"]["official_rows_by_region"]:
        raise ValueError("official per-region counts differ from frozen Mx5 contract")

    all_ids: list[str] = []
    all_regions: list[str] = []
    outputs: list[dict[str, Any]] = []
    per_region = {}
    accesses = {}
    input_feature_hashes = {}
    mx5_workspace_artifact = workspace / mx5_entry["path"]
    if not mx5_workspace_artifact.is_file() or sha256_file(mx5_workspace_artifact) != mx5_entry["sha256"]:
        raise ValueError("existing Mx5 artifact missing or hash differs from frozen harness")

    for region in REGIONS:
        ids = geoids_by_region[region]
        inputs = ProvidedInputs(config, workspace, region, ids)
        tree = __import__("shapely").STRtree(inputs.tract_geometries())
        batches = inputs.iter_layer_batches("pois", POI_COLUMNS)
        aggregate = aggregate_batches(batches, tree, ids, seed)
        outputs.append(aggregate)
        all_ids.extend(ids)
        all_regions.extend([region] * len(ids))
        diagnostics = aggregate["diagnostics"]

        # Parity-check the provider-stratified counts from the frozen Mx5 run. These are not
        # used to construct the pooled feature; they verify the common name/category/assignment logic.
        frozen = mx5_entry["diagnostics"][region]
        if diagnostics["rows_scanned"] != frozen["rows_scanned"]:
            raise ValueError(f"{region}: provided POI row count differs from frozen Mx5 diagnostics")
        if diagnostics["outside_or_unassigned_rows"] != frozen["outside_or_unassigned_rows"]:
            raise ValueError(f"{region}: bbox assignment differs from frozen Mx5 diagnostics")
        if diagnostics["education_name_places"] != frozen["education_name_places"]:
            raise ValueError(f"{region}: education-name count differs from frozen Mx5 diagnostics")
        if diagnostics["multisource_education_places"] != frozen["multisource_education_places"]:
            raise ValueError(f"{region}: provider multisource count differs from frozen Mx5 diagnostics")
        for provider in seed["source_buckets"]:
            observed = frozen["coverage_by_source"][provider]
            denominator = aggregate["provider_denominator"][provider]
            mismatch = aggregate["provider_mismatch"][provider]
            defined = denominator > 0
            if (int(defined.sum()) != observed["defined_tracts"]
                    or int(denominator.sum()) != observed["education_name_places"]
                    or int(mismatch.sum()) != observed["category_mismatches"]):
                raise ValueError(f"{region}/{provider}: source-sliced parity against Mx5 failed")
        if len(ids) != len(set(ids)):
            raise ValueError(f"{region}: duplicate official GEOID")
        defined = aggregate["defined"]
        share = aggregate["pooled_share"]
        per_region[region] = {
            "official_ids": len(ids), "defined_ids": int(defined.sum()),
            "undefined_zero_denominator_ids": int((~defined).sum()),
            "coverage_fraction": float(defined.mean()),
            "education_name_places": diagnostics["education_name_places"],
            "category_mismatches": int(aggregate["category_mismatches"].sum()),
            "multisource_education_places_counted_once": diagnostics["multisource_education_places"],
            "pooled_mismatch_share_distribution": value_distribution(share, defined),
            "selected_input_rows_sha256": diagnostics["selected_input_rows_sha256"],
        }
        accesses[region] = {key: sorted(set(value)) for key, value in inputs.accessed.items()}
        input_feature_hashes[region] = diagnostics["selected_input_rows_sha256"]

    if len(all_ids) != 9379 or len(set(all_ids)) != 9379:
        raise ValueError("combined output does not partition exactly the 9,379 official GEOIDs")
    total_defined = sum(int(item["defined"].sum()) for item in outputs)
    pooled_defined = np.concatenate([item["defined"] for item in outputs])
    pooled_values = np.concatenate([item["pooled_share"] for item in outputs])
    if int(pooled_defined.sum()) != total_defined:
        raise AssertionError("aggregate coverage count mismatch")
    pooled_stats = value_distribution(pooled_values, pooled_defined)
    coverage = total_defined / len(all_ids)
    source_coverage = {
        provider: sum(mx5_entry["diagnostics"][region]["coverage_by_source"][provider]["defined_tracts"]
                      for region in REGIONS) / len(all_ids)
        for provider in seed["source_buckets"]
    }
    best_single_source = max(source_coverage, key=source_coverage.get)
    lift_percentage_points = 100.0 * (coverage - source_coverage[best_single_source])
    coverage_pass = coverage >= 0.95
    lift_pass = lift_percentage_points >= 9.22

    aggregate = {
        key: np.concatenate([item[key] for item in outputs])
        for key in ("eligible_places", "category_mismatches", "defined", "pooled_share")
    }
    payload = parquet_bytes(all_ids, all_regions, aggregate)
    payload_repeat = parquet_bytes(all_ids, all_regions, aggregate)
    if payload != payload_repeat:
        raise ValueError("pooled Mx5 Parquet serialization is not byte deterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    report = {
        "schema_version": 1,
        "audit": "Pooled Mx5 education-name taxonomy mismatch covariate coverage; label-free screen, not promotion",
        "feature": {
            "definition": "per-tract count of provided education-name Overture POI rows whose categories.primary is outside frozen Mx5 school category set, divided by all assigned education-name rows; each row contributes once regardless of provider memberships",
            "frozen_education_name_regex": seed["education_name_regex"],
            "frozen_school_categories": sorted({str(value).casefold() for value in seed["school_categories"]}),
            "bbox_assignment": "existing Mx5 center-of-bbox assignment to at most one official tract; ties by first tract index",
            "zero_denominator": "share is null/undefined when eligible place count is zero; never filled with zero",
        },
        "scope": {"official_ids": len(all_ids), "exact_geoid_partition": True,
                  "defined_ids": total_defined, "undefined_zero_denominator_ids": len(all_ids) - total_defined,
                  "coverage_fraction": coverage, "multi_source_education_rows_counted_once": sum(
                      item["diagnostics"]["multisource_education_places"] for item in outputs),
                  "pooled_place_count": sum(item["diagnostics"]["pooled_places_counted_once"] for item in outputs),
                  "provider_bucket_memberships_for_parity_only": sum(
                      item["diagnostics"]["provider_bucket_memberships"] for item in outputs),
                  "sample_score_values_read": False, "labels_read": False,
                  "external_or_removed_reference_data_read": False, "uploads": 0,
                  "existing_mx5_feature_modified_or_replaced": False},
        "baseline": {
            "source_specific_defined_tract_fraction": {
                provider: {"defined_ids": sum(mx5_entry["diagnostics"][r]["coverage_by_source"][provider]["defined_tracts"]
                                               for r in REGIONS),
                           "official_ids": len(all_ids),
                           "coverage_fraction": source_coverage[provider]}
                for provider in seed["source_buckets"]
            },
            "best_single_source": best_single_source,
            "best_single_source_coverage_fraction": source_coverage[best_single_source],
            "parity_with_frozen_mx5_source_slices": True,
            "frozen_mx5_artifact_sha256": mx5_entry["sha256"],
        },
        "after": {"pooled_coverage": {"defined_ids": total_defined,
                                       "official_ids": len(all_ids),
                                       "fraction": coverage},
                  "pooled_mismatch_share_distribution": pooled_stats,
                  "regions": per_region},
        "acceptance": {"coverage_minimum": 0.95, "coverage_pass": coverage_pass,
                       "lift_over_best_single_source_minimum_percentage_points": 9.22,
                       "lift_over_best_single_source_percentage_points": lift_percentage_points,
                       "lift_pass": lift_pass,
                       "best_single_source": best_single_source,
                       "exact_geoid_partition": True,
                       "defined_values_finite_bounded": True,
                       "undefined_to_zero_fills": 0,
                       "acceptance_pass": coverage_pass and lift_pass},
        "provenance": {
            "harness_config": {"path": "config/bias60-harness-preregistered.json",
                               "sha256": sha256_file(config_path)},
            "harness_report": {"path": "reports/ideas-harness.json",
                               "sha256": sha256_file(harness_path)},
            "mx_implementation": {"path": "src/bias60/ideas/mx.py",
                                  "sha256": sha256_file(implementation_path)},
            "audit_script": {"path": "scripts/audit_mx5_pooled_provider_coverage.py",
                             "sha256": sha256_file(Path(__file__).resolve())},
            "frozen_mx5_artifact": {"path": str(mx5_workspace_artifact),
                                    "sha256": sha256_file(mx5_workspace_artifact)},
            "official_geoid_partition_sha256": hashlib.sha256(
                json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "selected_provided_poi_input_hashes": input_feature_hashes,
            "selected_input_definition": "SHA-256 over canonical JSON rows (in provided parquet row order) of only bbox,sources,names,categories read from the provided overture-pois objects",
            "accessed_inputs": accesses,
            "provided_package_bucket": config["inputs"]["package_bucket"],
        },
        "per_tract_artifact": {"path": str(artifact_path), "rows": len(all_ids),
                               "sha256": hashlib.sha256(payload).hexdigest(),
                               "deterministic_bytes": True},
        "determinism": {"aggregate_calculation_input_order_fixed": True,
                        "serialized_bytes_repeatable": True},
        "interpretation": ("Pooled feature meets the 95% coverage and minimum-lift criteria. This is a feature coverage screen only; do not integrate or promote without separate review."
                           if coverage_pass and lift_pass else
                           "Pooled feature misses at least one preregistered coverage/lift threshold; report the measured limit and do not integrate."),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/mx5-pooled-provider-coverage.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact = Path(args.artifact) if args.artifact else workspace / "mx5-pooled-provider-coverage/pooled-mx5.parquet"
    report = run(workspace, report_path, artifact)
    print(json.dumps({"out": str(report_path.relative_to(ROOT)),
                      "coverage": report["scope"]["coverage_fraction"],
                      "lift_over_best_single_source_percentage_points": report["acceptance"]["lift_over_best_single_source_percentage_points"],
                      "defined_ids": report["scope"]["defined_ids"],
                      "undefined_ids": report["scope"]["undefined_zero_denominator_ids"],
                      "pooled_mismatch_share_distribution": report["after"]["pooled_mismatch_share_distribution"],
                      "multi_source_rows": report["scope"]["multi_source_education_rows_counted_once"],
                      "acceptance_pass": report["acceptance"]["acceptance_pass"],
                      "artifact": report["per_tract_artifact"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
