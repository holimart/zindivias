"""Label-free orthogonality measurement on the study tracts (spec section 3.3).

Computed from the emitted idea parquets only: the correlation matrix, variance inflation factors,
the condition number of the correlation matrix, and single/agglomerative clustering on 1 - |rho|.
No target, no label, no lab value and no leaderboard moment enters, which is exactly the part of
spec 3.3 that is allowed before the organiser ruling.

Merge decisions follow the preregistered thresholds (|rho| > 0.8 or VIF > 10 within a component)
and are reported, never applied silently: the combiner row consumes this report and decides.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from bias60.determinism import (canonical_bytes, normalized_bytes,  # noqa: E402
                                wall_clock_hits as _wall_clock_hits)


def load_columns(idea_paths: dict[str, Path]) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, float]], dict[str, list[str]]]:
    """Return (column keys, column values by GEOID, per-column region order)."""
    order: list[str] = []
    values: dict[str, dict[str, float]] = {}
    keys: dict[str, dict[str, str]] = {}
    for idea_id in sorted(idea_paths):
        path = Path(idea_paths[idea_id])
        if not path.is_file():
            raise FileNotFoundError(f"idea parquet missing for {idea_id}: {path}")
        table = pq.read_table(path, columns=["GEOID", "region", "idea", "family", "component",
                                             "channel", "value", "defined"])
        rows = table.to_pylist()
        for row in rows:
            if row["idea"] != idea_id:
                raise ValueError(f"{path.name} contains rows for idea {row['idea']}")
            column = f"{idea_id}|{row['component']}|{row['channel']}"
            if column not in values:
                values[column] = {}
                keys[column] = {"idea": idea_id, "family": row["family"],
                                "component": row["component"], "channel": row["channel"]}
                order.append(column)
            geoid = row["GEOID"]
            if geoid in values[column]:
                raise ValueError(f"duplicate GEOID {geoid} in {column}")
            values[column][geoid] = float(row["value"]) if row["defined"] else float("nan")
    return keys, values, order


def average_linkage_clusters(distance: np.ndarray, cut: float) -> list[int]:
    """Deterministic average-linkage agglomeration over a square distance matrix.

    Clusters are merged while their average distance is at most ``cut``. Ties are broken by the
    lowest pair of cluster labels, so the partition does not depend on dictionary or float order.
    """
    size = distance.shape[0]
    labels = list(range(size))
    clusters: dict[int, list[int]] = {index: [index] for index in range(size)}
    while True:
        best: tuple[float, int, int] | None = None
        for a in sorted(clusters):
            for b in sorted(clusters):
                if a >= b:
                    continue
                left, right = clusters[a], clusters[b]
                mean = float(np.mean([distance[i, j] for i in left for j in right]))
                if mean <= cut and (best is None or mean < best[0] - 1e-15
                                    or (abs(mean - best[0]) <= 1e-15 and (a, b) < (best[1], best[2]))):
                    best = (mean, a, b)
        if best is None:
            break
        _, a, b = best
        clusters[a] = clusters[a] + clusters[b]
        del clusters[b]
    partition = [0] * size
    for cluster_index, (_, members) in enumerate(sorted(clusters.items())):
        for member in members:
            partition[member] = cluster_index
    return partition


def correlation_matrix(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pairwise-complete Pearson correlation and the pairwise overlap counts."""
    columns = matrix.shape[1]
    correlation = np.full((columns, columns), np.nan, dtype=np.float64)
    overlap = np.zeros((columns, columns), dtype=np.int64)
    for i in range(columns):
        for j in range(columns):
            valid = np.isfinite(matrix[:, i]) & np.isfinite(matrix[:, j])
            overlap[i, j] = int(valid.sum())
            if valid.sum() < 3:
                continue
            left = matrix[valid, i] - matrix[valid, i].mean()
            right = matrix[valid, j] - matrix[valid, j].mean()
            denominator = float(np.sqrt((left * left).sum() * (right * right).sum()))
            correlation[i, j] = float((left * right).sum() / denominator) if denominator > 0 else 0.0
    return correlation, overlap


def variance_inflation(matrix: np.ndarray) -> list[float]:
    """VIF per column on the complete-case rows of the included matrix."""
    valid = np.all(np.isfinite(matrix), axis=1)
    rows = matrix[valid]
    if rows.shape[0] < matrix.shape[1] + 2:
        return [float("nan")] * matrix.shape[1]
    centred = rows - rows.mean(axis=0)
    out: list[float] = []
    for index in range(matrix.shape[1]):
        others = np.delete(centred, index, axis=1)
        if others.shape[1] == 0:
            out.append(1.0)
            continue
        target = centred[:, index]
        design = np.column_stack([np.ones(rows.shape[0]), others])
        coefficient, *_ = np.linalg.lstsq(design, target, rcond=None)
        residual = target - design @ coefficient
        total = float((target ** 2).sum())
        r_squared = float((residual ** 2).sum() / total) if total > 0 else 1.0
        variance = 1.0 - r_squared
        out.append(float("inf") if variance <= 1e-12 else float(1.0 / variance))
    return out


def build_report(*, config: dict[str, Any], idea_paths: dict[str, Path],
                 geoids: list[str], implemented_ids: list[str],
                 git_commit: str, versions: dict[str, str],
                 tamper: str | None = None) -> dict[str, Any]:
    thresholds = config["orthogonality"]
    keys, values, order = load_columns(idea_paths)
    if tamper == "drop_column":
        # Simulate a harness that silently forgets one estimator: every column of the first idea.
        dropped_idea = keys[order[0]]["idea"]
        order = [column for column in order if keys[column]["idea"] != dropped_idea]
    if tamper == "duplicate_column":
        order = list(order) + [order[0]]
    geoid_index = {geoid: position for position, geoid in enumerate(geoids)}
    matrix = np.full((len(geoids), len(order)), np.nan, dtype=np.float64)
    for position, column in enumerate(order):
        for geoid, value in values.get(column, {}).items():
            if geoid in geoid_index:
                matrix[geoid_index[geoid], position] = value

    coverage = [float(np.mean(np.isfinite(matrix[:, position]))) for position in range(len(order))]
    forced = 0 if tamper == "low_coverage" else None
    if tamper == "low_coverage":
        # Simulate a harness that reports a column it should have excluded for lack of coverage.
        matrix[: int(len(geoids) * 0.8), 0] = np.nan
        coverage = [float(np.mean(np.isfinite(matrix[:, position]))) for position in range(len(order))]
    included = [position for position, value in enumerate(coverage)
                if value >= thresholds["minimum_column_coverage"]]
    if forced is not None and forced not in included:
        included = sorted(included + [forced])
    excluded = [{"column": order[position], "coverage": coverage[position]}
                for position, value in enumerate(coverage)
                if value < thresholds["minimum_column_coverage"]]
    kept = [order[position] for position in included]
    kept_matrix = matrix[:, included]

    correlation, overlap = correlation_matrix(kept_matrix)
    if tamper == "break_symmetry":
        correlation[0, 1] += 0.05
    complete = np.all(np.isfinite(kept_matrix), axis=1)

    # A column with a single distinct value carries no information: its correlation is undefined and
    # its VIF is infinite. Those are reported as degenerate rather than hidden or silently dropped.
    distinct = []
    for position in range(kept_matrix.shape[1]):
        finite = kept_matrix[np.isfinite(kept_matrix[:, position]), position]
        distinct.append(int(np.unique(finite).size) if finite.size else 0)
    degenerate = [position for position, count in enumerate(distinct) if count < 2]

    # A column can be technically non-constant yet numerically null: the filtered-versus-unfiltered
    # road contrast lands at float64 noise (1e-15) for every tract. Those columns are declared here so
    # a reader cannot mistake float noise for a measured signal, and cannot be dropped quietly.
    epsilon = thresholds["numerical_null_epsilon"]
    spread: list[float] = []
    for position in range(kept_matrix.shape[1]):
        finite = kept_matrix[np.isfinite(kept_matrix[:, position]), position]
        spread.append(float(finite.max() - finite.min()) if finite.size else 0.0)
    numerically_null = [position for position, count in enumerate(distinct)
                        if count >= 2 and spread[position] <= epsilon]
    informative = [position for position in range(kept_matrix.shape[1]) if position not in degenerate]
    for position in degenerate:
        correlation[position, :] = np.nan
        correlation[:, position] = np.nan
    for position in informative:
        correlation[position, position] = 1.0

    informative_matrix = kept_matrix[:, informative]
    vif_full = variance_inflation(informative_matrix) if informative else []
    vif: list[float | None] = []
    cursor = 0
    for position in range(kept_matrix.shape[1]):
        if position in degenerate:
            vif.append(None)
        else:
            value = vif_full[cursor]
            vif.append(value if np.isfinite(value) else None)
            cursor += 1

    finite_correlation = np.where(np.isfinite(correlation), correlation, 0.0)
    distance = 1.0 - np.abs(finite_correlation)
    np.fill_diagonal(distance, 0.0)
    clusters = average_linkage_clusters(distance, 1.0 - thresholds["merge_abs_rho"])
    all_columns_matrix = finite_correlation + np.eye(len(kept)) * 1e-12
    condition_all = float(np.linalg.cond(all_columns_matrix))
    if informative:
        informative_correlation = finite_correlation[np.ix_(informative, informative)]
        condition = float(np.linalg.cond(informative_correlation + np.eye(len(informative)) * 1e-12))
    else:
        condition = float("nan")
    if not np.isfinite(condition_all):
        condition_all = None

    high_pairs = []
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            value = correlation[i, j]
            if np.isfinite(value) and abs(value) > thresholds["merge_abs_rho"]:
                high_pairs.append({"left": kept[i], "right": kept[j], "abs_rho": float(abs(value)),
                                   "same_component": keys[kept[i]]["component"] == keys[kept[j]]["component"],
                                   "same_family": keys[kept[i]]["family"] == keys[kept[j]]["family"]})
    high_vif = [{"column": kept[position], "vif": vif[position]} for position in range(len(kept))
                if vif[position] is not None and vif[position] > thresholds["merge_vif"]]
    degenerate_columns = [{"column": kept[position], "distinct_values": distinct[position],
                           "spread": spread[position],
                           "reason": "constant or empty over the study tracts: correlation undefined "
                                     "and VIF infinite"}
                          for position in degenerate]
    numerical_null_columns = [{"column": kept[position], "distinct_values": distinct[position],
                               "spread": spread[position], "epsilon": epsilon,
                               "reason": "values vary only below the preregistered numerical-null "
                                         "epsilon, so the estimator measured no difference on the "
                                         "study tracts"}
                              for position in numerically_null]

    within, between = [], []
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            value = correlation[i, j]
            if not np.isfinite(value):
                continue
            same = keys[kept[i]]["component"] == keys[kept[j]]["component"]
            (within if same else between).append(abs(value))
    mtmm = {
        "mean_abs_rho_within_component": float(np.mean(within)) if within else None,
        "mean_abs_rho_between_components": float(np.mean(between)) if between else None,
        "pairs_within_component": len(within),
        "pairs_between_components": len(between),
        "convergent_validity_expected": "same-component pairs from different families should correlate",
        "discriminant_validity_expected": "different-component pairs should correlate less",
    }

    cluster_members: dict[str, list[str]] = {}
    for position, column in enumerate(kept):
        cluster_members.setdefault(str(clusters[position]), []).append(column)

    def mean_distance(left: list[int], right: list[int]) -> float:
        return float(np.mean([distance[i, j] for i in left for j in right]))

    groups: dict[int, list[int]] = {}
    for position, cluster in enumerate(clusters):
        groups.setdefault(int(cluster), []).append(position)
    within_maximum = 0.0
    between_minimum = float("inf")
    keys_in_order = sorted(groups)
    for a_index, a in enumerate(keys_in_order):
        within_maximum = max(within_maximum, mean_distance(groups[a], groups[a]))
        for b in keys_in_order[a_index + 1:]:
            between_minimum = min(between_minimum, mean_distance(groups[a], groups[b]))
    if not keys_in_order:
        within_maximum, between_minimum = 0.0, float("nan")

    gates = {
        "evaluated_columns_are_the_implemented_ideas": sorted(
            {keys[column]["idea"] for column in order}) == sorted(implemented_ids),
        "every_official_geoid_present": all(
            len(values[column]) == len(geoids) for column in order),
        "no_duplicate_columns": len(order) == len(set(order)),
        "correlation_is_symmetric": bool(
            len(kept) == 0 or np.allclose(finite_correlation, finite_correlation.T, atol=1e-12)),
        "unit_diagonal_on_informative_columns": bool(
            not informative
            or np.allclose([correlation[p, p] for p in informative], 1.0, atol=1e-12)),
        "degenerate_columns_declared": all(
            kept[position] in {entry["column"] for entry in degenerate_columns}
            for position in degenerate),
        "numerically_null_columns_declared": {
            entry["column"] for entry in numerical_null_columns
        } == {kept[position] for position in numerically_null},
        "coverage_threshold_enforced": bool(
            all(coverage[position] >= thresholds["minimum_column_coverage"]
                for position in included)
            and all(column["coverage"] < thresholds["minimum_column_coverage"]
                    for column in excluded)),
        "merge_threshold_respected": bool(
            within_maximum <= 1.0 - thresholds["merge_abs_rho"] + 1e-12
            and (not np.isfinite(between_minimum)
                 or between_minimum > 1.0 - thresholds["merge_abs_rho"] - 1e-12)),
        "flagged_pairs_are_exactly_the_threshold_breaches": bool(
            all(abs(correlation[kept.index(p["left"]), kept.index(p["right"])]) 
                > thresholds["merge_abs_rho"] for p in high_pairs)
            and sum(1 for i in range(len(kept)) for j in range(i + 1, len(kept))
                    if np.isfinite(correlation[i, j])
                    and abs(correlation[i, j]) > thresholds["merge_abs_rho"]) == len(high_pairs)),
        "flagged_vif_are_exactly_the_threshold_breaches": sum(
            1 for value in vif
            if value is not None and np.isfinite(value)
            and value > thresholds["merge_vif"]) == len(high_vif),
        "condition_number_finite": bool(np.isfinite(condition)) if informative else True,
        "report_is_strict_json": _is_strict_json({"vif": vif, "condition": condition}),
        "label_free_no_target_column": all(
            not any(token in column.lower() for token in config["forbidden"]["column_tokens"])
            for column in order),
        "report_has_no_temporal_field": not _wall_clock_hits({"kept": kept, "mtmm": mtmm}),
    }
    gates["all_passed"] = all(gates.values())

    return {
        "preregistration": {"sha256": config.get("__sha256__", ""),
                            "thresholds": thresholds,
                            "spec": config.get("__spec__", {})},
        "scope": {"study_regions": config["regions"], "study_tracts": len(geoids),
                  "complete_case_rows": int(complete.sum()),
                  "label_free": True,
                  "uses_target_or_label": False},
        "environment": {"versions": versions, "git_commit": git_commit},
        "columns": [{"column": column, **keys[column], "coverage": coverage[order.index(column)]}
                    for column in order],
        "included_columns": kept,
        "excluded_columns": excluded,
        "correlation": {"columns": kept,
                        "matrix": [[None if not np.isfinite(value) else float(value)
                                    for value in row] for row in correlation],
                        "pairwise_overlap": overlap.tolist()},
        "variance_inflation_factors": [{"column": kept[position], "vif": vif[position],
                                        "degenerate": position in degenerate,
                                        "distinct_values": distinct[position]}
                                       for position in range(len(kept))],
        "degenerate_columns": degenerate_columns,
        "numerically_null_columns": numerical_null_columns,
        "column_spread": {kept[position]: spread[position] for position in range(len(kept))},
        "informative_columns": [kept[position] for position in informative],
        "condition_number": condition if np.isfinite(condition) else None,
        "condition_number_all_columns": condition_all,
        "clustering": {"distance": "1 - |rho|", "linkage": "average",
                       "merge_cut_on_distance": 1.0 - thresholds["merge_abs_rho"],
                       "clusters": {kept[position]: int(cluster)
                                    for position, cluster in enumerate(clusters)},
                       "cluster_members": cluster_members,
                       "maximum_within_cluster_mean_distance": within_maximum,
                       "minimum_between_cluster_mean_distance": (
                           None if not np.isfinite(between_minimum) else between_minimum)},
        "pairs_exceeding_merge_threshold": high_pairs,
        "columns_exceeding_vif_threshold": high_vif,
        "multitrait_multimethod": mtmm,
        "gates": gates,
        "summary": {"columns_evaluated": len(order), "columns_included": len(kept),
                    "clusters": len(set(clusters)) if clusters else 0,
                    "pairs_flagged": len(high_pairs), "columns_flagged_vif": len(high_vif),
                    "columns_degenerate": len(degenerate_columns),
                    "columns_numerically_null": len(numerical_null_columns),
                    "all_passed": gates["all_passed"]},
        "tamper_case": tamper,
    }


def _is_strict_json(document: Any) -> bool:
    try:
        json.dumps(document, allow_nan=False)
    except (ValueError, TypeError):
        return False
    return True


def report_sha256(report: dict[str, Any]) -> str:
    """Content hash of the report, excluding the run-time commit fields."""
    return hashlib.sha256(normalized_bytes(report)).hexdigest()
