#!/usr/bin/env python3
"""Region-stratified, label-free nonlinear redundancy screen for policy-cleared Tm features."""
from __future__ import annotations

import argparse
import hashlib
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
TEMPORAL_IDEAS = frozenset({"Tm2", "Tm3", "Tm5"})
POLICY_PENDING_IDEAS = frozenset({"Tm1", "Tm4"})
MIN_OVERLAP = 30
NMI_FLAG = 0.80


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def encode_feature(values: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Keep small integer categories intact; otherwise use within-region empirical deciles."""
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
        method, count = "category_integer_low_cardinality", int(unique.size)
    else:
        quantiles = np.quantile(finite, np.arange(0.1, 1.0, 0.1), method="linear")
        codes[finite_mask] = np.searchsorted(quantiles, finite, side="right").astype(np.int16)
        method, count = "empirical_decile_bins", int(np.unique(codes[finite_mask]).size)
    return codes, {"encoding": method, "categories_or_bins": count, "defined": int(finite.size)}


def normalized_mutual_information(left: np.ndarray, right: np.ndarray) -> float:
    """Symmetric arithmetic NMI 2*MI/(H(X)+H(Y)); zero-entropy pairs map to zero."""
    x = np.asarray(left, dtype=np.int64)
    y = np.asarray(right, dtype=np.int64)
    if x.shape != y.shape or x.ndim != 1 or not x.size or np.any(x < 0) or np.any(y < 0):
        raise ValueError("NMI needs aligned, nonempty, nonnegative category codes")
    _, x = np.unique(x, return_inverse=True)
    _, y = np.unique(y, return_inverse=True)
    nx, ny = int(x.max()) + 1, int(y.max()) + 1
    joint_counts = np.bincount(x * ny + y, minlength=nx * ny).reshape(nx, ny).astype(np.float64)
    joint = joint_counts / float(x.size)
    px, py = joint.sum(axis=1), joint.sum(axis=0)
    hx = -float(np.sum(px[px > 0] * np.log(px[px > 0])))
    hy = -float(np.sum(py[py > 0] * np.log(py[py > 0])))
    if hx + hy <= 0.0:
        return 0.0
    independent = px[:, None] * py[None, :]
    occupied = joint > 0
    mutual_information = float(np.sum(joint[occupied] * np.log(joint[occupied] / independent[occupied])))
    value = 2.0 * mutual_information / (hx + hy)
    if not np.isfinite(value):
        raise ValueError("NMI must be finite")
    return float(np.clip(value, 0.0, 1.0))


def measure_pair_nmi(left_codes: np.ndarray, right_codes: np.ndarray,
                     minimum_overlap: int = MIN_OVERLAP) -> dict[str, Any]:
    left_codes = np.asarray(left_codes, dtype=np.int16)
    right_codes = np.asarray(right_codes, dtype=np.int16)
    if left_codes.shape != right_codes.shape or left_codes.ndim != 1:
        raise ValueError("pair encodings must be aligned vectors")
    joint_defined = (left_codes >= 0) & (right_codes >= 0)
    count = int(joint_defined.sum())
    if count < minimum_overlap:
        return {"status": "excluded_below_minimum_overlap", "joint_defined": count, "nmi": None}
    value = normalized_mutual_information(left_codes[joint_defined], right_codes[joint_defined])
    return {"status": "measured", "joint_defined": count, "nmi": value}


def candidate_pairs(feature_keys: list[str], metadata: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
    """Unique cross-idea pairs with at least one Tm2/Tm3/Tm5 emission."""
    temporal = [key for key in feature_keys if metadata[key]["idea"] in TEMPORAL_IDEAS]
    pairs: set[tuple[str, str]] = set()
    for temporal_key in temporal:
        for other_key in feature_keys:
            if metadata[temporal_key]["idea"] == metadata[other_key]["idea"]:
                continue
            pairs.add(tuple(sorted((temporal_key, other_key))))
    return sorted(pairs)


def _load_policy_cleared_ids(registry_path: Path, status_path: Path,
                             harness_path: Path) -> tuple[list[str], dict[str, Any]]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    policy = status.get("ideas", {})
    for idea in POLICY_PENDING_IDEAS:
        if policy.get(idea, {}).get("status") != "pending_policy_review":
            raise ValueError(f"policy guard: {idea} must remain pending_policy_review")
    registered = {item["id"] for item in registry["ideas"] if item.get("status") == "implemented"}
    # The frozen harness can retain a historical Tm1 record, but the audit never opens its file.
    pending = {idea for idea, item in policy.items() if item.get("status") == "pending_policy_review"}
    if not POLICY_PENDING_IDEAS.issubset(pending):
        raise ValueError("pending-policy status does not include Tm1 and Tm4")
    selected = sorted(registered - pending)
    if not TEMPORAL_IDEAS.issubset(selected):
        raise ValueError("Tm2, Tm3, and Tm5 must all be policy-cleared registered ideas")
    harness_ids = set(harness.get("per_idea", {}))
    if harness_ids - registered or set(selected) - harness_ids:
        raise ValueError("registry and harness idea manifests are inconsistent")
    return selected, {"registered_idea_ids": sorted(registered), "pending_policy_ids": sorted(pending),
                      "selected_idea_ids": selected, "harness_idea_ids": sorted(harness_ids),
                      "harness": harness}


def load_registered_features(workspace: Path, registry_path: Path, status_path: Path,
                             harness_path: Path) -> tuple[dict[str, dict[str, np.ndarray]],
                                                         dict[str, dict[str, str]], dict[str, Any],
                                                         list[tuple[str, str]], dict[str, list[str]]]:
    idea_ids, manifests = _load_policy_cleared_ids(registry_path, status_path, harness_path)
    harness = manifests["harness"]
    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len({geoid for geoid, _ in official}) != 9379:
        raise ValueError("official scored GEOIDs must be an exact 9,379-ID partition")
    geoids_by_region = {region: [geoid for geoid, label in official if label == region]
                        for region in REGIONS}
    if sum(map(len, geoids_by_region.values())) != 9379 or any(not geoids_by_region[r] for r in REGIONS):
        raise ValueError("official region/GEOID partition is incomplete")
    positions = {region: {geoid: i for i, geoid in enumerate(geoids_by_region[region])}
                 for region in REGIONS}
    values: dict[str, dict[str, np.ndarray]] = {}
    metadata: dict[str, dict[str, str]] = {}
    artifact_manifest: dict[str, Any] = {}
    for idea_id in idea_ids:
        entry = harness["per_idea"][idea_id]
        if not entry.get("geoid_partition_exact") or entry.get("forbidden_source_hits"):
            raise ValueError(f"harness does not certify clean exact-ID output for {idea_id}")
        artifact_path = workspace / "ideas" / f"{idea_id}.parquet"
        # Pending-policy idea IDs were filtered before any artifact path is opened or hashed.
        if not artifact_path.is_file():
            raise FileNotFoundError(artifact_path)
        digest = sha256_file(artifact_path)
        if digest != entry["sha256"]:
            raise ValueError(f"{idea_id}: artifact digest differs from the frozen harness")
        table = pq.read_table(artifact_path, columns=["GEOID", "region", "idea", "family",
                                                     "component", "channel", "value", "defined"])
        arrays: dict[tuple[str, str], dict[str, np.ndarray]] = {}
        seen: dict[tuple[str, str], dict[str, set[str]]] = {}
        rows = table.to_pylist()
        for row in rows:
            region, geoid = str(row["region"]), str(row["GEOID"])
            if region not in positions or geoid not in positions[region] or row["idea"] != idea_id:
                raise ValueError(f"{idea_id}: row outside official region/GEOID scope")
            emission_key = (str(row["component"]), str(row["channel"]))
            if emission_key not in arrays:
                arrays[emission_key] = {r: np.full(len(geoids_by_region[r]), np.nan, dtype=np.float64)
                                        for r in REGIONS}
                seen[emission_key] = {r: set() for r in REGIONS}
            if geoid in seen[emission_key][region]:
                raise ValueError(f"{idea_id}/{emission_key}: duplicate {region}/{geoid}")
            seen[emission_key][region].add(geoid)
            if row["defined"]:
                value = float(row["value"])
                if not np.isfinite(value):
                    raise ValueError(f"{idea_id}/{emission_key}: defined feature is nonfinite")
                arrays[emission_key][region][positions[region][geoid]] = value
        for emission_key, region_values in arrays.items():
            for region in REGIONS:
                if seen[emission_key][region] != set(geoids_by_region[region]):
                    raise ValueError(f"{idea_id}/{emission_key}/{region}: GEOID coverage is incomplete")
            feature_key = f"{idea_id}|{emission_key[0]}|{emission_key[1]}"
            if feature_key in values:
                raise ValueError(f"duplicate registered feature key: {feature_key}")
            values[feature_key] = region_values
            metadata[feature_key] = {"idea": idea_id, "family": str(rows[0]["family"]),
                                     "component": emission_key[0], "channel": emission_key[1]}
        artifact_manifest[idea_id] = {
            "path": str(artifact_path), "sha256": digest, "rows": table.num_rows,
            "feature_emissions": len(arrays),
            "defined_ids_per_emission_by_region": {
                f"{key[0]}|{key[1]}": {region: int(np.isfinite(region_values[region]).sum())
                                         for region in REGIONS}
                for key, region_values in arrays.items()},
        }
    return values, metadata, artifact_manifest, official, geoids_by_region


def build_report(workspace: Path, registry_path: Path, status_path: Path,
                 harness_path: Path) -> dict[str, Any]:
    values, metadata, artifact_manifest, official, geoids_by_region = load_registered_features(
        workspace, registry_path, status_path, harness_path)
    feature_keys = sorted(values)
    temporal_features = [key for key in feature_keys if metadata[key]["idea"] in TEMPORAL_IDEAS]
    if not temporal_features:
        raise ValueError("no registered policy-cleared temporal feature emissions found")
    pairs = candidate_pairs(feature_keys, metadata)
    encodings: dict[str, dict[str, np.ndarray]] = {}
    encoding_metadata: dict[str, dict[str, dict[str, Any]]] = {}
    for key in feature_keys:
        encodings[key], encoding_metadata[key] = {}, {}
        for region in REGIONS:
            encodings[key][region], encoding_metadata[key][region] = encode_feature(values[key][region])

    measurements: list[dict[str, Any]] = []
    per_region = {region: {"candidate_pairs": len(pairs), "eligible": 0, "excluded_below_30": 0}
                  for region in REGIONS}
    max_global = -1.0
    for left, right in pairs:
        pair_regions = {}
        pair_max: float | None = None
        for region in REGIONS:
            result = measure_pair_nmi(encodings[left][region], encodings[right][region])
            pair_regions[region] = result
            if result["status"] == "measured":
                value = float(result["nmi"])
                if not np.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise ValueError("measured NMI escaped finite [0,1] bounds")
                per_region[region]["eligible"] += 1
                pair_max = value if pair_max is None else max(pair_max, value)
                max_global = max(max_global, value)
            else:
                per_region[region]["excluded_below_30"] += 1
        temporal_side = left if metadata[left]["idea"] in TEMPORAL_IDEAS else right
        peer_side = right if temporal_side == left else left
        measurements.append({"temporal_feature": temporal_side, "peer_feature": peer_side,
                             "max_region_nmi": pair_max, "regions": pair_regions})
    if sum(row["eligible"] + row["excluded_below_30"] for row in per_region.values()) \
            != len(pairs) * len(REGIONS):
        raise AssertionError("candidate pair accounting is incomplete")
    flagged = [row for row in measurements if row["max_region_nmi"] is not None
               and row["max_region_nmi"] > NMI_FLAG]
    high_pairs = sorted(flagged, key=lambda row: (-row["max_region_nmi"],
                                                  row["temporal_feature"], row["peer_feature"]))
    maximum_pair = max((row for row in measurements if row["max_region_nmi"] is not None),
                       key=lambda row: (row["max_region_nmi"], row["temporal_feature"],
                                        row["peer_feature"]), default=None)
    if maximum_pair is None:
        maximum_nmi_pair = None
    else:
        maximum_region = max(REGIONS, key=lambda region: (
            maximum_pair["regions"][region]["nmi"]
            if maximum_pair["regions"][region]["status"] == "measured" else -1.0,
            -REGIONS.index(region)))
        maximum_nmi_pair = {"temporal_feature": maximum_pair["temporal_feature"],
                            "peer_feature": maximum_pair["peer_feature"],
                            "region": maximum_region,
                            "joint_defined": maximum_pair["regions"][maximum_region]["joint_defined"],
                            "nmi": maximum_pair["regions"][maximum_region]["nmi"]}
    nmi_measurements = sum(row["eligible"] for row in per_region.values())
    policy_ids, policy_manifest = _load_policy_cleared_ids(registry_path, status_path, harness_path)
    harness = policy_manifest["harness"]
    official_digest = hashlib.sha256("\n".join(
        f"{geoid},{region}" for geoid, region in official).encode("utf-8")).hexdigest()
    nmi_values = [region_result["nmi"] for pair in measurements
                  for region_result in pair["regions"].values()
                  if region_result["status"] == "measured"]
    report = {
        "schema_version": 1,
        "audit": "Region-stratified nonlinear mutual-information redundancy screen for Tm2/Tm3/Tm5; diagnostic only",
        "method": {
            "candidate_scope": "Each Tm2, Tm3, and Tm5 emission is compared with every registered emission from a different policy-cleared idea; each unordered cross-idea pair is measured once. This includes cross-pairs among Tm2/Tm3/Tm5; same-idea emissions are not paired.",
            "continuous_binning": "For each feature separately, calculate deterministic empirical deciles within each region from that feature's own defined values (NumPy linear quantiles at 0.1..0.9); assign with searchsorted(side=right). Ties may collapse bins.",
            "categorical_encoding": "Integer-valued features with at most 10 unique values are retained as categorical levels; other features use the within-region empirical-decile encoding.",
            "pairwise_definedness": "Only jointly defined finite values are used for each pair in each region; encodings/decile cutpoints are computed before pairwise masking.",
            "minimum_joint_defined": MIN_OVERLAP,
            "nmi": "Symmetric arithmetic normalization 2*MI/(H(X)+H(Y)) with natural logarithms; zero-entropy pairs map to 0. NMI is calculated only at joint count >=30 and lies in [0,1].",
            "flag_rule": f"Flag a feature pair if its maximum region NMI is > {NMI_FLAG}; the threshold is strictly greater-than.",
        },
        "scope": {
            "official_ids": len(official), "exact_9379_id_partition": len(official) == 9379,
            "official_ids_by_region": {region: len(geoids_by_region[region]) for region in REGIONS},
            "registered_policy_cleared_ideas": len(policy_ids),
            "registered_feature_emissions": len(feature_keys),
            "temporal_candidate_ideas": sorted(TEMPORAL_IDEAS),
            "temporal_candidate_emissions": len(temporal_features),
            "candidate_cross_idea_feature_pairs": len(pairs),
            "eligible_region_pair_measurements": nmi_measurements,
            "flagged_feature_pairs_max_region_nmi_gt_0_80": len(flagged),
            "labels_read": False, "sample_score_values_read": False,
            "Tm1_or_Tm4_artifact_inputs_read": False,
            "removed_reference_or_external_layers_read": False,
            "features_or_registry_changed": False, "uploads": 0,
        },
        "pair_accounting": {"by_region": per_region,
                            "complete": sum(row["eligible"] + row["excluded_below_30"]
                                            for row in per_region.values()) == len(pairs) * len(REGIONS)},
        "maximum_nmi": max_global if max_global >= 0.0 else None,
        "maximum_nmi_pair": maximum_nmi_pair,
        "nmi_measurement_distribution": {
            "n": len(nmi_values), "min": float(min(nmi_values)) if nmi_values else None,
            "p50": float(np.quantile(nmi_values, .5)) if nmi_values else None,
            "p95": float(np.quantile(nmi_values, .95)) if nmi_values else None,
            "max": float(max(nmi_values)) if nmi_values else None,
        },
        "flagged_pairs_gt_0_80": high_pairs,
        "pair_results": measurements,
        "provenance": {
            "official_geoid_region_sha256": official_digest,
            "idea_artifacts": artifact_manifest,
            "registry_sha256": sha256_file(registry_path),
            "tm_policy_status_sha256": sha256_file(status_path),
            "harness_report_sha256": sha256_file(harness_path),
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "excluded_policy_pending_ideas": sorted(POLICY_PENDING_IDEAS),
            "registered_outputs_only": True,
        },
        "measure_before": {"registered_Tm2_Tm3_Tm5_outputs_available": True,
                           "previous_tm_summary_metric": "Pearson/VIF only; no NMI reported",
                           "previous_Tm_family_report": "reports/bias60-family-tm-summary.json"},
        "acceptance": {
            "exact_9379_id_alignment": len(official) == 9379 and sum(map(len, geoids_by_region.values())) == 9379,
            "all_candidate_feature_pairs_accounted": sum(row["eligible"] + row["excluded_below_30"]
                                                          for row in per_region.values()) == len(pairs) * len(REGIONS),
            "all_measured_nmi_finite_and_bounded": all(np.isfinite(value) and 0.0 <= value <= 1.0
                                                       for value in nmi_values),
            "no_nmi_above_0_8": not flagged,
            "maximum_within_region_nmi": max_global if max_global >= 0 else None,
            "threshold": NMI_FLAG,
            "interpretation": "No region-stratified nonlinear redundancy flag exceeded NMI 0.8; retain all existing estimators."
            if not flagged else "At least one temporal feature pair exceeded NMI 0.8; flag for merge/review only, with no silent deletion or integration.",
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--registry", default="config/ideas.json")
    parser.add_argument("--tm-status", default="config/bias60-family-tm-status.json")
    parser.add_argument("--harness-report", default="reports/ideas-harness.json")
    parser.add_argument("--out", default="reports/tm-family-nonlinear-orthogonality.json")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    registry_path, status_path, harness_path = (ROOT / args.registry, ROOT / args.tm_status,
                                                ROOT / args.harness_report)
    report = build_report(workspace, registry_path, status_path, harness_path)
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if serialized != json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n":
        raise ValueError("report serialization is not deterministic")
    out_path.write_text(serialized, encoding="utf-8")
    print(json.dumps({"report": str(out_path.relative_to(ROOT)),
                      "report_sha256": sha256_file(out_path),
                      "official_ids": report["scope"]["official_ids"],
                      "registered_features": report["scope"]["registered_feature_emissions"],
                      "temporal_candidate_features": report["scope"]["temporal_candidate_emissions"],
                      "candidate_pairs": report["scope"]["candidate_cross_idea_feature_pairs"],
                      "eligible_region_measurements": report["scope"]["eligible_region_pair_measurements"],
                      "maximum_nmi": report["maximum_nmi"],
                      "flagged_pairs": report["scope"]["flagged_feature_pairs_max_region_nmi_gt_0_80"]},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
