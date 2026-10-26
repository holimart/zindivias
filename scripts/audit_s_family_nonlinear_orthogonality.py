#!/usr/bin/env python3
"""Label-free, region-stratified NMI screen for S1-S5 against registered idea outputs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
SPATIAL_IDEAS = frozenset({"S1", "S2", "S3", "S4", "S5"})
MIN_OVERLAP = 30
NMI_FLAG = 0.80


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def encode_feature(values: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Encode finite values as categories if integer/low-cardinality, otherwise decile bins."""
    values = np.asarray(values, dtype=np.float64)
    codes = np.full(values.shape, -1, dtype=np.int16)
    finite_mask = np.isfinite(values)
    finite = values[finite_mask]
    if not finite.size:
        return codes, {"encoding": "empty", "categories_or_bins": 0, "defined": 0}
    unique = np.unique(finite)
    integer_valued = bool(np.all(np.abs(finite - np.rint(finite)) <= 1e-12))
    if integer_valued and unique.size <= 10:
        _, inverse = np.unique(finite, return_inverse=True)
        codes[finite_mask] = inverse.astype(np.int16)
        encoding = "category_integer_low_cardinality"
        count = int(unique.size)
    else:
        quantiles = np.quantile(finite, np.arange(0.1, 1.0, 0.1), method="linear")
        codes[finite_mask] = np.searchsorted(quantiles, finite, side="right").astype(np.int16)
        encoding = "empirical_decile_bins"
        count = int(np.unique(codes[finite_mask]).size)
    return codes, {"encoding": encoding, "categories_or_bins": count,
                   "defined": int(finite.size)}


def normalized_mutual_information(left: np.ndarray, right: np.ndarray) -> float:
    """Symmetric arithmetic NMI: 2*MI/(H(left)+H(right)); constants map to 0."""
    x = np.asarray(left, dtype=np.int64)
    y = np.asarray(right, dtype=np.int64)
    if x.shape != y.shape or x.ndim != 1 or not x.size or np.any(x < 0) or np.any(y < 0):
        raise ValueError("NMI requires same-length, nonempty, nonnegative encoded labels")
    _, x = np.unique(x, return_inverse=True)
    _, y = np.unique(y, return_inverse=True)
    nx, ny = int(x.max()) + 1, int(y.max()) + 1
    counts = np.bincount(x * ny + y, minlength=nx * ny).reshape(nx, ny).astype(np.float64)
    pxy = counts / float(x.size)
    px = pxy.sum(axis=1)
    py = pxy.sum(axis=0)
    hx = -float(np.sum(px[px > 0] * np.log(px[px > 0])))
    hy = -float(np.sum(py[py > 0] * np.log(py[py > 0])))
    if hx + hy <= 0.0:
        return 0.0
    independent = px[:, None] * py[None, :]
    nonzero = pxy > 0
    mi = float(np.sum(pxy[nonzero] * np.log(pxy[nonzero] / independent[nonzero])))
    nmi = 2.0 * mi / (hx + hy)
    if not np.isfinite(nmi):
        raise ValueError("NMI must be finite")
    return float(np.clip(nmi, 0.0, 1.0))


def measure_pair_nmi(left_codes: np.ndarray, right_codes: np.ndarray,
                     minimum_overlap: int = MIN_OVERLAP) -> dict[str, Any]:
    left_codes = np.asarray(left_codes, dtype=np.int16)
    right_codes = np.asarray(right_codes, dtype=np.int16)
    if left_codes.shape != right_codes.shape or left_codes.ndim != 1:
        raise ValueError("pair feature encodings must be aligned vectors")
    joint = (left_codes >= 0) & (right_codes >= 0)
    count = int(joint.sum())
    if count < minimum_overlap:
        return {"status": "excluded_below_minimum_overlap", "joint_defined": count, "nmi": None}
    value = normalized_mutual_information(left_codes[joint], right_codes[joint])
    return {"status": "measured", "joint_defined": count, "nmi": value}


def candidate_pairs(feature_keys: list[str], feature_meta: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
    """All unique feature pairs where one idea is S1-S5 and the pair crosses idea IDs."""
    spatial = [key for key in feature_keys if feature_meta[key]["idea"] in SPATIAL_IDEAS]
    pairs = set()
    for left in spatial:
        for right in feature_keys:
            if feature_meta[left]["idea"] == feature_meta[right]["idea"]:
                continue
            pairs.add(tuple(sorted((left, right))))
    return sorted(pairs)


def serialize_pair_measurements(rows: list[list[Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["left_feature_index", "right_feature_index", "region_index", "joint_defined", "nmi"])
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _read_registry_ids(registry_path: Path) -> list[str]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    return sorted(idea["id"] for idea in registry["ideas"] if idea.get("status") == "implemented")


def load_registered_features(workspace: Path, registry_path: Path, harness_path: Path
                              ) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, dict[str, str]],
                                         dict[str, Any], list[tuple[str, str]], dict[str, list[str]]]:
    """Read/hash-check only registered idea outputs and reconcile every emission to official IDs."""
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    idea_ids = _read_registry_ids(registry_path)
    if set(idea_ids) != set(harness["per_idea"]):
        raise ValueError("implemented registry IDs differ from frozen harness ideas")
    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len({geoid for geoid, _ in official}) != 9379:
        raise ValueError("official scored GEOIDs are not an exact 9,379-ID partition")
    geoids_by_region = {region: [g for g, r in official if r == region] for region in REGIONS}
    if sum(map(len, geoids_by_region.values())) != 9379 or any(not geoids_by_region[r] for r in REGIONS):
        raise ValueError("official region/GEOID partition is incomplete")
    positions = {region: {geoid: i for i, geoid in enumerate(geoids_by_region[region])}
                 for region in REGIONS}
    values: dict[str, dict[str, np.ndarray]] = {}
    feature_meta: dict[str, dict[str, str]] = {}
    manifest: dict[str, Any] = {}
    for idea_id in idea_ids:
        entry = harness["per_idea"][idea_id]
        if not entry.get("geoid_partition_exact") or entry.get("forbidden_source_hits"):
            raise ValueError(f"harness does not certify clean exact-ID output for {idea_id}")
        path = workspace / "ideas" / f"{idea_id}.parquet"
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = sha256_file(path)
        if digest != entry["sha256"]:
            raise ValueError(f"{idea_id}: artifact hash differs from frozen harness")
        table = pq.read_table(path, columns=["GEOID", "region", "idea", "family", "component",
                                             "channel", "value", "defined"])
        # Each output emission gets a separate array; un-defined values remain NaN.
        arrays: dict[tuple[str, str], dict[str, np.ndarray]] = {}
        seen: dict[tuple[str, str], dict[str, set[str]]] = {}
        rows = table.to_pylist()
        for row in rows:
            region, geoid = str(row["region"]), str(row["GEOID"])
            if region not in positions or geoid not in positions[region] or row["idea"] != idea_id:
                raise ValueError(f"{idea_id}: row outside registered official region/GEOID scope")
            column_tuple = (str(row["component"]), str(row["channel"]))
            if column_tuple not in arrays:
                arrays[column_tuple] = {r: np.full(len(geoids_by_region[r]), np.nan, dtype=np.float64)
                                        for r in REGIONS}
                seen[column_tuple] = {r: set() for r in REGIONS}
            if geoid in seen[column_tuple][region]:
                raise ValueError(f"{idea_id}/{column_tuple}: duplicate {region}/{geoid}")
            seen[column_tuple][region].add(geoid)
            if row["defined"]:
                value = float(row["value"])
                if not np.isfinite(value):
                    raise ValueError(f"{idea_id}/{column_tuple}: defined nonfinite feature")
                arrays[column_tuple][region][positions[region][geoid]] = value
        for column_tuple, region_arrays in arrays.items():
            for region in REGIONS:
                if seen[column_tuple][region] != set(geoids_by_region[region]):
                    raise ValueError(f"{idea_id}/{column_tuple}/{region}: incomplete official GEOID coverage")
            key = f"{idea_id}|{column_tuple[0]}|{column_tuple[1]}"
            if key in values:
                raise ValueError(f"duplicate registered feature key {key}")
            values[key] = region_arrays
            feature_meta[key] = {"idea": idea_id, "family": str(rows[0]["family"]),
                                 "component": column_tuple[0], "channel": column_tuple[1]}
        manifest[idea_id] = {"path": str(path), "sha256": digest, "rows": table.num_rows,
                             "features": len(arrays), "official_ids_per_feature_by_region":
                             {region: len(geoids_by_region[region]) for region in REGIONS}}
    return values, feature_meta, manifest, official, geoids_by_region


def build_report(workspace: Path, registry_path: Path, harness_path: Path) -> dict[str, Any]:
    feature_values, feature_meta, manifest, official, geoids_by_region = load_registered_features(
        workspace, registry_path, harness_path)
    features = sorted(feature_values)
    spatial_features = [key for key in features if feature_meta[key]["idea"] in SPATIAL_IDEAS]
    if not spatial_features:
        raise ValueError("no S1-S5 emissions are registered")
    pairs = candidate_pairs(features, feature_meta)
    encodings: dict[str, dict[str, np.ndarray]] = {}
    encoding_meta: dict[str, dict[str, Any]] = {}
    for key in features:
        encodings[key], encoding_meta[key] = {}, {}
        for region in REGIONS:
            encodings[key][region], encoding_meta[key][region] = encode_feature(feature_values[key][region])

    results: list[dict[str, Any]] = []
    excluded_by_region = {region: 0 for region in REGIONS}
    eligible_by_region = {region: 0 for region in REGIONS}
    candidate_by_region = {region: len(pairs) for region in REGIONS}
    max_nmi = -1.0
    for left, right in pairs:
        for region in REGIONS:
            x = encodings[left][region]
            y = encodings[right][region]
            pair_result = measure_pair_nmi(x, y)
            if pair_result["status"] == "excluded_below_minimum_overlap":
                excluded_by_region[region] += 1
                continue
            nmi = pair_result["nmi"]
            if not np.isfinite(nmi) or not 0.0 <= nmi <= 1.0:
                raise ValueError("eligible NMI must be finite and within [0,1]")
            results.append({"left": left, "right": right,
                            "region": region, "joint_defined": pair_result["joint_defined"],
                            "nmi": nmi,
                            "left_encoding": encoding_meta[left][region]["encoding"],
                            "right_encoding": encoding_meta[right][region]["encoding"],
                            "left_categories_or_bins": encoding_meta[left][region]["categories_or_bins"],
                            "right_categories_or_bins": encoding_meta[right][region]["categories_or_bins"]})
            eligible_by_region[region] += 1
            max_nmi = max(max_nmi, nmi)

    measured = results
    flagged = [row for row in measured if row["nmi"] >= NMI_FLAG]
    # Ensure every registered S emission is compared with every other registered idea exactly once.
    expected_pair_count = len(pairs)
    if (len(results) != sum(eligible_by_region.values())
            or sum(eligible_by_region.values()) + sum(excluded_by_region.values())
            != expected_pair_count * len(REGIONS)):
        raise AssertionError("pair accounting incomplete")

    config_path = ROOT / "config/bias60-harness-preregistered.json"
    feature_index = {key: index for index, key in enumerate(features)}
    region_index = {region: index for index, region in enumerate(REGIONS)}
    compact_results = [[feature_index[row["left"]], feature_index[row["right"]],
                        region_index[row["region"]], row["joint_defined"], row["nmi"]]
                       for row in measured]
    encoding_summary = [
        {"feature": feature_index[key], "region": region_index[region],
         "encoding": encoding_meta[key][region]["encoding"],
         "categories_or_bins": encoding_meta[key][region]["categories_or_bins"],
         "defined": encoding_meta[key][region]["defined"]}
        for key in features for region in REGIONS
    ]
    return {
        "schema_version": 1,
        "audit": "Region-stratified nonlinear mutual-information screen for S1-S5; diagnostic only, no feature integration",
        "method": {
            "scope": "Every registered S1-S5 emission versus every emission from a different currently implemented idea, including cross-pairs among S1-S5; same-idea emission pairs are excluded. Each unordered feature pair is evaluated once per official region.",
            "continuous_encoding": "Within each region, use deterministic empirical deciles (NumPy linear quantiles at 0.1 through 0.9; ties may collapse bins); bins are computed from that feature's region-defined values before pairwise masking.",
            "categorical_encoding": "Keep integer-valued features with at most 10 distinct region-defined values as categories; this retains binary/flag features as categories. Other features use empirical-decile bins.",
            "pairwise_mask": "Only rows with both emissions defined and finite are included; calculate NMI when joint count >=30.",
            "nmi": "Symmetric arithmetic normalization: 2*MI/(H(X)+H(Y)) using natural-log entropy; if either/both encodings have zero entropy, report NMI=0 as non-informative. Numerical results are clipped only for floating-point roundoff into [0,1].",
            "threshold": NMI_FLAG,
        },
        "scope": {"official_ids": len(official), "exact_9379_id_partition": True,
                  "official_ids_by_region": {region: len(geoids_by_region[region]) for region in REGIONS},
                  "registered_ideas": len(manifest), "registered_feature_emissions": len(features),
                  "s_family_feature_emissions": len(spatial_features),
                  "candidate_feature_pairs": expected_pair_count,
                  "eligible_region_pair_measurements": len(measured),
                  "flagged_region_pairs_nmi_ge_0_80": len(flagged),
                  "labels_read": False, "sample_score_values_read": False,
                  "external_or_removed_reference_inputs_read": False,
                  "features_integrated_or_registry_changed": False, "uploads": 0},
        "pair_accounting": {"candidate_feature_pairs_per_region": candidate_by_region,
                            "eligible_pairs_per_region": eligible_by_region,
                            "excluded_below_30_per_region": excluded_by_region,
                            "nmi_measurements_total": len(measured),
                            "pair_result_row_schema": ["left_feature_index", "right_feature_index", "region_index", "joint_defined", "nmi"],
                            "complete": True},
        "feature_catalog": [{"index": feature_index[key], "key": key, **feature_meta[key]}
                           for key in features],
        "region_catalog": [{"index": region_index[region], "region": region} for region in REGIONS],
        "feature_encodings_by_region": encoding_summary,
        "maximum_nmi": max_nmi if max_nmi >= 0 else None,
        "flagged_pairs_ge_0_80": flagged,
        "pair_results": compact_results,
        "inputs": {"idea_artifacts": manifest,
                   "official_geoid_region_sha256": hashlib.sha256(
                       "\n".join(f"{geoid},{region}" for geoid, region in official).encode("utf-8")).hexdigest(),
                   "harness_report_sha256": sha256_file(harness_path),
                   "registry_sha256": sha256_file(registry_path),
                   "harness_config_sha256": sha256_file(config_path)},
        "provenance": {"script_sha256": sha256_file(Path(__file__).resolve()),
                       "registered_outputs_only": True},
        "acceptance": {"all_registered_spatial_feature_pairs_accounted": True,
                       "eligible_nmi_finite_bounded": all(np.isfinite(row["nmi"]) and 0.0 <= row["nmi"] <= 1.0 for row in measured),
                       "minimum_joint_defined": MIN_OVERLAP,
                       "high_nmi_threshold": NMI_FLAG,
                       "flagged_pairs_explicit": True},
        "interpretation": "Diagnostic NMI flags are for reviewer decisions only; no registered estimator or frozen feature was changed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--registry", default="config/ideas.json")
    parser.add_argument("--harness-report", default="reports/ideas-harness.json")
    parser.add_argument("--out", default="reports/s-family-nonlinear-orthogonality.json")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    registry_path = ROOT / args.registry
    harness_path = ROOT / args.harness_report
    report = build_report(workspace, registry_path, harness_path)
    pair_rows = report.pop("pair_results")
    pair_path = ROOT / "reports/s-family-nonlinear-orthogonality-pairs.csv"
    pair_bytes = serialize_pair_measurements(pair_rows)
    if pair_bytes != serialize_pair_measurements(pair_rows):
        raise ValueError("pair-measurement CSV serialization is not byte deterministic")
    pair_path.write_bytes(pair_bytes)
    report["pair_measurements"] = {"path": str(pair_path.relative_to(ROOT)),
                                   "sha256": hashlib.sha256(pair_bytes).hexdigest(),
                                   "rows": len(pair_rows),
                                   "columns": ["left_feature_index", "right_feature_index", "region_index", "joint_defined", "nmi"],
                                   "feature_indexes": "See feature_catalog; region_index refers to region_catalog."}
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out_path.relative_to(ROOT)),
                      "pair_measurements": str(pair_path.relative_to(ROOT)),
                      "pair_measurements_sha256": report["pair_measurements"]["sha256"],
                      "official_ids": report["scope"]["official_ids"],
                      "registered_features": report["scope"]["registered_feature_emissions"],
                      "candidate_pairs": report["scope"]["candidate_feature_pairs"],
                      "eligible_measurements": report["scope"]["eligible_region_pair_measurements"],
                      "maximum_nmi": report["maximum_nmi"],
                      "flagged_pairs": report["scope"]["flagged_region_pairs_nmi_ge_0_80"],
                      "report_sha256": sha256_file(out_path)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
