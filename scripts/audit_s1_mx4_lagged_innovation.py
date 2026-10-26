#!/usr/bin/env python3
"""Screen label-free innovation in S1's lagged Mx4 F channel, without registration."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from audit_s_family_nonlinear_orthogonality import encode_feature, normalized_mutual_information  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
N_BINS = 10
MIN_COVERAGE = 0.9859
MIN_REMAINING_VARIANCE_RATIO = 0.10
MAX_NMI = 0.80
MIN_NMI_OVERLAP = 30
CURRENT = ("Mx4", "Mx", "F", "covariate")
LAGGED = ("S1", "S", "F", "covariate:lag_mx4_f_covariate")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_feature(geoids: list[str], regions: list[str], values: np.ndarray,
                   defined: np.ndarray) -> str:
    """Canonical hash of just the permitted feature emission, not unrelated S1 channels."""
    lines = []
    for geoid, region, value, is_defined in zip(geoids, regions, values, defined):
        lines.append(f"{geoid},{region},{int(bool(is_defined))}," +
                     (format(float(value), ".17g") if is_defined else "NULL"))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def decile_codes(values: np.ndarray, defined: np.ndarray, bins: int = N_BINS) -> tuple[np.ndarray, np.ndarray]:
    """Apply deterministic empirical quantile bins; ties share a bin, possibly yielding <10 bins."""
    x = np.asarray(values, dtype=np.float64)
    mask = np.asarray(defined, dtype=bool)
    if x.ndim != 1 or mask.shape != x.shape:
        raise ValueError("decile inputs must be aligned vectors")
    if bins < 2 or np.any(mask & ~np.isfinite(x)):
        raise ValueError("decile binning requires finite defined values and at least two bins")
    output = np.full(x.shape, -1, dtype=np.int16)
    finite = x[mask]
    if not finite.size:
        return output, np.array([], dtype=np.float64)
    edges = np.quantile(finite, np.arange(0.1, 1.0, 0.1), method="linear")
    if bins != 10:
        edges = np.quantile(finite, np.arange(1, bins, dtype=np.float64) / bins, method="linear")
    output[mask] = np.searchsorted(edges, finite, side="right").astype(np.int16)
    return output, edges


def leave_one_out_bin_mean_residual(geoids: list[str], current: np.ndarray,
                                    current_defined: np.ndarray, lagged: np.ndarray,
                                    lagged_defined: np.ndarray, *, bins: int = N_BINS
                                    ) -> dict[str, Any]:
    """Subtract same-region, same-current-Mx4-decile S1 mean excluding each focal GEOID."""
    n = len(geoids)
    x = np.asarray(current, dtype=np.float64)
    y = np.asarray(lagged, dtype=np.float64)
    x_mask = np.asarray(current_defined, dtype=bool)
    y_mask = np.asarray(lagged_defined, dtype=bool)
    if len(set(geoids)) != n or any(a.shape != (n,) for a in (x, y, x_mask, y_mask)):
        raise ValueError("GEOID, feature values, and defined masks must align uniquely")
    if np.any(x_mask & ~np.isfinite(x)) or np.any(y_mask & ~np.isfinite(y)):
        raise ValueError("defined feature values must be finite")
    bin_ids, edges = decile_codes(x, x_mask, bins=bins)
    pairwise = x_mask & y_mask
    residual = np.full(n, np.nan, dtype=np.float64)
    peer_mean = np.full(n, np.nan, dtype=np.float64)
    peer_count = np.zeros(n, dtype=np.int32)
    residual_defined = np.zeros(n, dtype=bool)
    for bin_id in np.unique(bin_ids[pairwise]):
        members = np.flatnonzero(pairwise & (bin_ids == bin_id))
        if members.size <= 1:
            continue
        total = float(np.sum(y[members], dtype=np.float64))
        for index in members:
            count = int(members.size - 1)
            mean = (total - float(y[index])) / count
            peer_mean[index] = mean
            peer_count[index] = count
            residual[index] = float(y[index]) - mean
            residual_defined[index] = True
    if np.any(residual_defined & ~pairwise) or np.any(~residual_defined & np.isfinite(residual)):
        raise AssertionError("innovation must preserve pairwise definedness and nulls")
    if np.any(residual_defined & (peer_count < 1)):
        raise AssertionError("leave-one-out residual requires at least one peer")
    return {"innovation": residual, "defined": residual_defined,
            "pairwise_defined": pairwise, "current_mx4_decile": bin_ids,
            "peer_bin_mean": peer_mean, "peer_count": peer_count,
            "decile_edges": [float(edge) for edge in edges]}


def measure_region_nmi(current: np.ndarray, current_defined: np.ndarray,
                       innovation: np.ndarray, innovation_defined: np.ndarray) -> dict[str, Any]:
    xcodes, xmeta = encode_feature(np.where(current_defined, current, np.nan))
    rcodes, rmeta = encode_feature(np.where(innovation_defined, innovation, np.nan))
    joint = (xcodes >= 0) & (rcodes >= 0)
    count = int(joint.sum())
    if count < MIN_NMI_OVERLAP:
        return {"status": "excluded_below_minimum_overlap", "joint_defined": count,
                "nmi": None, "current_encoding": xmeta, "innovation_encoding": rmeta}
    score = normalized_mutual_information(xcodes[joint], rcodes[joint])
    return {"status": "measured", "joint_defined": count, "nmi": float(score),
            "current_encoding": xmeta, "innovation_encoding": rmeta}


def distribution(values: np.ndarray, defined: np.ndarray) -> dict[str, float | int | None]:
    x = np.asarray(values, dtype=np.float64)[np.asarray(defined, dtype=bool)]
    if not x.size:
        return {"n": 0, "min": None, "p05": None, "p50": None, "p95": None,
                "max": None, "mean": None, "variance": None}
    if not np.isfinite(x).all():
        raise ValueError("defined distribution values must be finite")
    return {"n": int(x.size), "min": float(x.min()), "p05": float(np.quantile(x, .05)),
            "p50": float(np.quantile(x, .5)), "p95": float(np.quantile(x, .95)),
            "max": float(x.max()), "mean": float(x.mean()), "variance": float(x.var(ddof=0))}


def output_table(geoids: list[str], regions: list[str], current: np.ndarray,
                 current_defined: np.ndarray, lagged: np.ndarray, lagged_defined: np.ndarray,
                 result: dict[str, Any]) -> pa.Table:
    n = len(geoids)
    if len(regions) != n or any(np.asarray(result[key]).shape != (n,) for key in
                                ("innovation", "defined", "pairwise_defined", "current_mx4_decile",
                                 "peer_bin_mean", "peer_count")):
        raise ValueError("output feature arrays must align with exact official GEOIDs")
    residual = np.asarray(result["innovation"], dtype=np.float64)
    defined = np.asarray(result["defined"], dtype=bool)
    if np.any(defined & (~np.isfinite(residual) | ~np.asarray(result["pairwise_defined"], bool))):
        raise ValueError("defined innovation must have finite residual and pairwise-defined source features")
    if np.any(~defined & np.isfinite(residual)):
        raise ValueError("undefined innovation must stay null")
    return pa.table({
        "GEOID": pa.array(geoids, type=pa.string()),
        "region": pa.array(regions, type=pa.string()),
        "Mx4_F": pa.array([float(x) if ok else None for x, ok in zip(current, current_defined)], type=pa.float64()),
        "Mx4_F_defined": pa.array(current_defined, type=pa.bool_()),
        "S1_lagged_Mx4_F": pa.array([float(x) if ok else None for x, ok in zip(lagged, lagged_defined)], type=pa.float64()),
        "S1_lagged_Mx4_F_defined": pa.array(lagged_defined, type=pa.bool_()),
        "Mx4_empirical_decile": pa.array(result["current_mx4_decile"], type=pa.int16()),
        "peer_bin_mean_S1_lagged_Mx4_F": pa.array([float(x) if ok else None
                                                      for x, ok in zip(result["peer_bin_mean"], defined)], type=pa.float64()),
        "peer_count": pa.array(result["peer_count"], type=pa.int32()),
        "innovation": pa.array([float(x) if ok else None for x, ok in zip(residual, defined)], type=pa.float64()),
        "innovation_defined": pa.array(defined, type=pa.bool_()),
    })


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def read_feature(path: Path, idea: str, family: str, component: str, channel: str,
                 official: list[tuple[str, str]], expected_sha256: str
                 ) -> tuple[np.ndarray, np.ndarray, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    file_sha = sha256_file(path)
    if file_sha != expected_sha256:
        raise ValueError(f"{idea} frozen artifact SHA differs from harness")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "family", "component",
                                         "channel", "value", "defined"],
                          filters=[("idea", "=", idea), ("component", "=", component),
                                   ("channel", "=", channel)])
    if table.num_rows != len(official):
        raise ValueError(f"{idea} selected emission must have one row per official GEOID")
    index_by_pair = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in index_by_pair or pair in seen:
            raise ValueError(f"unexpected or duplicate official row in {idea} emission: {pair}")
        if (row["idea"], row["family"], row["component"], row["channel"]) != (
                idea, family, component, channel):
            raise ValueError("selected feature metadata differs from requested emission")
        seen.add(pair)
        if bool(row["defined"]):
            value = float(row["value"])
            if not np.isfinite(value):
                raise ValueError(f"defined {idea} value is nonfinite for {pair[0]}")
            values[index_by_pair[pair]] = value
            defined[index_by_pair[pair]] = True
    if seen != set(official):
        raise ValueError(f"{idea} selected emission does not exactly reconcile to official GEOIDs")
    return values, defined, file_sha


def audit(workspace: Path, output_path: Path, residual_artifact_path: Path) -> dict[str, Any]:
    registry_path = ROOT / "config/ideas.json"
    harness_path = ROOT / "reports/ideas-harness.json"
    source_nmi_path = ROOT / "reports/s1-mx4-nmi-null.json"
    registry_sha_before = sha256_file(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    implemented = {entry["id"] for entry in registry["ideas"] if entry.get("status") == "implemented"}
    if not {"Mx4", "S1"}.issubset(implemented):
        raise ValueError("Mx4 and S1 must remain registered input ideas")
    if any(entry.get("id") == "S1-Mx4-innovation" for entry in registry["ideas"]):
        raise ValueError("innovation must remain a separate unregistered diagnostic")
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    source_nmi = json.loads(source_nmi_path.read_text(encoding="utf-8"))
    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len({g for g, _ in official}) != 9379 or len(set(official)) != 9379:
        raise ValueError("official GEOID/region list must be an exact 9,379-ID partition")
    geoids = [geoid for geoid, _ in official]
    regions = [region for _, region in official]
    if set(regions) != set(REGIONS):
        raise ValueError("official partition has unexpected or missing study region")
    ordered_by_region = {region: [g for g, r in official if r == region] for region in REGIONS}
    if sum(map(len, ordered_by_region.values())) != len(official):
        raise ValueError("region-specific GEOID partitions do not reconcile")

    channels = {"Mx4": CURRENT, "S1": LAGGED}
    values: dict[str, np.ndarray] = {}
    defined: dict[str, np.ndarray] = {}
    artifact_hashes: dict[str, str] = {}
    feature_hashes: dict[str, str] = {}
    for idea, (idea_id, family, component, channel) in channels.items():
        frozen_entry = harness.get("per_idea", {}).get(idea)
        if not frozen_entry or not frozen_entry.get("geoid_partition_exact") or frozen_entry.get("forbidden_source_hits"):
            raise ValueError(f"harness does not certify clean exact-ID output for {idea}")
        if not harness["per_idea"][idea].get("sha256"):
            raise ValueError(f"missing frozen artifact hash for {idea}")
        path = workspace / "ideas" / f"{idea}.parquet"
        vals, mask, artifact_sha = read_feature(path, idea_id, family, component, channel,
                                                official, frozen_entry["sha256"])
        values[idea], defined[idea], artifact_hashes[idea] = vals, mask, artifact_sha
        feature_hashes[idea] = digest_feature(geoids, regions, vals, mask)
    current, current_mask = values["Mx4"], defined["Mx4"]
    lagged, lagged_mask = values["S1"], defined["S1"]
    innovation = np.full(len(official), np.nan, dtype=np.float64)
    innovation_defined = np.zeros(len(official), dtype=bool)
    pairwise_defined = current_mask & lagged_mask
    bin_values = np.full(len(official), -1, dtype=np.int16)
    peer_means = np.full(len(official), np.nan, dtype=np.float64)
    peer_counts = np.zeros(len(official), dtype=np.int32)
    region_reports: dict[str, Any] = {}
    for region in REGIONS:
        indices = np.flatnonzero(np.asarray(regions) == region)
        ids = [geoids[i] for i in indices]
        local_result = leave_one_out_bin_mean_residual(
            ids, current[indices], current_mask[indices], lagged[indices], lagged_mask[indices], bins=N_BINS)
        innovation[indices] = local_result["innovation"]
        innovation_defined[indices] = local_result["defined"]
        pairwise_defined[indices] = local_result["pairwise_defined"]
        bin_values[indices] = local_result["current_mx4_decile"]
        peer_means[indices] = local_result["peer_bin_mean"]
        peer_counts[indices] = local_result["peer_count"]
        n_out = int(local_result["defined"].sum())
        n_pair = int(local_result["pairwise_defined"].sum())
        nmi_stats = measure_region_nmi(current[indices], current_mask[indices],
                                       local_result["innovation"], local_result["defined"])
        if nmi_stats["nmi"] is None:
            raise ValueError(f"{region}: NMI could not be measured on sufficient overlap")
        s1_on_output = lagged[indices][local_result["defined"]]
        residual_on_output = local_result["innovation"][local_result["defined"]]
        s1_variance = float(s1_on_output.var(ddof=0))
        residual_variance = float(residual_on_output.var(ddof=0))
        region_reports[region] = {
            "official_ids": len(indices),
            "Mx4_F_defined_ids": int(current_mask[indices].sum()),
            "S1_lagged_Mx4_F_defined_ids": int(lagged_mask[indices].sum()),
            "pairwise_defined_source_ids": n_pair,
            "innovation_defined_ids": n_out,
            "innovation_undefined_ids": int(len(indices) - n_out),
            "innovation_coverage_fraction": float(n_out / len(indices)),
            "current_Mx4_decile_edges": local_result["decile_edges"],
            "unique_current_Mx4_decile_bins": sorted(set(local_result["current_mx4_decile"][local_result["current_mx4_decile"] >= 0].tolist())),
            "peer_count_min": int(local_result["peer_count"][local_result["defined"]].min()) if n_out else None,
            "peer_count_max": int(local_result["peer_count"][local_result["defined"]].max()) if n_out else None,
            "source_S1_lagged_Mx4_F_distribution_on_innovation_support": distribution(s1_on_output, np.ones(n_out, bool)),
            "innovation_distribution": distribution(residual_on_output, np.ones(n_out, bool)),
            "remaining_variance_ratio": residual_variance / s1_variance if s1_variance > 0 else None,
            "NMI_innovation_vs_current_Mx4_F": nmi_stats,
        }

    # Preserve the source audit's exact Eastern Oklahoma baseline encoding/metric as a before check.
    east = np.asarray(regions) == "eastern-ok"
    before_nmi = measure_region_nmi(current[east], current_mask[east], lagged[east], lagged_mask[east])
    expected_source_nmi = 0.8201528016239874
    if before_nmi["joint_defined"] != 1192 or not np.isclose(before_nmi["nmi"], expected_source_nmi, rtol=0, atol=1e-12):
        raise ValueError("Eastern Oklahoma pre-screen NMI did not reproduce the frozen baseline")
    if source_nmi["pair"]["raw_nmi"] != expected_source_nmi:
        raise ValueError("recorded baseline source NMI differs from issued baseline")

    max_region = max(REGIONS, key=lambda r: float(region_reports[r]["NMI_innovation_vs_current_Mx4_F"]["nmi"]))
    total = len(official)
    output_defined_n = int(innovation_defined.sum())
    source_pair_n = int(pairwise_defined.sum())
    coverage = output_defined_n / total
    source_s1 = lagged[innovation_defined]
    innovation_values = innovation[innovation_defined]
    s1_variance = float(source_s1.var(ddof=0)) if source_s1.size else 0.0
    residual_variance = float(innovation_values.var(ddof=0)) if innovation_values.size else 0.0
    variance_ratio = residual_variance / s1_variance if s1_variance > 0 else None
    max_nmi_value = float(region_reports[max_region]["NMI_innovation_vs_current_Mx4_F"]["nmi"])
    output = output_table(geoids, regions, current, current_mask, lagged, lagged_mask, {
        "innovation": innovation, "defined": innovation_defined, "pairwise_defined": pairwise_defined,
        "current_mx4_decile": bin_values, "peer_bin_mean": peer_means, "peer_count": peer_counts})
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("innovation Parquet serialization is not deterministic")
    residual_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    residual_artifact_path.write_bytes(payload)

    registry_sha_after = sha256_file(registry_path)
    frozen_hashes_after = {idea: sha256_file(workspace / "ideas" / f"{idea}.parquet") for idea in ("Mx4", "S1")}
    frozen_unchanged = registry_sha_after == registry_sha_before and all(
        frozen_hashes_after[idea] == artifact_hashes[idea] for idea in ("Mx4", "S1"))
    if not frozen_unchanged:
        raise AssertionError("registry or frozen Mx4/S1 artifacts changed during the read-only diagnostic")
    acceptance = {
        "exact_9379_official_geoid_region_partition": len(official) == 9379 and output.num_rows == 9379,
        "pairwise_definedness_preserved": int(np.sum(innovation_defined & ~pairwise_defined)) == 0,
        "finite_coverage_at_least_98_59_percent": coverage >= MIN_COVERAGE,
        "maximum_within_region_NMI_below_0_80": max_nmi_value < MAX_NMI,
        "at_least_10_percent_of_S1_lag_variance_remains": variance_ratio is not None and variance_ratio >= MIN_REMAINING_VARIANCE_RATIO,
        "frozen_Mx4_S1_and_registry_unchanged": frozen_unchanged,
    }
    acceptance["distinct_innovation_flag"] = all(acceptance.values())
    report = {
        "schema_version": 1,
        "audit": "S1 lagged-Mx4 F leave-one-out empirical-decile innovation; label-free unregistered sensitivity screen",
        "method": {
            "definition": "Within each named study region, assign Mx4 F values to empirical decile bins using linear quantiles (NumPy method=linear; ties share bins). For each tract with pairwise-defined Mx4 F and S1 lagged-Mx4 F, subtract the mean S1 lag over other pairwise-defined tracts in the same Mx4 bin; the focal tract is excluded. If no other defined peer exists, leave its innovation null.",
            "definedness": "Innovation is defined only when both original features are defined and at least one other pairwise-defined tract exists in its same-region Mx4 empirical-decile bin.",
            "NMI": "Within-region arithmetic normalized mutual information 2*MI/(H(current Mx4)+H(innovation)), natural logarithms; each side uses the same deterministic encoder as the registered family nonlinear-orthogonality screen (categorical for integer low-cardinality data, otherwise empirical deciles). NMI is calculated on innovation-defined pairwise rows.",
            "variance_ratio": "Population variance (ddof=0) of pooled innovation values divided by population variance of S1 lagged-Mx4 values on the same innovation-defined support; the registered 10% gate is pooled across official GEOIDs and region-specific ratios are descriptive.",
            "input_scope": "Only the exact Mx4 F covariate and S1 F lagged-Mx4 emission are materialized from their registered Parquets. No other S1 channels or Tm1/Tm4 source artifacts are used.",
            "unregistered": True,
        },
        "scope": {
            "official_ids": total, "exact_official_geoid_region_partition": True,
            "pairwise_defined_source_ids": source_pair_n,
            "innovation_defined_ids": output_defined_n,
            "innovation_undefined_ids": total - output_defined_n,
            "coverage_fraction": coverage,
            "labels_read": False, "sample_score_values_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_inputs_read": False,
            "leaderboard_feedback_used": False, "Mx4_or_S1_modified": False,
            "registry_changed_or_innovation_registered": False,
            "only_declared_Mx4_and_S1_lagged_Mx4_emissions_used": True,
            "Tm1_or_Tm4_feature_values_used": False, "uploads": 0,
        },
        "measure_before": {
            "eastern_ok_Mx4_F_vs_S1_lagged_Mx4_F_raw_NMI": before_nmi["nmi"],
            "eastern_ok_pairwise_defined": before_nmi["joint_defined"],
            "adjusted_MI": 0.819574039123541,
            "permutation_p": 0.0004997501249375312,
            "source_audit_report_sha256": sha256_file(source_nmi_path),
        },
        "measure_after": {
            "maximum_within_region_NMI": max_nmi_value,
            "region_with_maximum_NMI": max_region,
            "pooled_innovation_variance": residual_variance,
            "pooled_source_S1_lag_variance_on_innovation_support": s1_variance,
            "pooled_remaining_variance_ratio": variance_ratio,
            "source_S1_lagged_Mx4_distribution_on_innovation_support": distribution(source_s1, np.ones(len(source_s1), bool)),
            "innovation_distribution": distribution(innovation_values, np.ones(len(innovation_values), bool)),
            "regions": region_reports,
        },
        "acceptance_thresholds": {"maximum_NMI_exclusive": MAX_NMI,
                                   "minimum_coverage_inclusive": MIN_COVERAGE,
                                   "minimum_remaining_variance_ratio_inclusive": MIN_REMAINING_VARIANCE_RATIO,
                                   "empirical_deciles": N_BINS},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": hashlib.sha256("\n".join(
                f"{geoid},{region}" for geoid, region in official).encode("utf-8")).hexdigest(),
            "frozen_input_artifacts": {idea: {"path": str(workspace / "ideas" / f"{idea}.parquet"),
                                              "sha256": artifact_hashes[idea],
                                              "selected_emission_sha256": feature_hashes[idea],
                                              "sha256_after": frozen_hashes_after[idea]}
                                       for idea in ("Mx4", "S1")},
            "registry_sha256_before": registry_sha_before,
            "registry_sha256_after": registry_sha_after,
            "harness_report_sha256": sha256_file(harness_path),
            "script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "output_artifact": {"path": str(residual_artifact_path), "rows": output.num_rows,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "columns": output.column_names, "deterministic_bytes": True},
        "interpretation": "The label-free screen meets the distinct-innovation thresholds; keep as a separate diagnostic sensitivity only, without registry integration or changes to frozen Mx4/S1."
        if acceptance["distinct_innovation_flag"] else
        "The innovation fails one or more thresholds; retain as diagnostic only and do not integrate or modify frozen Mx4/S1.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/s1-mx4-lagged-innovation.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE is required")
    output_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else \
        workspace / "s1-mx4-lagged-innovation" / "s1-mx4-lagged-innovation.parquet"
    report = audit(workspace, output_path, artifact_path)
    print(json.dumps({"coverage": report["scope"]["coverage_fraction"],
                      "innovation_defined_ids": report["scope"]["innovation_defined_ids"],
                      "max_within_region_nmi": report["measure_after"]["maximum_within_region_NMI"],
                      "remaining_variance_ratio": report["measure_after"]["pooled_remaining_variance_ratio"],
                      "distinct_innovation_flag": report["acceptance"]["distinct_innovation_flag"],
                      "output_artifact": report["output_artifact"],
                      "report": str(output_path.relative_to(ROOT))}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
