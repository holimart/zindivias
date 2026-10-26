#!/usr/bin/env python3
"""Summarize Family T coverage, distributions, pairwise correlations and VIF label-free."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60.inputs import official_scored_pairs, sha256  # noqa: E402
from bias60.orthogonality import load_columns  # noqa: E402

IDS = [f"T{i}" for i in range(1, 6)]
LIMITATIONS = {
    "T1": "Joins exact case-folded names.primary/route identities; the frozen <30-degree deflection-angle stroke criterion is not implemented.",
    "T2": "Identifies links whose connector neighbors are non-named only; emitted length is the in-tract link length, not adjacent carriageway length within 2 km.",
    "T3": "Uses provided road_flags/level_rules as grade-separation evidence; it does not enumerate geometric crossing pairs without shared connectors.",
    "T4": "Uses tract-local connector-degree product as a centrality proxy; this is not sampled shortest-path edge betweenness.",
    "T5": "Building-centroid distance to any provided roads-unfiltered geometry; centroids are assigned strictly within the provided tract polygons.",
}


def _pearson(left: np.ndarray, right: np.ndarray) -> tuple[float | None, int]:
    valid = np.isfinite(left) & np.isfinite(right)
    count = int(valid.sum())
    if count < 3:
        return None, count
    x, y = left[valid] - left[valid].mean(), right[valid] - right[valid].mean()
    denominator = float(np.sqrt(np.dot(x, x) * np.dot(y, y)))
    return (float(np.dot(x, y) / denominator) if denominator else 0.0), count


def main() -> int:
    workspace = Path(os.environ.get("COMPETITION_WORKSPACE", ""))
    if not workspace.is_dir():
        raise SystemExit("COMPETITION_WORKSPACE must point to the authorized data workspace")
    harness = json.loads((ROOT / "reports/ideas-harness.json").read_text())
    orth = json.loads((ROOT / "reports/ideas-orthogonality.json").read_text())
    expected_pairs = official_scored_pairs(workspace)
    expected = set(expected_pairs)
    idea_paths: dict[str, Path] = {}
    tables: dict[str, object] = {}
    for idea in harness["per_idea"]:
        entry = harness["per_idea"][idea]
        path = Path(entry["path"])
        if not path.is_absolute():
            path = workspace / path
        if not path.is_file() or sha256(path) != entry["sha256"]:
            raise ValueError(f"artifact missing or hash mismatch: {idea}")
        idea_paths[idea] = path
        if idea in IDS:
            table = pq.read_table(path, columns=["GEOID", "region", "component", "channel", "value", "defined"])
            got = set(zip(table.column("GEOID").to_pylist(), table.column("region").to_pylist()))
            if got != expected:
                raise ValueError(f"{idea} does not cover the exact official GEOID/region partition")
            tables[idea] = table
    if not set(IDS).issubset(tables):
        raise ValueError(f"Family T outputs missing: {sorted(set(IDS) - set(tables))}")

    _, values_by_column, all_columns = load_columns(idea_paths)
    geoids = [geoid for geoid, _ in expected_pairs]
    vif_by_column = {entry["column"]: entry for entry in orth["variance_inflation_factors"]}
    outputs = {}
    high_pairs = []
    for idea in IDS:
        table = tables[idea]
        channels = []
        rows = table.to_pylist()
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            grouped.setdefault((row["component"], row["channel"]), []).append(row)
        for (component, channel), entries in sorted(grouped.items()):
            column = f"{idea}|{component}|{channel}"
            defined = np.asarray([row["defined"] for row in entries], dtype=bool)
            data = np.asarray([row["value"] for row in entries], dtype=np.float64)
            finite = data[defined & np.isfinite(data)]
            quantiles = (np.quantile(finite, [0, .25, .5, .75, .9, .95, 1]).tolist()
                         if finite.size else [None] * 7)
            correlations = []
            left_map = values_by_column[column]
            left = np.asarray([left_map.get(geoid, np.nan) for geoid in geoids], dtype=np.float64)
            for other in all_columns:
                if other == column:
                    continue
                right_map = values_by_column[other]
                right = np.asarray([right_map.get(geoid, np.nan) for geoid in geoids], dtype=np.float64)
                rho, overlap = _pearson(left, right)
                correlations.append({"column": other, "pearson_r": rho,
                                     "pairwise_defined_rows": overlap})
                if rho is not None and abs(rho) > 0.8:
                    high_pairs.append({"left": column, "right": other, "pearson_r": rho,
                                       "pairwise_defined_rows": overlap,
                                       "retained_and_flagged": True})
            channels.append({
                "component": component, "channel": channel,
                "coverage": {"defined": int(defined.sum()), "rows": len(entries),
                             "fraction": float(defined.mean()) if len(entries) else 0.0},
                "distribution": {"min_q25_median_q75_q90_q95_max": quantiles,
                                 "mean": float(finite.mean()) if finite.size else None,
                                 "std": float(finite.std()) if finite.size else None},
                "correlations_to_every_implemented_channel": correlations,
                "vif": vif_by_column.get(column),
            })
        outputs[idea] = {"artifact": str(idea_paths[idea]),
                         "sha256": harness["per_idea"][idea]["sha256"],
                         "rows": int(table.num_rows), "limitations": LIMITATIONS[idea],
                         "channels": channels}

    t_vif_flags = [entry for entry in orth["columns_exceeding_vif_threshold"]
                   if entry["column"].split("|", 1)[0] in IDS]
    report = {
        "schema_version": 1, "family": "T", "title": "Topology and network structure",
        "scope": {"official_geoids": len(expected), "label_free": True,
                  "correlation": "pairwise-complete Pearson r over the official tract set",
                  "vif_source": "reports/ideas-orthogonality.json; VIF on its preregistered complete-case matrix"},
        "harness_gates": harness["gates"], "orthogonality_gates": orth["gates"],
        "outputs": outputs,
        "redundancy_flags": {"threshold_abs_r": 0.8,
                             "vif_threshold": orth["preregistration"]["thresholds"]["merge_vif"],
                             "vif_flags_involving_T": t_vif_flags,
                             "high_correlation_pairs_involving_T": high_pairs,
                             "handling": "all channels retained; flags disclosed for downstream merge handling; no silent drops"},
    }
    out = ROOT / "reports/bias60-family-t-summary.json"
    payload = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    out.write_bytes(payload)
    print(json.dumps({"out": str(out), "sha256": hashlib.sha256(payload).hexdigest(),
                      "ideas": len(outputs), "channels": sum(len(row["channels"]) for row in outputs.values()),
                      "high_correlation_pairs_involving_T": len(high_pairs)}, indent=2, sort_keys=True))
    print("PASS Family T summary: exact official GEOID partition, artifacts hash-verified, all channels measured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
