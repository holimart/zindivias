#!/usr/bin/env python3
"""Screen a bounded Tm5 POI version-zero share × Mx4 source-context interaction."""
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
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
MIN_COVERAGE = 0.9859
MIN_P95_CHANGE = 0.01
BOUNDS = (0.0, 1.0)
TM5_EMISSION = ("Tm5", "Tm", "Est", "covariate:tm5_poi_version0_share")
MX4_EMISSION = ("Mx4", "Mx", "F", "covariate")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clipped_interaction(tm5: np.ndarray, tm5_defined: np.ndarray,
                        mx4: np.ndarray, mx4_defined: np.ndarray,
                        low: float = 0.0, high: float = 1.0
                        ) -> dict[str, Any]:
    """Clip finite inputs to registered [low,high] bounds and multiply on joint rows only."""
    x = np.asarray(tm5, dtype=np.float64)
    y = np.asarray(mx4, dtype=np.float64)
    xm = np.asarray(tm5_defined, dtype=bool)
    ym = np.asarray(mx4_defined, dtype=bool)
    if x.ndim != 1 or y.ndim != 1 or x.shape != y.shape or xm.shape != x.shape or ym.shape != x.shape:
        raise ValueError("Tm5/Mx4 values and defined masks must be aligned one-dimensional arrays")
    if not np.isfinite(low) or not np.isfinite(high) or low > high:
        raise ValueError("interaction clipping bounds must be finite and ordered")
    if np.any(xm & ~np.isfinite(x)) or np.any(ym & ~np.isfinite(y)):
        raise ValueError("defined source values must be finite")
    x_clipped = np.full(x.shape, np.nan, dtype=np.float64)
    y_clipped = np.full(y.shape, np.nan, dtype=np.float64)
    x_clipped[xm] = np.clip(x[xm], low, high)
    y_clipped[ym] = np.clip(y[ym], low, high)
    joint = xm & ym
    product = np.full(x.shape, np.nan, dtype=np.float64)
    product[joint] = x_clipped[joint] * y_clipped[joint]
    if np.any(joint & (~np.isfinite(product) | (product < low) | (product > high))):
        raise AssertionError("clipped product escaped registered bounds")
    return {
        "tm5_clipped": x_clipped, "mx4_clipped": y_clipped,
        "interaction": product, "defined": joint,
        "tm5_clip_count": int(np.sum(xm & ((x < low) | (x > high)))),
        "mx4_clip_count": int(np.sum(ym & ((y < low) | (y > high)))),
    }


def distribution(values: np.ndarray) -> dict[str, Any]:
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 1 or (x.size and not np.isfinite(x).all()):
        raise ValueError("distribution requires finite one-dimensional values")
    if not x.size:
        return {"n": 0, "min": None, "p50": None, "p90": None,
                "p95": None, "max": None, "mean": None}
    return {"n": int(x.size), "min": float(x.min()), "p50": float(np.quantile(x, .5)),
            "p90": float(np.quantile(x, .9)), "p95": float(np.quantile(x, .95)),
            "max": float(x.max()), "mean": float(x.mean())}


def feature_digest(official: list[tuple[str, str]], values: np.ndarray,
                   defined: np.ndarray) -> str:
    lines = [f"{geoid},{region},{int(ok)}," + (format(float(value), ".17g") if ok else "NULL")
             for (geoid, region), value, ok in zip(official, values, defined)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def read_emission(workspace: Path, idea: str, family: str, component: str, channel: str,
                  official: list[tuple[str, str]], expected_sha: str
                  ) -> tuple[np.ndarray, np.ndarray, str]:
    path = workspace / "ideas" / f"{idea}.parquet"
    if not path.is_file():
        raise FileNotFoundError(path)
    artifact_sha = sha256_file(path)
    if artifact_sha != expected_sha:
        raise ValueError(f"{idea} artifact hash differs from frozen harness")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "family", "component",
                                         "channel", "value", "defined"],
                          filters=[("idea", "=", idea), ("component", "=", component),
                                   ("channel", "=", channel)])
    if table.num_rows != len(official):
        raise ValueError(f"{idea}/{component}/{channel}: unexpected emission row count")
    index = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in index or pair in seen:
            raise ValueError(f"{idea}: duplicate or out-of-scope official pair {pair}")
        if (row["idea"], row["family"], row["component"], row["channel"]) != (
                idea, family, component, channel):
            raise ValueError("selected feature metadata differs from registered emission")
        seen.add(pair)
        if row["defined"]:
            value = float(row["value"])
            if not np.isfinite(value):
                raise ValueError(f"{idea}: defined feature value is nonfinite")
            values[index[pair]] = value
            defined[index[pair]] = True
    if seen != set(official):
        raise ValueError(f"{idea}: selected emission does not exactly reconcile to official IDs")
    return values, defined, artifact_sha


def output_table(official: list[tuple[str, str]], tm5: np.ndarray, tm5_defined: np.ndarray,
                 mx4: np.ndarray, mx4_defined: np.ndarray, result: dict[str, Any]) -> pa.Table:
    n = len(official)
    keys = ("tm5_clipped", "mx4_clipped", "interaction", "defined")
    if any(np.asarray(result[key]).shape != (n,) for key in keys):
        raise ValueError("interaction output arrays must align with official GEOIDs")
    expected = tm5_defined & mx4_defined
    if not np.array_equal(result["defined"], expected):
        raise ValueError("interaction must preserve the exact pairwise source mask")
    ids, regions = zip(*official)
    return pa.table({
        "GEOID": pa.array(ids, type=pa.string()), "region": pa.array(regions, type=pa.string()),
        "tm5_poi_version0_share": pa.array([float(v) if ok else None for v, ok in zip(tm5, tm5_defined)], type=pa.float64()),
        "tm5_defined": pa.array(tm5_defined, type=pa.bool_()),
        "mx4_f_source_share": pa.array([float(v) if ok else None for v, ok in zip(mx4, mx4_defined)], type=pa.float64()),
        "mx4_defined": pa.array(mx4_defined, type=pa.bool_()),
        "tm5_clipped_0_1": pa.array([float(v) if ok else None for v, ok in zip(result["tm5_clipped"], tm5_defined)], type=pa.float64()),
        "mx4_clipped_0_1": pa.array([float(v) if ok else None for v, ok in zip(result["mx4_clipped"], mx4_defined)], type=pa.float64()),
        "tm5_mx4_product": pa.array([float(v) if ok else None for v, ok in zip(result["interaction"], result["defined"])], type=pa.float64()),
        "interaction_defined": pa.array(result["defined"], type=pa.bool_()),
    })


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    registry_path = ROOT / "config/ideas.json"
    harness_path = ROOT / "reports/ideas-harness.json"
    tm_report_path = ROOT / "reports/tm-family-nonlinear-orthogonality.json"
    registry_sha_before = sha256_file(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    implemented = {idea["id"] for idea in registry["ideas"] if idea.get("status") == "implemented"}
    if not {"Tm5", "Mx4"}.issubset(implemented):
        raise ValueError("Tm5 and Mx4 must remain registered inputs")
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    source_nmi = json.loads(tm_report_path.read_text(encoding="utf-8"))
    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len(set(official)) != 9379 or len({g for g, _ in official}) != 9379:
        raise ValueError("official GEOID-region list must be an exact 9,379-ID partition")
    if set(r for _, r in official) != set(REGIONS):
        raise ValueError("official partition has missing or unexpected regions")

    values: dict[str, np.ndarray] = {}
    masks: dict[str, np.ndarray] = {}
    artifact_hashes: dict[str, str] = {}
    feature_hashes: dict[str, str] = {}
    selected = {"Tm5": TM5_EMISSION, "Mx4": MX4_EMISSION}
    for idea, (idea_id, family, component, channel) in selected.items():
        entry = harness.get("per_idea", {}).get(idea)
        if not entry or not entry.get("geoid_partition_exact") or entry.get("forbidden_source_hits"):
            raise ValueError(f"frozen harness does not certify clean exact-ID output for {idea}")
        vals, defined, artifact_sha = read_emission(workspace, idea_id, family, component, channel,
                                                    official, entry["sha256"])
        values[idea], masks[idea], artifact_hashes[idea] = vals, defined, artifact_sha
        feature_hashes[idea] = feature_digest(official, vals, defined)
    result = clipped_interaction(values["Tm5"], masks["Tm5"], values["Mx4"], masks["Mx4"], *BOUNDS)
    joint = result["defined"]
    change = np.abs(result["interaction"][joint] - result["tm5_clipped"][joint])
    coverage = float(joint.mean())
    by_region: dict[str, Any] = {}
    for region in REGIONS:
        region_mask = np.asarray([r == region for _, r in official], dtype=bool)
        local_joint = joint & region_mask
        local_delta = np.abs(result["interaction"][local_joint] - result["tm5_clipped"][local_joint])
        by_region[region] = {
            "official_ids": int(region_mask.sum()),
            "tm5_defined_ids": int((masks["Tm5"] & region_mask).sum()),
            "mx4_defined_ids": int((masks["Mx4"] & region_mask).sum()),
            "pairwise_defined_ids": int(local_joint.sum()),
            "pairwise_coverage_fraction": float(local_joint.sum() / region_mask.sum()),
            "tm5_place_version0_share": distribution(values["Tm5"][masks["Tm5"] & region_mask]),
            "mx4_f_source_share": distribution(values["Mx4"][masks["Mx4"] & region_mask]),
            "interaction": distribution(result["interaction"][local_joint]),
            "absolute_change_from_tm5": distribution(local_delta),
            "changed_ids": int(np.count_nonzero(local_delta > 0.0)),
        }
    table = output_table(official, values["Tm5"], masks["Tm5"], values["Mx4"], masks["Mx4"], result)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("interaction artifact serialization is not deterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    bounded = bool(np.isfinite(result["interaction"][joint]).all()
                   and np.all((result["interaction"][joint] >= 0.0)
                              & (result["interaction"][joint] <= 1.0)))
    exact_ids = bool(table.num_rows == len(official) == 9379)
    p95_change = float(np.quantile(change, .95)) if change.size else None
    acceptance = {
        "exact_9379_geoid_region_alignment": exact_ids,
        "pairwise_defined_ids": int(joint.sum()), "pairwise_coverage_fraction": coverage,
        "pairwise_coverage_at_least_98_59_percent": coverage >= MIN_COVERAGE,
        "all_interaction_values_finite_and_in_0_1": bounded,
        "p95_absolute_change_from_existing_tm5": p95_change,
        "p95_absolute_change_at_least_0_01": bool(p95_change is not None and p95_change >= MIN_P95_CHANGE),
        "acceptance_pass": bool(exact_ids and coverage >= MIN_COVERAGE and bounded
                                 and p95_change is not None and p95_change >= MIN_P95_CHANGE),
    }
    artifact_sha_after = sha256_file(artifact_path)
    hashes_after = {idea: sha256_file(workspace / "ideas" / f"{idea}.parquet") for idea in selected}
    registry_sha_after = sha256_file(registry_path)
    frozen_unchanged = (hashes_after == artifact_hashes and registry_sha_after == registry_sha_before)
    if not frozen_unchanged:
        raise AssertionError("frozen Tm5/Mx4 or registry changed during diagnostic")
    report = {
        "schema_version": 1,
        "screen": "Tm5 POI version-zero share × Mx4 F source-context interaction; unregistered sensitivity",
        "inputs": {
            "Tm5": "Registered Tm5 Est covariate:tm5_poi_version0_share, the provided Overture POI share whose version is 0 (new this release); this naturally bounded [0,1] share is used as the specified place-version churn input.",
            "Mx4": "Frozen registered Mx4 F covariate (county building source share), naturally bounded [0,1].",
            "formula": "interaction = clip(Tm5_poi_version0_share,0,1) * clip(Mx4_F,0,1); comparison baseline is clipped Tm5 input on the pairwise-defined support.",
        },
        "scope": {
            "official_ids": len(official), "exact_id_alignment": exact_ids,
            "Tm5_defined_ids": int(masks["Tm5"].sum()), "Mx4_defined_ids": int(masks["Mx4"].sum()),
            "pairwise_defined_ids": int(joint.sum()), "pairwise_coverage_fraction": coverage,
            "labels_read": False, "sample_score_values_read": False,
            "Tm1_or_Tm4_inputs_or_artifacts_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_inputs_read": False,
            "leaderboard_feedback_used": False, "registry_integration": False,
            "frozen_Tm5_or_Mx4_modified": False, "uploads": 0,
        },
        "measure_before": {
            "Tm2_Tm3_Tm5_maximum_region_NMI": source_nmi["maximum_nmi"],
            "Tm2_Tm3_Tm5_source_partition_exact": source_nmi["scope"]["exact_9379_id_partition"],
            "no_prior_Tm5_Mx4_interaction_measured": True,
            "Tm5_source_distribution": distribution(values["Tm5"][masks["Tm5"]]),
            "Mx4_source_distribution": distribution(values["Mx4"][masks["Mx4"]]),
        },
        "measure_after": {
            "pairwise_defined_ids": int(joint.sum()), "pairwise_coverage_fraction": coverage,
            "Tm5_clip_count": result["tm5_clip_count"], "Mx4_clip_count": result["mx4_clip_count"],
            "interaction_distribution": distribution(result["interaction"][joint]),
            "absolute_change_from_tm5": distribution(change),
            "changed_ids": int(np.count_nonzero(change > 0.0)),
            "regions": by_region,
        },
        "acceptance_thresholds": {"minimum_pairwise_coverage": MIN_COVERAGE,
                                   "interaction_bounds_inclusive": list(BOUNDS),
                                   "minimum_p95_absolute_change": MIN_P95_CHANGE},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": hashlib.sha256("\n".join(
                f"{g},{r}" for g, r in official).encode("utf-8")).hexdigest(),
            "frozen_inputs": {idea: {"path": str(workspace / "ideas" / f"{idea}.parquet"),
                                     "sha256": artifact_hashes[idea], "sha256_after": hashes_after[idea],
                                     "selected_emission_sha256": feature_hashes[idea]}
                              for idea in selected},
            "registry_sha256_before": registry_sha_before, "registry_sha256_after": registry_sha_after,
            "harness_report_sha256": sha256_file(harness_path),
            "Tm_nonlinear_orthogonality_report_sha256": sha256_file(tm_report_path),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": artifact_sha_after, "columns": table.column_names,
                                "deterministic_bytes": True},
            "script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "interpretation": "All acceptance criteria pass; keep this only as a separate unregistered diagnostic, without accuracy/promotion claims or automatic integration." if acceptance["acceptance_pass"] else
        "The p95 absolute-change criterion fails or another invariant failed; retain frozen Tm5 unchanged and do not integrate the interaction.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm5-mx4-churn-interaction.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "tm5-mx4-churn-interaction" / "tm5-mx4-product.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"],
                      "interaction": report["measure_after"]["interaction_distribution"],
                      "output_artifact": report["provenance"]["output_artifact"],
                      "report": str(report_path.relative_to(ROOT))}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
