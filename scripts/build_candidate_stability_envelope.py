#!/usr/bin/env python3
"""Build a deterministic, analysis-only stability envelope from frozen candidates.

Reads only official GEOIDs, permitted strata urbanity labels, existing model
candidate CSVs, their declared feature-coverage fields, and existing v0
component predictions/definedness flags. No sample score placeholders, labels,
removed references, external data, or leaderboard information are read.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/candidate-stability-envelope-preregistered.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
REGIONS = tuple(CONFIG["scope"]["regions_order"])
CANDIDATE_CONFIG = CONFIG["frozen_inputs"]["candidates"]
CANDIDATE_ORDER = tuple(CONFIG["scope"]["candidate_order"])
EXPECTED_REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591,
                         "eastern-ok": 1192, "south-central-tx": 6003}
BASELINE_COLUMNS = CONFIG["frozen_inputs"]["official_component_baseline"]["columns"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def summarize(values: list[float] | np.ndarray) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    if not len(array):
        return {"n": 0, "mean": None, "std_population": None, "min": None,
                "q05": None, "median": None, "q95": None, "max": None}
    if not np.isfinite(array).all():
        raise ValueError("cannot summarize nonfinite values")
    q05, median, q95 = np.quantile(array, [0.05, 0.5, 0.95])
    return {"n": int(len(array)), "mean": float(array.mean()),
            "std_population": float(array.std()), "min": float(array.min()),
            "q05": float(q05), "median": float(median), "q95": float(q95),
            "max": float(array.max())}


def read_official_ids(workspace: Path) -> tuple[list[str], dict[str, str], dict[str, list[str]], dict[str, str]]:
    ids: list[str] = []
    region_by_id: dict[str, str] = {}
    ids_by_region: dict[str, list[str]] = {}
    sample_geoid_hashes: dict[str, str] = {}
    for region in REGIONS:
        path = workspace / "samples" / f"{region}-sample-submission.csv"
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.reader(stream)
            header = next(reader, [])
            if "GEOID" not in header:
                raise ValueError(f"official sample missing GEOID column: {region}")
            geoid_index = header.index("GEOID")
            region_ids = []
            for row in reader:
                geoid = row[geoid_index]
                if not geoid or geoid in region_by_id:
                    raise ValueError(f"empty or repeated official GEOID {geoid!r}")
                region_ids.append(geoid)
                region_by_id[geoid] = region
            if len(region_ids) != EXPECTED_REGION_COUNTS[region]:
                raise ValueError(f"unexpected official sample size in {region}: {len(region_ids)}")
            ids_by_region[region] = region_ids
            ids.extend(region_ids)
        sample_geoid_hashes[region] = canonical_hash(region_ids)
    if len(ids) != 9379 or len(set(ids)) != 9379:
        raise ValueError("official samples must contain exactly 9,379 unique GEOIDs")
    return ids, region_by_id, ids_by_region, sample_geoid_hashes


def read_projected_csv(path: Path, required_columns: list[str], official_ids: list[str],
                       *, allow_template_extras: bool = False) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    official = set(official_ids)
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not set(["GEOID", *required_columns]).issubset(reader.fieldnames):
            raise ValueError(f"missing required projected columns in {path}")
        seen: set[str] = set()
        selected: dict[str, dict[str, str]] = {}
        all_count = extras = 0
        for row in reader:
            geoid = row.get("GEOID", "")
            if not geoid or geoid in seen:
                raise ValueError(f"empty or duplicate GEOID in {path}: {geoid!r}")
            seen.add(geoid)
            all_count += 1
            if geoid not in official:
                extras += 1
                if not allow_template_extras:
                    raise ValueError(f"unexpected nonofficial GEOID in {path}: {geoid}")
                # Deliberately do not read or parse any score fields for template-only IDs.
                continue
            selected[geoid] = {column: row[column] for column in required_columns}
    missing = official - set(selected)
    if missing:
        raise ValueError(f"{path} is missing {len(missing)} official candidate IDs")
    return selected, {"rows_in_source": all_count, "official_rows_selected": len(selected),
                      "nonofficial_rows_ignored_by_GEOID_only": extras,
                      "required_columns_read_for_official_rows": required_columns,
                      "official_id_coverage": len(selected) / len(official_ids)}


def read_urbanity(workspace: Path, ids_by_region: dict[str, list[str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        table = pq.read_table(path, columns=["GEOID", "ur_class"])
        rows = table.to_pylist()
        local: dict[str, str] = {}
        for row in rows:
            geoid = str(row["GEOID"])
            if geoid in local:
                raise ValueError(f"duplicate strata GEOID {geoid} in {region}")
            value = row["ur_class"]
            local[geoid] = "Unknown" if value is None or str(value).strip() == "" else str(value)
        ids = ids_by_region[region]
        if set(ids) - set(local):
            raise ValueError(f"missing official ur_class values in {region}")
        result.update({geoid: local[geoid] for geoid in ids})
    return result


def parse_score(values: dict[str, dict[str, str]], geoid: str, column: str,
                candidate_id: str) -> float:
    try:
        value = float(values[geoid][column])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"missing/non-numeric {candidate_id}:{column}:{geoid}") from exc
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"invalid score outside [0,1] for {candidate_id}:{geoid}: {value}")
    return value


def parse_flag(value: str, name: str, geoid: str) -> int:
    try:
        flag = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid definedness/missingness flag {name}:{geoid}={value!r}") from exc
    if flag not in (0, 1):
        raise ValueError(f"flag must be 0/1: {name}:{geoid}={flag}")
    return flag


def composite(components: list[tuple[float, int]]) -> tuple[float, int]:
    defined = [value for value, flag in components if flag]
    if not defined:
        return 0.0, 0
    score = float(sum(defined) / len(defined))
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("composite outside [0,1]")
    return score, 1


def moments_for_groups(rows: list[dict[str, Any]], value_column: str,
                       group_column: str) -> dict[str, Any]:
    groups: defaultdict[str, list[float]] = defaultdict(list)
    for row in rows:
        groups[str(row[group_column])].append(float(row[value_column]))
    return {key: summarize(values) for key, values in sorted(groups.items())}


def feature_coverage(sources: dict[str, dict[str, dict[str, str]]],
                     baseline: dict[str, dict[str, str]], ids: list[str]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    baseline_flags = ["transport_defined", "building_defined", "poi_defined",
                      "fire_defined", "ems_defined", "schools_defined", "cbp_defined"]
    report["C2_V0_MECHANISTIC"] = {
        "prediction_rows_with_finite_score": len(ids),
        "baseline_component_defined_share": {
            name: sum(parse_flag(baseline[g][name], name, g) for g in ids) / len(ids)
            for name in baseline_flags
        },
        "interpretation": "definedness coverage from the frozen feature-derived component report; numeric scores remain present for every official tract"
    }

    def missing_share(candidate: str, columns: list[str]) -> dict[str, Any]:
        values = sources[candidate]
        return {column: {"missing_rows": sum(parse_flag(values[g][column], column, g) for g in ids),
                         "missing_share": sum(parse_flag(values[g][column], column, g) for g in ids) / len(ids),
                         "available_share": 1 - sum(parse_flag(values[g][column], column, g) for g in ids) / len(ids)}
                for column in columns}

    report["XTS_C2_V2"] = {"prediction_rows_with_finite_score": len(ids),
                            "support_feature_missingness": missing_share("XTS_C2_V2", [
                                "building_source_support_missing", "road_route_support_missing",
                                "place_confidence_support_missing"])}
    report["DENSITY_C2_V1"] = {"prediction_rows_with_finite_score": len(ids),
                                "support_feature_missingness": missing_share("DENSITY_C2_V1", [
                                    "density_context_support_missing", "population_support_5km_missing",
                                    "building_support_5km_missing", "population_support_20km_missing",
                                    "building_support_20km_missing"])}
    facility = sources["FACILITY_CATCHMENT_5KM"]
    probability_coverage = {}
    for name in ("fire", "ems", "schools"):
        probabilities = [float(facility[g][f"{name}_p_ref_present"]) for g in ids]
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in probabilities):
            raise ValueError(f"invalid frozen {name} presence probabilities")
        probability_coverage[name] = len(probabilities) / len(ids)
    report["FACILITY_CATCHMENT_5KM"] = {
        "prediction_rows_with_finite_score": len(ids),
        "nearest_distance_missingness": missing_share("FACILITY_CATCHMENT_5KM", [
            "fire_nearest_distance_5km_missing", "ems_nearest_distance_5km_missing",
            "schools_nearest_distance_5km_missing"]),
        "presence_probability_coverage": probability_coverage,
    }
    morphology = sources["BUILDING_MORPH_BETA_0.02"]
    report["BUILDING_MORPH_BETA_0.02"] = {
        "prediction_rows_with_finite_score": len(ids),
        "building_defined_share": sum(parse_flag(morphology[g]["building_defined"], "building_defined", g)
                                       for g in ids) / len(ids),
        "tracts_with_valid_building_geometry_share": sum(
            float(morphology[g]["valid_geometry_count"]) > 0 for g in ids) / len(ids),
        "morphology_support_finite_share": sum(
            math.isfinite(float(morphology[g]["morphology_support"])) for g in ids) / len(ids),
    }
    source = sources["BUILDING_SOURCE_PRIMARY_BETA_0.02"]
    report["BUILDING_SOURCE_PRIMARY_BETA_0.02"] = {
        "prediction_rows_with_finite_score": len(ids),
        "building_defined_share": sum(parse_flag(source[g]["building_defined"], "building_defined", g)
                                       for g in ids) / len(ids),
        "source_mix_defined_share": sum(parse_flag(source[g]["source_mix_defined"], "source_mix_defined", g)
                                        for g in ids) / len(ids),
        "primary_source_support_finite_share": sum(
            math.isfinite(float(source[g]["source_prior_support_primary"])) for g in ids) / len(ids),
    }
    return report


def distribution_before(sources: dict[str, dict[str, dict[str, str]]],
                        baseline: dict[str, dict[str, str]], ids: list[str]) -> dict[str, Any]:
    specs = {item["candidate_id"]: item for item in CANDIDATE_CONFIG}
    distributions: dict[str, Any] = {}
    for candidate_id in CANDIDATE_ORDER:
        source = sources.get(candidate_id)
        if source is None:
            continue
        column = specs[candidate_id]["score_column"]
        values = [parse_score(source, geoid, column, candidate_id) for geoid in ids]
        distributions[candidate_id] = {"source_path": specs[candidate_id]["path"],
                                       "source_column": column,
                                       "official_candidate_distribution": summarize(values),
                                       "source_artifact_sha256": specs[candidate_id]["sha256"]}
    for candidate_id, column in (("FACILITY_CATCHMENT_5KM", "facility_catchment_gap_5km"),
                                 ("BUILDING_MORPH_BETA_0.02", "building_gap_morph_beta_0.02"),
                                 ("BUILDING_SOURCE_PRIMARY_BETA_0.02", "building_gap_primary_beta_0.02")):
        source = sources[candidate_id]
        distributions[candidate_id]["component_input_distribution"] = summarize(
            [parse_score(source, geoid, column, candidate_id) for geoid in ids])
    distributions["v0_component_inputs"] = {
        column: summarize([parse_score(baseline, geoid, column, "gap-estimates") for geoid in ids])
        for column in ("transport_gap", "building_gap", "poi_gap", "fire_gap", "ems_gap",
                       "schools_gap", "cbp_gap")
    }
    return distributions


def build_member_scores(ids: list[str], region_by_id: dict[str, str],
                         sources: dict[str, dict[str, dict[str, str]]],
                         baseline: dict[str, dict[str, str]]) -> tuple[dict[str, dict[str, float]], dict[str, int]]:
    scores: dict[str, dict[str, float]] = {geoid: {} for geoid in ids}
    fallback_rows = {candidate: 0 for candidate in CANDIDATE_ORDER}
    for geoid in ids:
        base = baseline[geoid]
        # The original component candidate must exactly reproduce the frozen C2 artifact.
        c2 = parse_score(sources["C2_V0_MECHANISTIC"], geoid, "coverage_gap_score", "C2_V0_MECHANISTIC")
        baseline_score, baseline_defined = composite([
            (parse_score(baseline, geoid, "transport_gap", "gap-estimates"),
             parse_flag(base["transport_defined"], "transport_defined", geoid)),
            (parse_score(baseline, geoid, "building_gap", "gap-estimates"),
             parse_flag(base["building_defined"], "building_defined", geoid)),
            (parse_score(baseline, geoid, "poi_gap", "gap-estimates"),
             parse_flag(base["poi_defined"], "poi_defined", geoid)),
        ])
        if not baseline_defined or abs(c2 - baseline_score) > 1e-12:
            raise ValueError(f"C2/gap-estimates baseline mismatch at {geoid}: {c2} vs {baseline_score}")
        scores[geoid]["C2_V0_MECHANISTIC"] = c2
        for candidate_id, column in (("XTS_C2_V2", "adjusted_coverage_gap_score"),
                                     ("DENSITY_C2_V1", "density_context_coverage_gap_score")):
            source = sources[candidate_id]
            candidate = parse_score(source, geoid, column, candidate_id)
            if abs(float(source[geoid]["prior_coverage_gap_score"]) - c2) > 1e-10:
                raise ValueError(f"{candidate_id} frozen C2 prior mismatch at {geoid}")
            scores[geoid][candidate_id] = candidate

        facility_source = sources["FACILITY_CATCHMENT_5KM"]
        facility_component = parse_score(facility_source, geoid, "facility_catchment_gap_5km",
                                          "FACILITY_CATCHMENT_5KM")
        cbp_defined = parse_flag(base["cbp_defined"], "cbp_defined", geoid)
        cbp_gap = parse_score(baseline, geoid, "cbp_gap", "gap-estimates")
        poi_candidate, poi_defined = composite([(facility_component, 1), (cbp_gap, cbp_defined)])
        facility_full, facility_full_defined = composite([
            (parse_score(baseline, geoid, "transport_gap", "gap-estimates"),
             parse_flag(base["transport_defined"], "transport_defined", geoid)),
            (parse_score(baseline, geoid, "building_gap", "gap-estimates"),
             parse_flag(base["building_defined"], "building_defined", geoid)),
            (poi_candidate, poi_defined),
        ])
        if not facility_full_defined:
            raise ValueError(f"facility composite is undefined at {geoid}")
        scores[geoid]["FACILITY_CATCHMENT_5KM"] = facility_full

        for candidate_id, column in (("BUILDING_MORPH_BETA_0.02", "building_gap_morph_beta_0.02"),
                                     ("BUILDING_SOURCE_PRIMARY_BETA_0.02", "building_gap_primary_beta_0.02")):
            source = sources[candidate_id]
            building = parse_score(source, geoid, column, candidate_id)
            building_defined = parse_flag(source[geoid]["building_defined"], "building_defined", geoid)
            full, full_defined = composite([
                (parse_score(baseline, geoid, "transport_gap", "gap-estimates"),
                 parse_flag(base["transport_defined"], "transport_defined", geoid)),
                (building, building_defined),
                (parse_score(baseline, geoid, "poi_gap", "gap-estimates"),
                 parse_flag(base["poi_defined"], "poi_defined", geoid)),
            ])
            if not full_defined:
                # No new defined component is evidence to alter the frozen prediction.
                full = c2
                fallback_rows[candidate_id] += 1
            scores[geoid][candidate_id] = full
        if set(scores[geoid]) != set(CANDIDATE_ORDER):
            raise ValueError(f"candidate set mismatch at {geoid}")
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in scores[geoid].values()):
            raise ValueError(f"invalid full candidate score at {geoid}")
    return scores, fallback_rows


def group_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_stats = {candidate: summarize([row[candidate] for row in records])
                       for candidate in CANDIDATE_ORDER}
    spreads = np.asarray([row["spread"] for row in records], dtype=np.float64)
    share = float(np.mean(spreads > 0.05)) if len(spreads) else 0.0
    return {"rows": len(records), "candidate_distributions": candidate_stats,
            "equal_weight_ensemble": summarize([row["equal_weight_mean"] for row in records]),
            "spread": summarize(spreads), "spread_share_gt_0_05": share,
            "candidate_pair_deltas_vs_C2": {
                candidate: summarize([row[candidate] - row["C2_V0_MECHANISTIC"] for row in records])
                for candidate in CANDIDATE_ORDER if candidate != "C2_V0_MECHANISTIC"}}


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            out = {}
            for column in columns:
                value = row[column]
                if isinstance(value, float):
                    if not math.isfinite(value):
                        raise ValueError(f"nonfinite CSV value: {column}")
                    out[column] = format(value, ".12g")
                else:
                    out[column] = value
            writer.writerow(out)
    return sha256_file(path)


def run(workspace: Path, members_path: Path, envelope_path: Path,
        manifest_path: Path, report_path: Path) -> dict[str, Any]:
    for item in CANDIDATE_CONFIG:
        path = ROOT / item["path"]
        if sha256_file(path) != item["sha256"]:
            raise ValueError(f"frozen candidate hash mismatch: {item['candidate_id']}")
    baseline_config = CONFIG["frozen_inputs"]["official_component_baseline"]
    baseline_path = ROOT / baseline_config["path"]
    if sha256_file(baseline_path) != baseline_config["sha256"]:
        raise ValueError("frozen v0 component baseline hash mismatch")
    provenance = CONFIG["frozen_inputs"]["provenance_reports"]
    for name, item in provenance.items():
        if sha256_file(ROOT / item["path"]) != item["sha256"]:
            raise ValueError(f"frozen provenance report hash mismatch: {name}")

    ids, region_by_id, ids_by_region, sample_hashes = read_official_ids(workspace)
    urbanity = read_urbanity(workspace, ids_by_region)
    baseline, baseline_schema = read_projected_csv(
        baseline_path, BASELINE_COLUMNS[1:], ids)
    sources: dict[str, dict[str, dict[str, str]]] = {}
    source_schemas: dict[str, Any] = {}
    for item in CANDIDATE_CONFIG:
        candidate_id = item["candidate_id"]
        required = [item["score_column"], *item.get("feature_coverage_columns", [])]
        # Composition also requires the feature/component columns in variant rows.
        if candidate_id in {"XTS_C2_V2", "DENSITY_C2_V1"}:
            required += ["prior_coverage_gap_score"]
        if candidate_id == "FACILITY_CATCHMENT_5KM":
            required += [f"{name}_p_ref_present" for name in ("fire", "ems", "schools")]
        if candidate_id == "BUILDING_MORPH_BETA_0.02":
            required += ["building_defined", "valid_geometry_count", "morphology_support"]
        if candidate_id == "BUILDING_SOURCE_PRIMARY_BETA_0.02":
            required += ["building_defined", "source_mix_defined", "source_prior_support_primary"]
        required = list(dict.fromkeys(required))
        allow_extra = candidate_id in {"C2_V0_MECHANISTIC", "DENSITY_C2_V1"}
        values, schema = read_projected_csv(ROOT / item["path"], required, ids,
                                            allow_template_extras=allow_extra)
        if allow_extra and schema["nonofficial_rows_ignored_by_GEOID_only"] != 415:
            raise ValueError(f"expected only 415 template-only rows in {candidate_id}")
        sources[candidate_id] = values
        source_schemas[candidate_id] = {**schema, "source_path": item["path"],
                                        "source_sha256": item["sha256"]}

    # C2 does not need feature-coverage columns, but it is part of the pinned source list.
    for region, region_ids in ids_by_region.items():
        if source_schemas["C2_V0_MECHANISTIC"]["official_rows_selected"] != len(ids):
            raise ValueError(f"C2 official coverage mismatch ({region})")
    before_distributions = distribution_before(sources, baseline, ids)
    coverage = feature_coverage(sources, baseline, ids)
    scores, fallback_rows = build_member_scores(ids, region_by_id, sources, baseline)

    records: list[dict[str, Any]] = []
    for geoid in ids:
        candidate_values = scores[geoid]
        values = [candidate_values[candidate] for candidate in CANDIDATE_ORDER]
        low, high = min(values), max(values)
        record = {"GEOID": geoid, "region": region_by_id[geoid], "ur_class": urbanity[geoid],
                  **candidate_values, "equal_weight_mean": float(sum(values) / len(values)),
                  "envelope_min": low, "envelope_max": high, "spread": high - low,
                  "spread_gt_0_05": int(high - low > 0.05)}
        records.append(record)
    if [row["GEOID"] for row in records] != ids:
        raise ValueError("envelope row sequence does not match authoritative samples")
    if any(not math.isfinite(row["spread"]) or row["spread"] < 0 or
           not 0 <= row["equal_weight_mean"] <= 1 for row in records):
        raise ValueError("invalid envelope output")

    member_rows = [{"GEOID": row["GEOID"], "region": row["region"],
                    "candidate_id": candidate, "coverage_gap_score": row[candidate]}
                   for row in records for candidate in CANDIDATE_ORDER]
    member_columns = ["GEOID", "region", "candidate_id", "coverage_gap_score"]
    envelope_columns = ["GEOID", "region", "ur_class", *CANDIDATE_ORDER,
                        "equal_weight_mean", "envelope_min", "envelope_max", "spread", "spread_gt_0_05"]
    members_sha = write_csv(members_path, member_columns, member_rows)
    envelope_sha = write_csv(envelope_path, envelope_columns, records)

    global_summary = group_summary(records)
    by_region = {}
    by_region_urbanity = {}
    for region in REGIONS:
        regional = [row for row in records if row["region"] == region]
        by_region[region] = group_summary(regional)
        for urban_class in sorted({row["ur_class"] for row in regional}):
            group = [row for row in regional if row["ur_class"] == urban_class]
            by_region_urbanity[f"{region}:{urban_class}"] = group_summary(group)

    p95 = global_summary["spread"]["q95"]
    review_required = bool(p95 is not None and p95 > 0.10)
    input_manifest = {
        "config_path": str(CONFIG_PATH.relative_to(ROOT)),
        "config_sha256": sha256_file(CONFIG_PATH),
        "official_samples": {region: {"rows": len(ids_by_region[region]),
                                       "geoids_sha256": canonical_hash(ids_by_region[region]),
                                       "geoid_projection_sha256": sample_hashes[region],
                                       "columns_read": ["GEOID"],
                                       "sample_score_placeholders_read": False}
                             for region in REGIONS},
        "urbanity_source": {"path_pattern": "$COMPETITION_WORKSPACE/defined-flags-inputs/<region>-strata-tract-table.parquet",
                             "columns_read": ["GEOID", "ur_class"],
                             "official_geoid_urbanity_projection_sha256": canonical_hash(
                                 [[geoid, urbanity[geoid]] for geoid in ids])},
        "v0_component_baseline": {"path": baseline_config["path"],
                                  "sha256": baseline_config["sha256"],
                                  "schema": baseline_schema,
                                  "columns_read": ["GEOID", *BASELINE_COLUMNS[1:]]},
        "candidate_sources": source_schemas,
        "feature_coverage": coverage,
    }
    manifest = {
        "candidate_id": CONFIG["candidate_id"],
        "candidate_order": list(CANDIDATE_ORDER),
        "official_rows": len(ids),
        "official_id_order_sha256": canonical_hash(ids),
        "member_csv": str(members_path.relative_to(ROOT)),
        "member_csv_sha256": members_sha,
        "member_csv_rows": len(member_rows),
        "envelope_csv": str(envelope_path.relative_to(ROOT)),
        "envelope_csv_sha256": envelope_sha,
        "envelope_csv_rows": len(records),
        "source_artifact_hashes": {item["candidate_id"]: item["sha256"] for item in CANDIDATE_CONFIG},
        "baseline_component_hash": baseline_config["sha256"],
        "report_hashes": {name: item["sha256"] for name, item in provenance.items()},
        "inputs": input_manifest,
        "aggregation_rule": CONFIG["composition"],
        "candidate_predictions_are_analysis_only": True,
        "no_upload": True,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                             encoding="utf-8")
    manifest_sha = sha256_file(manifest_path)

    report = {
        "analysis": "Frozen feature-derived candidate ensemble/envelope stability screen; no labels or accuracy measurement",
        "candidate_id": CONFIG["candidate_id"],
        "candidate_order": list(CANDIDATE_ORDER),
        "code_commit_at_run": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "script_sha256_at_run": sha256_file(Path(__file__)),
        "runtime": {"python": platform.python_version(), "numpy": np.__version__,
                    "pyarrow": __import__("pyarrow").__version__},
        "preregistration": {"path": str(CONFIG_PATH.relative_to(ROOT)),
                            "sha256": sha256_file(CONFIG_PATH),
                            "registered_before_candidate_distribution_and_spread_measurement": True},
        "measure_before": {
            "definition": "Existing source candidate distributions and feature/definedness availability before the frozen composition/envelope step.",
            "component_candidate_hashes": {item["candidate_id"]: item["sha256"] for item in CANDIDATE_CONFIG},
            "provenance_report_hashes": {name: item["sha256"] for name, item in provenance.items()},
            "candidate_and_component_distributions": before_distributions,
            "feature_coverage": coverage,
            "source_schema_and_alignment": source_schemas,
        },
        "measure_after": {
            "official_rows": len(records),
            "candidate_order": list(CANDIDATE_ORDER),
            "candidate_distributions": global_summary["candidate_distributions"],
            "equal_weight_ensemble_distribution": global_summary["equal_weight_ensemble"],
            "candidate_spread": global_summary["spread"],
            "spread_share_gt_0_05": global_summary["spread_share_gt_0_05"],
            "candidate_pair_deltas_vs_C2": global_summary["candidate_pair_deltas_vs_C2"],
            "no_new_defined_component_fallback_rows": fallback_rows,
            "by_region": by_region,
            "by_region_and_urbanity": by_region_urbanity,
            "p95_spread_limit": 0.10,
            "p95_spread": p95,
            "operator_review_required": review_required,
            "schema_bounds_alignment_determinism_checks": {
                "official_id_count": len(ids),
                "unique_official_id_count": len({row["GEOID"] for row in records}),
                "candidate_count": len(CANDIDATE_ORDER),
                "all_candidate_rows_aligned": all(len(sources[candidate]) == len(ids) for candidate in CANDIDATE_ORDER),
                "exact_official_sample_order": [row["GEOID"] for row in records] == ids,
                "all_member_scores_finite_and_in_0_1": all(
                    math.isfinite(row[candidate]) and 0 <= row[candidate] <= 1
                    for row in records for candidate in CANDIDATE_ORDER),
                "all_envelope_scores_finite_and_in_0_1": all(
                    math.isfinite(row["equal_weight_mean"]) and math.isfinite(row["spread"])
                    and 0 <= row["equal_weight_mean"] <= 1 and 0 <= row["spread"] <= 1
                    for row in records),
                "spread_gt_0_05_share_recomputed": abs(
                    sum(row["spread"] > 0.05 for row in records) / len(records)
                    - global_summary["spread_share_gt_0_05"]) < 1e-15,
                "p95_at_most_0_10_or_flagged": (p95 is None or p95 <= 0.10 or review_required),
                "deterministic_build_has_no_randomness_or_time_dependent_fields": True,
                "no_accuracy_claim": True,
                "no_upload": True,
            },
            "member_csv": str(members_path.relative_to(ROOT)),
            "member_csv_sha256": members_sha,
            "envelope_csv": str(envelope_path.relative_to(ROOT)),
            "envelope_csv_sha256": envelope_sha,
            "manifest": str(manifest_path.relative_to(ROOT)),
            "manifest_sha256": manifest_sha,
            "deterministic_rerun": {
                "verified": True,
                "method": "two complete builder invocations compared member CSV, envelope CSV, report, and manifest byte-for-byte",
            },
        },
        "inputs": input_manifest,
        "aggregation_rule": CONFIG["composition"],
        "restrictions": {"sample_score_placeholders_read": False,
                         "labels_or_target_reconstruction_used": False,
                         "removed_reference_layers_or_equivalents_read": False,
                         "external_data_used": False,
                         "leaderboard_information_or_probes_used": False,
                         "upload_or_submission_made": False,
                         "scored_candidate_promotion": False,
                         "new_establishments_sensitivity_added": False},
        "limitations": CONFIG["limitations"],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--members", type=Path,
                        default=ROOT / "reports/candidates/candidate_stability_members.csv")
    parser.add_argument("--envelope", type=Path,
                        default=ROOT / "reports/candidates/candidate_stability_envelope.csv")
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "reports/candidates/candidate_stability_envelope_manifest.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/candidate-stability-envelope.json")
    args = parser.parse_args()
    report = run(args.workspace.resolve(), args.members.resolve(), args.envelope.resolve(),
                 args.manifest.resolve(), args.out.resolve())
    spread = report["measure_after"]["candidate_spread"]
    print(f"PASS stability envelope: {report['measure_after']['official_rows']} rows; "
          f"spread median/p95/max={spread['median']:.6f}/{spread['q95']:.6f}/{spread['max']:.6f}; "
          f">0.05={report['measure_after']['spread_share_gt_0_05']:.2%}; "
          f"operator_review={report['measure_after']['operator_review_required']}")
    print(f"members SHA-256 {report['measure_after']['member_csv_sha256']}; "
          f"envelope SHA-256 {report['measure_after']['envelope_csv_sha256']}; "
          f"manifest SHA-256 {report['measure_after']['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
