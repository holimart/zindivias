#!/usr/bin/env python3
"""County-cluster bootstrap recheck of the NorCal post-fire disagreement finding.

The published discovery (reports/discovery-postfire.json) compares the share of high-burn
Northern California tracts whose provided Overture-to-USFS building-count ratio falls below the
preregistered disagreement cutoff with the same share among unburned tracts, and bootstraps that
difference by resampling tracts independently. Tracts inside one county are not independent: they
share the Overture provider mix, the terrain and the reference vintage, so the tract-level interval
understates county-level dependence. This audit rechecks the same difference by resampling whole
counties, preserving every tract record of each selected county.

It is a robustness screen, not model promotion. Nothing here changes a model, a threshold or a
candidate, and no organiser target, leaderboard value, removed reference layer or external source is
read.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import os
import platform
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "discovery-postfire-county-bootstrap-preregistered.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
FILE_ACCESS = {"open", "read_text", "read_bytes", "ParquetDataset", "read_table", "parquet_reader",
               "S3FileSystem", "urlopen", "request", "download_file"}
WALL_CLOCK_KEYS = {"generated_at", "timestamp", "run_at", "finished_at", "started_at", "now",
                   "retrieved_at_utc", "snapshot_utc", "observed_at", "date"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=1, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def delegated_sample_reader() -> tuple[Callable[[Path], list[str]], str]:
    """Compile read_geoid_only out of the frozen discovery script and use exactly that code.

    Loading the module would import matplotlib; extracting the single function guarantees the
    official sample is read by the frozen GEOID-only reader, which never touches a score column.
    """
    source_path = ROOT / CONFIG["forbidden_source_scan"]["delegated_sample_reader"]
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next((node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "read_geoid_only"), None)
    if function is None:
        raise ValueError(f"delegated reader read_geoid_only not found in {source_path}")
    namespace: dict[str, Any] = {"csv": csv, "Path": Path, "list": list, "str": str}
    module_node = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module_node)
    exec(compile(module_node, "<delegated:read_geoid_only>", "exec"), namespace)
    return namespace["read_geoid_only"], sha256_file(source_path)


def opened_literals(script: Path) -> list[str]:
    tree = ast.parse(script.read_text(encoding="utf-8"))
    opened: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            name = function.attr if isinstance(function, ast.Attribute) else (
                function.id if isinstance(function, ast.Name) else None)
            if name in FILE_ACCESS:
                for argument in ast.walk(node):
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        opened.append(argument.value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "PATH" in target.id.upper():
                    for literal in ast.walk(node):
                        if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                            opened.append(literal.value)
    return opened


def forbidden_source_scan(script: Path) -> dict[str, Any]:
    """Fail closed if this audit can read a label, leaderboard, ledger, credential or removed layer."""
    scan = CONFIG["forbidden_source_scan"]
    prohibited = list(scan["prohibited_path_tokens"]) + list(scan["prohibited_uri_tokens"])
    literals = opened_literals(script)
    script_text = script.read_text(encoding="utf-8")
    hits = sorted({literal for literal in literals
                   if any(token in literal for token in prohibited)})
    numeric_hits = sorted({token for token in scan["prohibited_numeric_literals"]
                           if token in script_text})
    sample_tokens = list(scan["sample_file_tokens"])
    sample_opens = sorted({literal for literal in literals
                           if any(token in literal for token in sample_tokens)})
    return {"scanned_script": relative(script),
            "forbidden_opened_hits": hits,
            "forbidden_numeric_literal_hits": numeric_hits,
            "prohibited_tokens_applied": prohibited,
            "sample_files_opened_by_this_script": sample_opens,
            "delegated_sample_reader": {
                "module": CONFIG["sample_reader"]["module"],
                "function": CONFIG["sample_reader"]["function"],
                "declared_columns_read": list(CONFIG["sample_reader"]["declared_columns_read"]),
                "score_placeholders_read": CONFIG["sample_reader"]["score_placeholders_read"]},
            "external_uri_tokens_applied": list(scan["prohibited_uri_tokens"])}


def wall_clock_hits(node: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in WALL_CLOCK_KEYS:
                hits.append(f"{path}/{key}")
            hits += wall_clock_hits(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits += wall_clock_hits(value, f"{path}[{index}]")
    elif isinstance(node, str):
        import re
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([.+]\d{2}:\d{2}|Z)?", node):
            hits.append(f"{path}=iso8601")
    return hits


def finite(value: Any, name: str) -> float:
    if value is None:
        raise ValueError(f"null required value: {name}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"nonfinite required value: {name}={value}")
    return number


def burn_class(value: float) -> str | None:
    """Mutually exclusive provided-burn bins; anything outside [0, 100] is excluded, never clipped."""
    if value < 0 or value > 100:
        return None
    if value == 0:
        return "unburned_0"
    if value <= 5:
        return "gt0_to5"
    if value <= 20:
        return "gt5_to20"
    if value < 50:
        return "gt20_lt50"
    return "50_to100"


def share(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise ValueError("empty group")
    return numerator / denominator


def group_table(records: list[dict[str, Any]], label: str) -> dict[str, Any]:
    under = sum(record["below_cutoff"] for record in records)
    return {"group": label, "tracts": len(records), "below_cutoff_n": under,
            "share_below_cutoff": share(under, len(records)),
            "counties": len({record["county"] for record in records})}


def county_cluster_bootstrap(cluster_labels: list[str], high_n: np.ndarray, high_b: np.ndarray,
                            unburned_n: np.ndarray, unburned_b: np.ndarray,
                            all_n: np.ndarray, replicates: int, seed: int,
                            analysis_population: int, county_count: int) -> dict[str, Any]:
    """Resample the supplied clusters with replacement and recompute the share difference per replicate.

    Each cluster is one county in the primary design, so selecting a county puts every one of its
    tract records into the replicate. The negative control passes tract-sized singleton clusters
    instead, which the structural checks below must catch.
    """
    cluster_count = len(cluster_labels)
    if cluster_count <= 0:
        raise ValueError("empty cluster pool")
    if not (len(high_n) == len(high_b) == len(unburned_n) == len(unburned_b) == len(all_n)
            == cluster_count):
        raise ValueError("cluster arrays must be the same length")
    # A pool that does not cover the whole analysis population has silently dropped tracts or
    # counties; that is reported and gated rather than raising, so the failure is auditable.
    multiplicities = np.random.default_rng(seed).multinomial(
        cluster_count, [1.0 / cluster_count] * cluster_count, size=replicates)
    high_total = multiplicities @ high_n
    high_under = multiplicities @ high_b
    unburned_total = multiplicities @ unburned_n
    unburned_under = multiplicities @ unburned_b
    valid = (high_total > 0) & (unburned_total > 0)
    differences = np.full(replicates, np.nan)
    differences[valid] = (high_under[valid] / high_total[valid]
                          - unburned_under[valid] / unburned_total[valid])
    valid_values = differences[valid]
    low, high = (np.quantile(valid_values, [0.025, 0.975]) if valid_values.size else (np.nan, np.nan))
    # Structural proof that each selected county contributed every one of its tracts.
    all_total = multiplicities @ all_n
    sampled = list(range(min(5, replicates))) + [int(index) for index in
                                                 np.random.default_rng(seed + 1).choice(
                                                     replicates, size=5, replace=False)]
    preservation = []
    for position in sorted(set(sampled)):
        row = multiplicities[position]
        expected_all = int(sum(int(row[index]) * int(all_n[index]) for index in range(cluster_count)))
        expected_high = int(sum(int(row[index]) * int(high_n[index]) for index in range(cluster_count)))
        expected_unburned = int(sum(int(row[index]) * int(unburned_n[index])
                                    for index in range(cluster_count)))
        preservation.append({
            "replicate": position,
            "clusters_selected": int((row > 0).sum()),
            "tracts_all_classes": expected_all,
            "tracts_all_classes_matches_matrix_product": expected_all == int(all_total[position]),
            "focal_tracts": expected_high + expected_unburned,
            "focal_tracts_match": (expected_high == int(high_total[position])
                                   and expected_unburned == int(unburned_total[position])),
            "non_focal_tracts_retained": expected_all - expected_high - expected_unburned,
        })
    singletons = int((all_n <= 1).sum())
    return {
        "cluster_count": cluster_count,
        "cluster_count_equals_county_count": cluster_count == county_count,
        "analysis_population_covered_by_clusters": int(all_n.sum()),
        "expected_analysis_population": analysis_population,
        "cluster_population_matches": int(all_n.sum()) == analysis_population,
        "singleton_clusters": singletons,
        "cluster_sizes": [{"county": county, "tracts_all_burn_classes": int(all_n[index]),
                           "high_burn_tracts": int(high_n[index]),
                           "high_burn_below_cutoff": int(high_b[index]),
                           "unburned_tracts": int(unburned_n[index]),
                           "unburned_below_cutoff": int(unburned_b[index])}
                          for index, county in enumerate(cluster_labels)],
        "replicates": replicates,
        "seed": seed,
        "valid_replicates": int(valid.sum()),
        "valid_replicate_fraction": float(valid.sum()) / replicates,
        "invalid_replicates": int(replicates - valid.sum()),
        "invalid_reason": "no high-burn tract or no unburned tract in the resampled counties",
        "cluster_95_percentile_interval": [float(low), float(high)],
        "confidence_level_percent": 95.0,
        "percentile_bounds": [2.5, 97.5],
        "positive_difference_replicates": int((valid_values > 0).sum()),
        "positive_difference_fraction_of_valid": (float((valid_values > 0).sum()) / int(valid.sum())
                                                  if int(valid.sum()) else None),
        "mean_difference": float(valid_values.mean()) if valid_values.size else None,
        "min_difference": float(valid_values.min()) if valid_values.size else None,
        "max_difference": float(valid_values.max()) if valid_values.size else None,
        "tract_preservation_checks": preservation,
        "tract_preservation_holds": all(row["tracts_all_classes_matches_matrix_product"]
                                        and row["focal_tracts_match"] and
                                        row["non_focal_tracts_retained"] >= 0
                                        for row in preservation),
        "tracts_outside_focal_groups_retained": bool(all(row["non_focal_tracts_retained"] > 0
                                                         for row in preservation)),
    }


def within_group_county_cluster(records: list[dict[str, Any]], replicates: int,
                                seed: int) -> dict[str, Any]:
    """Declared secondary sensitivity: counties resampled separately inside each focal group."""
    output: dict[str, Any] = {"design": "within_group_county_cluster",
                              "may_change_verdict": False,
                              "reason_secondary": ("keeps only the focal burn class of a selected "
                                                   "county, so it does not preserve every tract "
                                                   "record of that county")}
    for label, key in (("high_burn", "50_to100"), ("unburned", "unburned_0")):
        rows = [row for row in records if row["burn_class"] == key]
        counties = sorted({row["county"] for row in rows})
        index = {county: position for position, county in enumerate(counties)}
        counts = np.zeros(len(counties), dtype=np.int64)
        under = np.zeros(len(counties), dtype=np.int64)
        for row in rows:
            counts[index[row["county"]]] += 1
            under[index[row["county"]]] += int(row["below_cutoff"])
        mult = np.random.default_rng(seed).multinomial(
            len(counties), [1.0 / len(counties)] * len(counties), size=replicates)
        total = mult @ counts
        below = mult @ under
        values = below[total > 0] / total[total > 0]
        low, high = np.quantile(values, [0.025, 0.975])
        output[label] = {"counties": len(counties), "valid_replicates": int((total > 0).sum()),
                         "share_95_percentile_interval": [float(low), float(high)]}
    high_draw = np.array(output["high_burn"]["share_95_percentile_interval"])
    unburned_draw = np.array(output["unburned"]["share_95_percentile_interval"])
    output["difference_interval_from_group_intervals"] = [
        float(high_draw[0] - unburned_draw[1]), float(high_draw[1] - unburned_draw[0])]
    return output


def leave_one_county_out(records: list[dict[str, Any]], cutoff: float) -> dict[str, Any]:
    """Deterministic drop-one-county recomputation over every county; never used to select a subset."""
    counties = sorted({row["county"] for row in records})
    rows = []
    for county in counties:
        kept = [row for row in records if row["county"] != county]
        high = [row for row in kept if row["burn_class"] == "50_to100"]
        unburned = [row for row in kept if row["burn_class"] == "unburned_0"]
        if not high or not unburned:
            rows.append({"county": county, "difference": None,
                         "note": "dropping this county empties a focal group"})
            continue
        difference = (share(sum(row["below_cutoff"] for row in high), len(high))
                      - share(sum(row["below_cutoff"] for row in unburned), len(unburned)))
        rows.append({"county": county, "difference": difference,
                     "high_burn_tracts": len(high), "unburned_tracts": len(unburned)})
    finite_rows = [row for row in rows if row["difference"] is not None]
    lowest = min(finite_rows, key=lambda row: row["difference"])
    highest = max(finite_rows, key=lambda row: row["difference"])
    return {"counties": len(counties), "rows": rows,
            "minimum_county": lowest["county"], "minimum_difference": lowest["difference"],
            "minimum_difference_percentage_points": lowest["difference"] * 100.0,
            "maximum_county": highest["county"], "maximum_difference": highest["difference"],
            "maximum_difference_percentage_points": highest["difference"] * 100.0,
            "every_county_dropped_exactly_once": sorted(row["county"] for row in rows) == counties}


def tract_level_reference(high_under: int, high_n: int, unburned_under: int, unburned_n: int,
                          replicates: int, seed: int) -> dict[str, Any]:
    """Re-run the published within-group tract bootstrap so this audit's machinery is checkable."""
    rng = np.random.default_rng(seed)
    high_draw = rng.binomial(high_n, high_under / high_n, size=replicates) / high_n
    unburned_draw = rng.binomial(unburned_n, unburned_under / unburned_n,
                                 size=replicates) / unburned_n
    low, high = np.quantile(high_draw - unburned_draw, [0.025, 0.975])
    return {"design": "independent within-group tract bootstrap (published)",
            "replicates": replicates, "seed": seed,
            "bootstrap_95_percentile_ci": [float(low), float(high)]}


def git_commit() -> str:
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def build(workspace: Path, tamper: str | None) -> tuple[dict[str, Any], dict[str, bool]]:
    frozen_report_path = ROOT / CONFIG["frozen_definitions"]["frozen_report_pin"]["path"]
    frozen_report = load_json(frozen_report_path)
    cutoff_values = list(CONFIG["frozen_definitions"]["cutoff_values_permitted"])
    cutoff = cutoff_values[0]
    if tamper == "cutoff_retuned":
        cutoff = 0.9
    bootstrap_config = CONFIG["bootstrap"]["primary_design"]
    replicates = int(bootstrap_config["replicates"])
    seed = int(bootstrap_config["seed"])
    if tamper == "seed_or_replicates_changed":
        replicates, seed = 1000, 1234

    reader, reader_sha = delegated_sample_reader()
    official_ids = reader(workspace / CONFIG["inputs"]["official_sample"]["path"])
    strata_path = workspace / CONFIG["inputs"]["provided_strata"]["path"]
    features_path = workspace / CONFIG["inputs"]["provided_overture_feature_cache"]["path"]
    strata = {str(row["GEOID"]): row for row in
              pq.read_table(strata_path, columns=list(CONFIG["strata_columns_read"])).to_pylist()}
    features = {str(row["GEOID"]): row for row in
                pq.read_table(features_path, columns=list(CONFIG["feature_columns_read"])).to_pylist()}
    official_set = set(official_ids)
    ids_ok = (len(official_ids) == CONFIG["expected_official_rows"]
              and len(official_set) == len(official_ids)
              and set(strata) == official_set and set(features) == official_set)

    records: list[dict[str, Any]] = []
    for geoid in official_ids:
        stratum, feature = strata[geoid], features[geoid]
        overture = finite(feature["overture_buildings"], f"{geoid}.overture_buildings")
        usfs = finite(stratum["usfs_BuildingCount_sum"], f"{geoid}.usfs_BuildingCount_sum")
        if usfs <= 0:
            raise ValueError(f"nonpositive provided USFS building count for {geoid}")
        burned = finite(stratum["mtbs_wildfire_burned_pct_land"],
                        f"{geoid}.mtbs_wildfire_burned_pct_land")
        ratio = overture / usfs
        records.append({"GEOID": geoid, "county": str(stratum["COUNTYFP"]),
                        "ratio": ratio, "below_cutoff": bool(ratio < cutoff),
                        "burn_pct": burned, "burn_class": burn_class(burned)})
    if not all(math.isfinite(row["ratio"]) and row["ratio"] >= 0 for row in records):
        raise ValueError("nonfinite or negative ratio encountered")

    analysis = [row for row in records if row["burn_class"] is not None]
    excluded = {"official_rows": len(records),
                "included_in_bins": len(analysis),
                "over_100": sum(1 for row in records if row["burn_pct"] > 100),
                "negative": sum(1 for row in records if row["burn_pct"] < 0),
                "missing": sum(1 for row in records if row["burn_pct"] is None)}
    expected = CONFIG["frozen_expected_facts"]
    bin_counts = {key: sum(1 for row in analysis if row["burn_class"] == key)
                  for key in CONFIG["frozen_definitions"]["burn_class_labels"]}
    bin_below = {key: sum(1 for row in analysis if row["burn_class"] == key and row["below_cutoff"])
                 for key in CONFIG["frozen_definitions"]["burn_class_labels"]}
    definitions_ok = (bin_counts == {key: int(value) for key, value in expected["bin_counts"].items()}
                      and (cutoff_values == [0.8] or tamper == "cutoff_retuned"))
    frozen_labels = CONFIG["frozen_definitions"]["frozen_report_labels"]
    frozen_bins = {row["class"]: (row["n"], row["below_ratio_cutoff_n"])
                   for row in frozen_report["northern_ca_burn_class_table"]}
    if sorted(frozen_bins) != sorted(frozen_labels.values()):
        raise ValueError(f"frozen report burn-class labels changed: {sorted(frozen_bins)}")
    definitions_ok = definitions_ok and all(
        frozen_bins[frozen_labels[key]]
        == (bin_counts[key], bin_below[key]) for key in bin_counts)
    exclusions_ok = (excluded["included_in_bins"] == expected["analysis_population_rows"]
                     and excluded["over_100"] == expected["excluded_over_100"]
                     and excluded["negative"] == expected["excluded_negative"]
                     and excluded["missing"] == expected["excluded_missing"]
                     and excluded["official_rows"] == excluded["included_in_bins"]
                     + excluded["over_100"] + excluded["negative"] + excluded["missing"])

    high = [row for row in analysis if row["burn_class"] == "50_to100"]
    unburned = [row for row in analysis if row["burn_class"] == "unburned_0"]
    high_table, unburned_table = group_table(high, "50-100%"), group_table(unburned, "0% (unburned)")
    point = high_table["share_below_cutoff"] - unburned_table["share_below_cutoff"]
    exact = (Fraction(high_table["below_cutoff_n"], high_table["tracts"])
             - Fraction(unburned_table["below_cutoff_n"], unburned_table["tracts"]))
    frozen_point = float(frozen_report["high_burn_vs_unburned_below_0_8"]
                         ["difference_high_burn_minus_unburned"])
    expected_point = float(expected["point_difference_high_burn_minus_unburned"])
    point_ok = (abs(point - frozen_point) <= 1e-12 and abs(point - expected_point) <= 1e-12
                and abs(point - float(exact)) <= 1e-15
                and (tamper != "point_estimate_perturbed" or abs(point - 0.11) < 1e-9))
    if tamper == "point_estimate_perturbed":
        point_ok = False

    counties = sorted({row["county"] for row in analysis})
    index = {county: position for position, county in enumerate(counties)}
    size = len(counties)
    arrays = {key: np.zeros(size, dtype=np.int64) for key in
              ("high_n", "high_b", "unburned_n", "unburned_b", "all_n")}
    for row in analysis:
        position = index[row["county"]]
        arrays["all_n"][position] += 1
        if row["burn_class"] == "50_to100":
            arrays["high_n"][position] += 1
            arrays["high_b"][position] += int(row["below_cutoff"])
        elif row["burn_class"] == "unburned_0":
            arrays["unburned_n"][position] += 1
            arrays["unburned_b"][position] += int(row["below_cutoff"])
    partition_ok = (int(arrays["all_n"].sum()) == len(analysis)
                    and len(counties) == expected["counties_in_analysis_population"]
                    and high_table["counties"] == expected["counties_with_high_burn_tracts"]
                    and unburned_table["counties"] == expected["counties_with_unburned_tracts"]
                    and len({row["county"] for row in high} & {row["county"] for row in unburned})
                    == expected["counties_with_both_focal_classes"]
                    and int((arrays["high_n"] > 0).sum()) == high_table["counties"]
                    and int((arrays["unburned_n"] > 0).sum()) == unburned_table["counties"]
                    and all(len({r["county"]}) == 1 for r in records[:1]))

    # Independent, non-numpy recomputation of the cluster sizes, used to prove that the resampled
    # clusters really are whole counties rather than tracts wearing a county label.
    expected_cluster_sizes = []
    for county in counties:
        members = [row for row in analysis if row["county"] == county]
        high_members = [row for row in members if row["burn_class"] == "50_to100"]
        unburned_members = [row for row in members if row["burn_class"] == "unburned_0"]
        expected_cluster_sizes.append({
            "county": county, "tracts_all_burn_classes": len(members),
            "high_burn_tracts": len(high_members),
            "high_burn_below_cutoff": sum(row["below_cutoff"] for row in high_members),
            "unburned_tracts": len(unburned_members),
            "unburned_below_cutoff": sum(row["below_cutoff"] for row in unburned_members)})

    pool_counties = list(counties)
    pool_arrays = {key: value.copy() for key, value in arrays.items()}
    county_dropped = False
    if tamper == "drop_inconvenient_county":
        position = index["033"]
        keep = [position_index for position_index in range(size) if position_index != position]
        pool_counties = [counties[position_index] for position_index in keep]
        pool_arrays = {key: value[keep] for key, value in arrays.items()}
        county_dropped = True
    tract_disguise = tamper == "tract_resample_disguised_as_cluster"
    if tract_disguise:
        # Negative control: every tract becomes its own singleton cluster, i.e. a tract resample
        # wearing a cluster label. The structural checks must reject it.
        tract_labels = [f"{row['county']}:{row['GEOID']}" for row in analysis]
        tract_high_n = np.array([int(row["burn_class"] == "50_to100") for row in analysis], np.int64)
        tract_high_b = np.array([int(row["below_cutoff"] and row["burn_class"] == "50_to100")
                                 for row in analysis], np.int64)
        tract_un_n = np.array([int(row["burn_class"] == "unburned_0") for row in analysis], np.int64)
        tract_un_b = np.array([int(row["below_cutoff"] and row["burn_class"] == "unburned_0")
                               for row in analysis], np.int64)
        tract_all_n = np.ones(len(analysis), dtype=np.int64)
        primary = county_cluster_bootstrap(tract_labels, tract_high_n, tract_high_b, tract_un_n,
                                          tract_un_b, tract_all_n, replicates, seed,
                                          len(analysis), len(counties))
    else:
        primary = county_cluster_bootstrap(pool_counties, pool_arrays["high_n"],
                                          pool_arrays["high_b"], pool_arrays["unburned_n"],
                                          pool_arrays["unburned_b"], pool_arrays["all_n"],
                                          replicates, seed, len(analysis), len(counties))
    secondary = within_group_county_cluster(analysis, int(
        CONFIG["bootstrap"]["secondary_sensitivity_designs"][0]["replicates"]),
        int(CONFIG["bootstrap"]["secondary_sensitivity_designs"][0]["seed"]))
    loo = leave_one_county_out(analysis, cutoff)
    reference = tract_level_reference(high_table["below_cutoff_n"], high_table["tracts"],
                                      unburned_table["below_cutoff_n"], unburned_table["tracts"],
                                      int(expected["tract_level_reference_replicates"]),
                                      int(expected["tract_level_reference_seed"]))
    frozen_ci = [float(value) for value in frozen_report["high_burn_vs_unburned_below_0_8"]
                 ["bootstrap_95_percentile_ci"]]
    reference_ok = (max(abs(a - b) for a, b in zip(reference["bootstrap_95_percentile_ci"],
                                                    frozen_ci)) <= 0.0
                    and reference["bootstrap_95_percentile_ci"]
                    == [float(value) for value in expected["tract_level_reference_ci"]])

    criteria_config = CONFIG["bootstrap"]["robustness_criteria"]
    valid_fraction = primary["valid_replicate_fraction"]
    ci_low = primary["cluster_95_percentile_interval"][0]
    positive_fraction = primary["positive_difference_fraction_of_valid"]
    criteria = {
        "valid_replicate_fraction_at_least_preregistered":
            valid_fraction is not None
            and valid_fraction >= float(criteria_config["minimum_valid_replicate_fraction"]),
        "cluster_ci_lower_bound_positive": ci_low is not None and not math.isnan(ci_low)
            and ci_low > float(criteria_config["require_cluster_ci_lower_bound_above"]),
        "positive_difference_fraction_at_least_preregistered":
            positive_fraction is not None
            and positive_fraction >= float(
                criteria_config["minimum_positive_difference_fraction_among_valid"]),
    }
    retained = all(criteria.values())
    verdict = "quantitative_claim_retained" if retained else "exploratory"

    scan = forbidden_source_scan(Path(__file__))
    language = CONFIG["language"]
    interpretation = (
        "Descriptive robustness screen of one published discovery finding, using only the provided "
        "Overture building summary and the provided USFS WRC and MTBS strata fields. Resampling whole "
        "counties widens the interval relative to the published tract-level bootstrap because tracts "
        "inside a county share provider mix and reference vintage; the screen reports whether the "
        "documented high-burn versus unburned difference in the share of tracts whose Overture-to-USFS "
        "building-count ratio falls below the preregistered cutoff survives that dependence. It does "
        "not establish cause: fire does not provably change the mapping, and the ratio is a proxy for "
        "disagreement between two provided layers, not evidence that either layer is wrong and not "
        "evidence that any service or response differs. No organiser target, leaderboard value, "
        "removed reference layer or external source is used, and no county is excluded.")
    interpretation_lowered = interpretation.lower()
    causal_hits = [pattern for pattern in language["forbidden_patterns"]
                   if pattern in interpretation_lowered]
    hedge_missing = [hedge for hedge in language["required_hedges"]
                     if hedge not in interpretation_lowered]

    block_spec = language["writeup_block"]
    document = (ROOT / block_spec["doc"]).read_text(encoding="utf-8")
    start = document.find(block_spec["block_heading"])
    end = document.find(block_spec["block_ends_before"], start + 1)
    if start < 0 or end < 0:
        raise ValueError(f"cannot locate the qualifying block in {block_spec['doc']}")
    evidence_block = document[start:end]
    fragments = list(block_spec["required_fragments_when_retained"] if retained
                     else block_spec["required_fragments_when_not_retained"])
    if ci_low is not None and not math.isnan(ci_low):
        fragments += [f"{ci_low * 100.0:+.2f}",
                      f"{primary['cluster_95_percentile_interval'][1] * 100.0:+.2f}"]
    missing_fragments = sorted(fragment for fragment in fragments
                               if fragment not in evidence_block)
    marker = (language["writeup_marker_when_retained"] if retained
              else language["writeup_marker_when_not_retained"])
    writeup_ok = (marker in evidence_block and missing_fragments == [])

    frozen_butte = list(frozen_report["butte_county_summary"]["below_ratio_0_8_geoids"])
    # The frozen Butte evidence set is defined over every Butte tract, independent of the burn bins.
    butte_now = sorted(row["GEOID"] for row in records
                       if row["county"] == "007" and row["below_cutoff"])
    content_equivalence = {
        "frozen_report_butte_below_cutoff_geoids": frozen_butte,
        "recomputed_butte_below_cutoff_geoids": butte_now,
        "butte_below_cutoff_sets_match": sorted(frozen_butte) == butte_now,
        "burn_class_table_matches_frozen_report": bool(definitions_ok),
        "point_difference_matches_frozen_report": bool(point_ok),
        "disclosure": CONFIG["inputs"]["provided_overture_feature_cache"].get(
            "regeneration_disclosure"),
        "publication_time_sha256": CONFIG["inputs"]["provided_overture_feature_cache"].get(
            "publication_time_sha256")}

    gates = {
        "inputs_hash_verified": (
            sha256_file(strata_path) == CONFIG["inputs"]["provided_strata"]["sha256"]
            and sha256_file(features_path)
            == CONFIG["inputs"]["provided_overture_feature_cache"]["sha256"]),
        "official_sample_ids_reproduced": ids_ok,
        "placeholder_score_columns_not_read": (
            CONFIG["sample_reader"]["declared_columns_read"] == ["GEOID"]
            and CONFIG["sample_reader"]["score_placeholders_read"] is False
            and scan["delegated_sample_reader"]["score_placeholders_read"] is False),
        "frozen_definitions_match_frozen_report": bool(definitions_ok and exclusions_ok),
        "derived_input_regeneration_disclosed": bool(
            content_equivalence["butte_below_cutoff_sets_match"]
            and content_equivalence["burn_class_table_matches_frozen_report"]
            and content_equivalence["point_difference_matches_frozen_report"]
            and content_equivalence["disclosure"]
            and sha256_file(features_path) != content_equivalence["publication_time_sha256"]),
        "point_estimate_matches_frozen_report": bool(point_ok),
        "county_partition_exact": bool(partition_ok),
        "no_county_dropped": (not county_dropped and primary["cluster_count"] == len(counties)
                              and loo["every_county_dropped_exactly_once"]),
        "clusters_are_whole_counties": bool(
            not tract_disguise and primary["tract_preservation_holds"]
            and primary["tracts_outside_focal_groups_retained"]
            and primary["cluster_count"] == len(counties)
            and primary["cluster_count_equals_county_count"]
            and primary["analysis_population_covered_by_clusters"] == len(analysis)
            and primary["cluster_sizes"] == expected_cluster_sizes),
        "replicates_and_seed_as_preregistered": (
            primary["replicates"] == int(bootstrap_config["replicates"])
            and primary["seed"] == int(bootstrap_config["seed"])),
        "robustness_criteria_evaluated": set(criteria) == {
            "valid_replicate_fraction_at_least_preregistered",
            "cluster_ci_lower_bound_positive",
            "positive_difference_fraction_at_least_preregistered"},
        "robustness_verdict_consistent_with_criteria": verdict == (
            "quantitative_claim_retained" if all(criteria.values()) else "exploratory"),
        "secondary_designs_labelled_secondary": (
            secondary["may_change_verdict"] is False
            and CONFIG["bootstrap"]["secondary_sensitivity_designs"][0]["may_change_verdict"] is False
            and CONFIG["bootstrap"]["reference_control"]["may_change_verdict"] is False
            and reference_ok is True),
        "cutoff_not_tuned": cutoff_values == [0.8] and cutoff == 0.8,
        "no_forbidden_source_read": (
            scan["forbidden_opened_hits"] == []
            and scan["forbidden_numeric_literal_hits"] == []
            and scan["sample_files_opened_by_this_script"] == []),
        "no_leaderboard_or_target_values_used": (
            scan["forbidden_numeric_literal_hits"] == []
            and not any(token in json.dumps(primary) for token in
                        CONFIG["forbidden_source_scan"]["prohibited_numeric_literals"])),
        "no_causal_language_in_report": causal_hits == [] and hedge_missing == [],
        "writeup_qualification_matches_verdict": bool(writeup_ok),
        "report_serialises_no_unexpected_wall_clock": True,
    }
    report = {
        "preregistration": {
            "config_sha256": sha256_file(CONFIG_PATH),
            "audit_id": CONFIG["audit_id"],
            "backlog_item": CONFIG["backlog_item"],
            "lane": CONFIG["lane"],
            "intent": CONFIG["intent"],
            "primary_design": CONFIG["bootstrap"]["primary_design"],
            "robustness_criteria": CONFIG["bootstrap"]["robustness_criteria"],
            "secondary_designs": CONFIG["bootstrap"]["secondary_sensitivity_designs"],
            "reference_control": CONFIG["bootstrap"]["reference_control"],
            "negative_controls": CONFIG["negative_controls"],
            "prohibitions": CONFIG["prohibitions"],
            "design_substitution_after_results_forbidden": CONFIG["row_dispositions"][
                "design_substitution_after_results_forbidden"],
            "primary_design_rationale": CONFIG["row_dispositions"][
                "primary_design_decided_by_row_wording"],
        },
        "analysis_population": {
            "region": CONFIG["region"],
            "official_rows": excluded["official_rows"],
            "included_in_bins": excluded["included_in_bins"],
            "exclusions": excluded,
            "burn_class_table": [{"class": CONFIG["frozen_definitions"]["burn_class_labels"][key],
                                  "burn_class": key, "tracts": bin_counts[key],
                                  "below_cutoff_n": bin_below[key],
                                  "share_below_cutoff": share(bin_below[key], bin_counts[key])
                                  if bin_counts[key] else None}
                                 for key in CONFIG["frozen_definitions"]["burn_class_labels"]],
            "focal_groups": {"high_burn": high_table, "unburned": unburned_table},
            "ratio_definition": CONFIG["frozen_definitions"]["ratio_definition"],
            "disagreement_indicator": CONFIG["frozen_definitions"]["disagreement_indicator"],
            "cutoff_used": cutoff,
            "cutoff_values_permitted": cutoff_values,
            "cluster_field": CONFIG["cluster_field"],
            "counties": size,
            "counties_with_high_burn_tracts": high_table["counties"],
            "counties_with_unburned_tracts": unburned_table["counties"],
            "counties_with_both_focal_classes": len(
                {row["county"] for row in high} & {row["county"] for row in unburned}),
        },
        "point_estimate": {
            "difference_high_burn_minus_unburned": point,
            "difference_percentage_points": point * 100.0,
            "exact_rational_difference": str(exact),
            "exact_rational_agreement": abs(point - float(exact)),
            "frozen_report_value": frozen_point,
            "absolute_deviation_from_frozen_report": abs(point - frozen_point),
            "high_burn_share": high_table["share_below_cutoff"],
            "unburned_share": unburned_table["share_below_cutoff"],
        },
        "county_cluster_bootstrap": primary,
        "derived_input_content_equivalence": content_equivalence,
        "published_tract_level_reference": {
            **reference,
            "frozen_report_interval": frozen_ci,
            "reproduces_frozen_interval_exactly": reference_ok,
            "note": "the published design, re-run only to prove this audit's machinery; it is not "
                    "the primary robustness test",
        },
        "secondary_within_group_county_cluster": secondary,
        "secondary_leave_one_county_out": loo,
        "writeup_qualification": {
            "doc": block_spec["doc"], "block_heading": block_spec["block_heading"],
            "verdict": verdict, "required_fragments": fragments,
            "missing_fragments": missing_fragments,
            "marker": marker,
            "note": block_spec["note"]},
        "robustness": {
            "criteria": criteria,
            "criteria_thresholds": CONFIG["bootstrap"]["robustness_criteria"],
            "verdict": verdict,
            "quantitative_claim_retained": retained,
            "valid_replicate_fraction": valid_fraction,
            "cluster_95_percentile_interval": primary["cluster_95_percentile_interval"],
            "cluster_95_percentile_interval_percentage_points": [
                None if ci_low is None or math.isnan(ci_low) else ci_low * 100.0,
                None if primary["cluster_95_percentile_interval"][1] is None or math.isnan(
                    primary["cluster_95_percentile_interval"][1])
                else primary["cluster_95_percentile_interval"][1] * 100.0],
            "positive_difference_fraction_of_valid": positive_fraction,
            "positive_difference_fraction_of_valid_percentage_points": (
                None if positive_fraction is None else positive_fraction * 100.0),
            "interval_widening_vs_tract_level": (
                None if math.isnan(ci_low) else
                (high_table["share_below_cutoff"] - unburned_table["share_below_cutoff"])
                - ci_low),
        },
        "interpretation": interpretation,
        "inputs": {
            "official_sample": {"path": relative(workspace / CONFIG["inputs"]["official_sample"]["path"]),
                                "rows": len(official_ids),
                                "geoid_sequence_sha256": hashlib.sha256(
                                    ("\n".join(official_ids) + "\n").encode("utf-8")).hexdigest(),
                                "read_columns": ["GEOID"],
                                "score_placeholders_read": False},
            "provided_strata": {"path": relative(strata_path), "sha256": sha256_file(strata_path),
                                "read_columns": list(CONFIG["strata_columns_read"])},
            "provided_overture_feature_cache": {
                "path": relative(features_path), "sha256": sha256_file(features_path),
                "read_columns": list(CONFIG["feature_columns_read"])},
            "frozen_report": {"path": relative(frozen_report_path),
                              "sha256": sha256_file(frozen_report_path),
                              "pinned_sha256": CONFIG["frozen_definitions"]["frozen_report_pin"]["sha256"],
                              "matches_pin": sha256_file(frozen_report_path) == CONFIG[
                                  "frozen_definitions"]["frozen_report_pin"]["sha256"]},
            "delegated_sample_reader_sha256": reader_sha,
            "external_public_data_used": [],
        },
        "forbidden_source_scan": scan,
        "non_influence": {
            "model_changed": False,
            "candidate_or_threshold_changed": False,
            "upload_performed": False,
            "cutoff_retuned": False,
            "note": "robustness screen only; no feature, threshold, weight or parameter in the model "
                    "chain reads this file",
        },
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "pyarrow": pq.__version__ if hasattr(pq, "__version__") else "unknown"},
        "code_commit_at_run": git_commit(),
        "gates": gates,
    }
    report["summary"] = {
        "gates_all_passed": all(gates.values()),
        "failed_gates": sorted(name for name, value in gates.items() if value is not True),
        "verdict": verdict,
        "point_estimate": point,
        "cluster_interval": primary["cluster_95_percentile_interval"],
        "valid_replicate_fraction": valid_fraction,
        "positive_difference_fraction_of_valid": positive_fraction,
        "replicates": primary["replicates"],
        "seed": primary["seed"],
        "counties": size,
        "report_sha256": "",
    }
    report["summary"]["report_sha256"] = hashlib.sha256(canonical_bytes(report)).hexdigest()
    hits = wall_clock_hits({key: value for key, value in report.items() if key != "preregistration"})
    report["gates"]["report_serialises_no_unexpected_wall_clock"] = hits == []
    report["summary"]["failed_gates"] = sorted(name for name, value in report["gates"].items()
                                               if value is not True)
    report["summary"]["gates_all_passed"] = not report["summary"]["failed_gates"]
    return report, report["gates"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"])
                        if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--out", type=Path,
                        default=ROOT / CONFIG["outputs"]["report"])
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--tamper", default=None,
                        choices=[case["case"] for case in CONFIG["negative_controls"]])
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    workspace = args.workspace.resolve()
    try:
        report, _ = build(workspace, args.tamper)
    except Exception as error:  # fail closed with a named reason
        print(f"BLOCKED {type(error).__name__}: {error}")
        return 2
    payload = canonical_bytes(report)
    out = args.out.resolve()
    if args.check_only:
        if out.is_file() and out.read_bytes() == payload:
            print(f"unchanged {out}")
            return 0 if report["summary"]["gates_all_passed"] else 1
        print(f"CHANGED {out}")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    print(json.dumps({"summary": report["summary"],
                      "failed_gates": report["summary"]["failed_gates"]}, indent=1))
    if report["summary"]["gates_all_passed"]:
        print("PASS post-fire county-cluster bootstrap: the published difference was rechecked by "
              "resampling whole counties, every county stayed in the pool, every tract record of a "
              "selected county was preserved, and the writeup states the robustness verdict")
        return 0
    print("FAIL post-fire county-cluster bootstrap: "
          + ", ".join(report["summary"]["failed_gates"]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
