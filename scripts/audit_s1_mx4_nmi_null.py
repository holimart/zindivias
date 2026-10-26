#!/usr/bin/env python3
"""Permutation-null and paired-bootstrap validation of the flagged Eastern OK Mx4/S1 NMI pair."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from audit_s_family_nonlinear_orthogonality import encode_feature, normalized_mutual_information  # noqa: E402

REGION = "eastern-ok"
PAIR_LEFT = "Mx4|F|covariate"
PAIR_RIGHT = "S1|F|covariate:lag_mx4_f_covariate"
EXPECTED_RAW_NMI = 0.8201528016239874
MIN_N = 30
PERMUTATIONS = 2000
BOOTSTRAPS = 2000
PERMUTATION_SEED = 20261027
BOOTSTRAP_SEED = 20261028
ALPHA = 0.05


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mutual_information(left: np.ndarray, right: np.ndarray) -> tuple[float, float, float]:
    """Return MI in nats and both marginal entropies for categorical integer vectors."""
    x = np.asarray(left, dtype=np.int64)
    y = np.asarray(right, dtype=np.int64)
    if x.ndim != 1 or y.ndim != 1 or x.shape != y.shape or not x.size:
        raise ValueError("MI requires aligned nonempty one-dimensional category vectors")
    if np.any(x < 0) or np.any(y < 0):
        raise ValueError("MI labels must be nonnegative")
    _, x = np.unique(x, return_inverse=True)
    _, y = np.unique(y, return_inverse=True)
    nx, ny = int(x.max()) + 1, int(y.max()) + 1
    counts = np.bincount(x * ny + y, minlength=nx * ny).reshape(nx, ny).astype(np.float64)
    pxy = counts / x.size
    px, py = pxy.sum(axis=1), pxy.sum(axis=0)
    hx = -float(np.sum(px[px > 0] * np.log(px[px > 0])))
    hy = -float(np.sum(py[py > 0] * np.log(py[py > 0])))
    expected_independent = px[:, None] * py[None, :]
    occupied = pxy > 0
    mi = float(np.sum(pxy[occupied] * np.log(pxy[occupied] / expected_independent[occupied])))
    return mi, hx, hy


def _log_choose(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def expected_mutual_information(left: np.ndarray, right: np.ndarray) -> float:
    """Exact EMI under the fixed-margin hypergeometric permutation model, in nats."""
    x = np.asarray(left, dtype=np.int64)
    y = np.asarray(right, dtype=np.int64)
    if x.ndim != 1 or y.ndim != 1 or x.shape != y.shape or not x.size:
        raise ValueError("EMI requires aligned nonempty one-dimensional category vectors")
    if np.any(x < 0) or np.any(y < 0):
        raise ValueError("EMI labels must be nonnegative")
    _, x = np.unique(x, return_inverse=True)
    _, y = np.unique(y, return_inverse=True)
    rows = np.bincount(x)
    cols = np.bincount(y)
    n_total = int(x.size)
    emi = 0.0
    for row_count in rows:
        for col_count in cols:
            low = max(1, int(row_count + col_count - n_total))
            high = min(int(row_count), int(col_count))
            denominator = _log_choose(n_total, int(col_count))
            for overlap in range(low, high + 1):
                log_probability = (_log_choose(int(row_count), overlap)
                                   + _log_choose(n_total - int(row_count), int(col_count) - overlap)
                                   - denominator)
                probability = math.exp(log_probability)
                emi += probability * (overlap / n_total) * math.log(
                    overlap * n_total / (int(row_count) * int(col_count)))
    if not np.isfinite(emi) or emi < -1e-12:
        raise ValueError("exact expected MI is not finite and nonnegative")
    return max(0.0, float(emi))


def adjusted_mutual_information(left: np.ndarray, right: np.ndarray) -> dict[str, float]:
    mi, hx, hy = mutual_information(left, right)
    emi = expected_mutual_information(left, right)
    denominator = 0.5 * (hx + hy) - emi
    adjusted = 0.0 if denominator <= 1e-15 else (mi - emi) / denominator
    if not np.isfinite(adjusted):
        raise ValueError("adjusted mutual information must be finite")
    return {"mutual_information_nats": mi, "expected_mutual_information_nats": emi,
            "entropy_left_nats": hx, "entropy_right_nats": hy,
            "arithmetic_entropy_mean_minus_expected_mi_nats": denominator,
            "adjusted_mutual_information": float(adjusted)}


def bootstrap_nmi(left: np.ndarray, right: np.ndarray, *, replicates: int,
                  seed: int, confidence: float = 0.95) -> dict[str, Any]:
    x = np.asarray(left, dtype=np.int16)
    y = np.asarray(right, dtype=np.int16)
    if x.ndim != 1 or y.shape != x.shape or not len(x):
        raise ValueError("bootstrap requires aligned nonempty encoded labels")
    rng = np.random.default_rng(seed)
    scores = np.empty(replicates, dtype=np.float64)
    for index in range(replicates):
        take = rng.integers(0, len(x), size=len(x))
        scores[index] = normalized_mutual_information(x[take], y[take])
    tail = (1.0 - confidence) / 2.0
    return {"replicates": replicates, "seed": seed, "confidence_level": confidence,
            "method": "paired tract bootstrap with replacement; percentile interval",
            "raw_nmi_mean": float(scores.mean()),
            "raw_nmi_standard_error": float(scores.std(ddof=1)),
            "raw_nmi_interval": [float(np.quantile(scores, tail)),
                                 float(np.quantile(scores, 1.0 - tail))]}


def permutation_null(left: np.ndarray, right: np.ndarray, *, replicates: int,
                     seed: int) -> dict[str, Any]:
    x = np.asarray(left, dtype=np.int16)
    y = np.asarray(right, dtype=np.int16)
    if x.ndim != 1 or y.shape != x.shape or not len(x):
        raise ValueError("permutation test requires aligned nonempty encoded labels")
    observed = normalized_mutual_information(x, y)
    rng = np.random.default_rng(seed)
    null = np.empty(replicates, dtype=np.float64)
    for index in range(replicates):
        null[index] = normalized_mutual_information(x, rng.permutation(y))
    exceedances = int(np.count_nonzero(null >= observed - 1e-15))
    p_value = (exceedances + 1) / (replicates + 1)
    return {"replicates": replicates, "seed": seed,
            "procedure": "independent uniform permutation of the right feature labels over the pairwise-defined Eastern Oklahoma tracts; left labels and pairwise-defined ID set held fixed",
            "tail": "one-sided upper tail for NMI >= observed",
            "observed_raw_nmi": observed,
            "null_mean_nmi": float(null.mean()), "null_sd_nmi": float(null.std(ddof=1)),
            "null_quantiles_nmi": {str(q): float(np.quantile(null, q)) for q in (0.5, 0.95, 0.99)},
            "exceedances_ge_observed": exceedances,
            "p_value_plus_one": float(p_value),
            "permutation_bias_corrected_nmi_observed_minus_null_mean": float(observed - null.mean())}


def is_confirmed_redundancy(permutation_p: float, adjusted_mi: float) -> bool:
    return permutation_p <= 0.01 and adjusted_mi >= 0.50


def load_one_feature(path: Path, *, idea: str, family: str, component: str,
                     channel: str, official_pairs: list[tuple[str, str]],
                     expected_hash: str) -> tuple[dict[str, float], str]:
    digest = sha256_file(path)
    if digest != expected_hash:
        raise ValueError(f"{idea} Parquet hash mismatch: {digest} != {expected_hash}")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "family", "component",
                                         "channel", "value", "defined"])
    expected = set(official_pairs)
    found: dict[str, tuple[str, float]] = {}
    for row in table.to_pylist():
        if (row["idea"] != idea or row["family"] != family or row["component"] != component
                or row["channel"] != channel):
            continue
        geoid, region = str(row["GEOID"]), str(row["region"])
        if (geoid, region) not in expected or geoid in found:
            raise ValueError(f"{idea}/{channel} has an unexpected or duplicate official row {geoid}")
        value = float(row["value"]) if row["defined"] else float("nan")
        if row["defined"] and not np.isfinite(value):
            raise ValueError(f"{idea}/{channel} has a defined nonfinite value for {geoid}")
        found[geoid] = (region, value)
    if set(found) != {geoid for geoid, _ in official_pairs}:
        raise ValueError(f"{idea}/{channel} does not contain one row per official GEOID")
    if any(found[geoid][0] != region for geoid, region in official_pairs):
        raise ValueError(f"{idea}/{channel} has incorrect official region assignments")
    return {geoid: found[geoid][1] for geoid, _ in official_pairs}, digest


def build_audit(workspace: Path, *, registry_path: Path, harness_path: Path,
                nmi_report_path: Path, output_path: Path) -> dict[str, Any]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    implemented = {entry["id"] for entry in registry["ideas"] if entry.get("status") == "implemented"}
    if not {"Mx4", "S1"}.issubset(implemented):
        raise ValueError("the two target features are not both registered")
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    nmi_report = json.loads(nmi_report_path.read_text(encoding="utf-8"))
    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len({geoid for geoid, _ in official}) != 9379:
        raise ValueError("official GEOID metadata is not an exact 9,379-ID partition")
    official_hash = hashlib.sha256("\n".join(f"{geoid},{region}" for geoid, region in official).encode()).hexdigest()
    if official_hash != nmi_report["inputs"]["official_geoid_region_sha256"]:
        raise ValueError("official GEOID/region metadata hash differs from source NMI audit")
    east_ids = [geoid for geoid, region in official if region == REGION]
    if len(east_ids) != 1192 or len(set(east_ids)) != 1192:
        raise ValueError("Eastern Oklahoma official ID partition is not exactly 1,192 unique GEOIDs")

    feature_meta = {
        "Mx4": {"family": "Mx", "component": "F", "channel": "covariate", "key": PAIR_LEFT},
        "S1": {"family": "S", "component": "F", "channel": "covariate:lag_mx4_f_covariate", "key": PAIR_RIGHT},
    }
    value_arrays: dict[str, np.ndarray] = {}
    artifact_hashes: dict[str, str] = {}
    for idea, meta in feature_meta.items():
        report_entry = nmi_report["inputs"]["idea_artifacts"][idea]
        harness_entry = harness["per_idea"][idea]
        if report_entry["sha256"] != harness_entry["sha256"]:
            raise ValueError(f"{idea} source NMI and frozen harness hashes disagree")
        path = workspace / "ideas" / f"{idea}.parquet"
        values_by_id, digest = load_one_feature(
            path, idea=idea, family=meta["family"], component=meta["component"],
            channel=meta["channel"], official_pairs=official, expected_hash=report_entry["sha256"])
        artifact_hashes[idea] = digest
        value_arrays[meta["key"]] = np.asarray([values_by_id[geoid] for geoid in east_ids], dtype=np.float64)

    x_values = value_arrays[PAIR_LEFT]
    y_values = value_arrays[PAIR_RIGHT]
    x_codes_full, x_encoding = encode_feature(x_values)
    y_codes_full, y_encoding = encode_feature(y_values)
    source_feature_idx = {row["key"]: row["index"] for row in nmi_report["feature_catalog"]}
    source_encoding_map = {(row["feature"], row["region"]): row
                           for row in nmi_report["feature_encodings_by_region"]}
    region_index = next(row["index"] for row in nmi_report["region_catalog"] if row["region"] == REGION)
    source_x_encoding = source_encoding_map[(source_feature_idx[PAIR_LEFT], region_index)]
    source_y_encoding = source_encoding_map[(source_feature_idx[PAIR_RIGHT], region_index)]
    for actual, source in ((x_encoding, source_x_encoding), (y_encoding, source_y_encoding)):
        if (actual["encoding"] != source["encoding"]
                or actual["categories_or_bins"] != source["categories_or_bins"]
                or actual["defined"] != source["defined"]):
            raise ValueError("recomputed feature encoding differs from the original NMI audit")
    if (x_encoding["encoding"] != "empirical_decile_bins"
            or y_encoding["encoding"] != "empirical_decile_bins"
            or x_encoding["categories_or_bins"] != 3
            or y_encoding["categories_or_bins"] != 4):
        raise ValueError("expected original empirical-decile 3x4 pair encoding was not reproduced")

    joint = (x_codes_full >= 0) & (y_codes_full >= 0)
    n = int(joint.sum())
    if n < MIN_N:
        raise ValueError(f"pairwise-defined sample n={n} is below {MIN_N}")
    x, y = x_codes_full[joint].astype(np.int16), y_codes_full[joint].astype(np.int16)
    raw_nmi = normalized_mutual_information(x, y)
    if n != 1192 or not math.isclose(raw_nmi, EXPECTED_RAW_NMI, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"source raw NMI not reproduced: n={n}, NMI={raw_nmi:.16g}")
    source_pair = next((row for row in nmi_report["flagged_pairs_ge_0_80"]
                        if row["left"] == PAIR_LEFT and row["right"] == PAIR_RIGHT
                        and row["region"] == REGION), None)
    if source_pair is None or source_pair["joint_defined"] != n or not math.isclose(
            source_pair["nmi"], raw_nmi, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("raw NMI differs from the flagged source report")

    ami = adjusted_mutual_information(x, y)
    permutation = permutation_null(x, y, replicates=PERMUTATIONS, seed=PERMUTATION_SEED)
    bootstrap = bootstrap_nmi(x, y, replicates=BOOTSTRAPS, seed=BOOTSTRAP_SEED, confidence=0.95)
    confirmed = is_confirmed_redundancy(permutation["p_value_plus_one"],
                                        ami["adjusted_mutual_information"])
    report = {
        "schema_version": 1,
        "audit": "Eastern Oklahoma Mx4 F versus S1 lagged Mx4 F: seeded permutation null for flagged empirical-decile NMI",
        "scope": {"region": REGION, "official_region_ids": 1192,
                  "joint_defined_pairwise_ids": n, "pairwise_missing_count": 1192 - n,
                  "exact_official_id_reconciliation": True,
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_reference_data_read": False,
                  "external_data_read": False, "leaderboard_feedback_used": False,
                  "candidate_or_registry_changed": False, "uploads": 0},
        "pair": {"left_feature": PAIR_LEFT, "right_feature": PAIR_RIGHT,
                 "left_encoding": x_encoding, "right_encoding": y_encoding,
                 "pairwise_definedness": "The original per-feature empirical-decile codes are computed over all finite values within Eastern Oklahoma; NMI/permutation/bootstrap then use only IDs where both feature values are defined. Every permutation shuffles one encoded feature only over that fixed paired-ID set.",
                 "raw_nmi": raw_nmi, "nmi_normalization": "same as source audit: 2*MI/(H(left)+H(right)); natural-log entropy"},
        "adjusted_mutual_information": {**ami,
                 "definition": "Exact expected mutual information under the fixed-margin hypergeometric permutation model; AMI=(MI-EMI)/(arithmetic mean of marginal entropies-EMI), in natural-log units."},
        "permutation_test": permutation,
        "paired_tract_bootstrap": bootstrap,
        "decision": {"confirmed_redundancy": confirmed,
                     "rule": "confirmed only if one-sided permutation p<=0.01 and adjusted MI>=0.50; otherwise unconfirmed warning",
                     "permutation_p_at_most_0_01": permutation["p_value_plus_one"] <= 0.01,
                     "adjusted_mi_at_least_0_50": ami["adjusted_mutual_information"] >= 0.50,
                     "interpretation": "Confirmed nonlinear redundancy under the registered diagnostic rule." if confirmed
                     else "Retain the original high-NMI flag as an unconfirmed warning; do not change either feature or registry."},
        "inputs": {"registered_feature_artifacts": {
                        idea: {"path": str(workspace / "ideas" / f"{idea}.parquet"),
                               "sha256": artifact_hashes[idea],
                               "registered_harness_sha256": harness["per_idea"][idea]["sha256"]}
                        for idea in ("Mx4", "S1")},
                    "official_geoid_region_sha256": official_hash,
                    "source_nmi_report_sha256": sha256_file(nmi_report_path),
                    "harness_report_sha256": sha256_file(harness_path),
                    "registry_sha256": sha256_file(registry_path)},
        "provenance": {"script_sha256": sha256_file(Path(__file__).resolve()),
                       "random_generator": "numpy.random.default_rng / PCG64",
                       "permutations": PERMUTATIONS, "permutation_seed": PERMUTATION_SEED,
                       "bootstrap_replicates": BOOTSTRAPS, "bootstrap_seed": BOOTSTRAP_SEED},
        "interpretation": "Bounded label-free diagnostic only. No feature or registry decision is made by this screen; any confirmed flag is for reviewer interpretation, not automatic removal or promotion.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--registry", default="config/ideas.json")
    parser.add_argument("--harness-report", default="reports/ideas-harness.json")
    parser.add_argument("--nmi-report", default="reports/s-family-nonlinear-orthogonality.json")
    parser.add_argument("--out", default="reports/s1-mx4-nmi-null.json")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE is required")
    report = build_audit(workspace, registry_path=ROOT / args.registry,
                         harness_path=ROOT / args.harness_report,
                         nmi_report_path=ROOT / args.nmi_report,
                         output_path=ROOT / args.out)
    print(json.dumps({"raw_nmi": report["pair"]["raw_nmi"],
                      "adjusted_mi": report["adjusted_mutual_information"]["adjusted_mutual_information"],
                      "permutation_p": report["permutation_test"]["p_value_plus_one"],
                      "bootstrap_95_interval": report["paired_tract_bootstrap"]["raw_nmi_interval"],
                      "confirmed_redundancy": report["decision"]["confirmed_redundancy"],
                      "report": args.out}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
