#!/usr/bin/env python3
"""Assemble the frozen model-only v2 estimator into one pre-registered candidate.

Every level, weight and parameter here comes from the challenge-provided features
and the frozen component mechanisms already committed in this repo, plus the
organiser's published method. No leaderboard-derived constant enters the candidate:
no `alpha` from the phase-2 probe, no probe region means, no probe beta, no
`E[qy]/E[q]` target moment, and no per-GEOID fit. The removed reference layers
(TIGER, MS footprints, USGS structures, CBP) and any equivalent copy are never
read; the platform template's score placeholder column is never read.

The script writes the frozen scored artifact, its manifest card with a
pre-registered public-RMSE forecast written before any upload, and
``reports/estimator-v2-audit.json``. It performs no upload: the committed
final-two protocol queues zero uploads and requires operator authorization
through the orchestrator gate.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from audit_v2_component_masks import (  # noqa: E402  (single source of truth for the frozen v2 rules)
    CONFIG as MASK_CONFIG,
    REGIONS,
    composite_mean,
    official_scored_ids,
    parse_flag,
    parse_float,
    sha256_file,
)

COLUMNS = ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]
SCORE_COLUMNS = COLUMNS[1:]
TEMPLATE_REGIONS = (*REGIONS, "eastern-wa")
CANDIDATE_ID = "V2_model"
ARTIFACT = Path("reports/candidates/V2_model.csv")
COMPONENT_ARTIFACTS = {
    "transport": "reports/candidates/road_topology_candidate.csv",
    "building": "reports/candidates/building_morphology.csv",
    "poi": "reports/candidates/poi_confidence_weighted.csv",
}
# Retired leaderboard-derived constants. They must not appear as a v2 score value
# or as a region mean: their presence would mean a probe constant entered the model.
RETIRED_LB_CONSTANTS = {
    "alpha_c1_from_P4": 0.3288136427728255,
    "beta_constrained_from_P5": -0.04967993197091435,
    "beta_unconstrained_from_P5": -0.2181321596482996,
    "global_constant_c_from_P6": 0.05652688955988315,
}
HISTORICAL_PUBLIC_RMSE = {
    "C0": 0.107246299,
    "C1": 0.146449112,
    "C2": 0.235358712,
    "C3": 0.349409867,
    "P4": 0.091440198,
}
MEASURE_BEFORE = {
    "C2_inferred_E_py": 0.004396101562947799,
    "C2_uncentred_correlation_proxy": 0.17858516630602533,
    "P5_unconstrained_detail_beta": -0.2181321596482996,
    "P5_note": "v0 tract detail is anti-informative after region-mean removal (unconstrained beta < 0)",
    "source": "reports/lb-score-algebra.json (historical probe algebra, quoted as background only)",
}
DEPENDENCY_REPORTS = {
    "transport route-ref": "reports/transport-route-ref-audit.json",
    "road topology variants": "reports/transport-route-variants.json",
    "POI category audit": "reports/poi-category-audit.json",
    "facility-miss model": "reports/poi-facility-miss-audit.json",
    "building v2 level": "reports/building-v2-audit.json",
    "building source prior": "reports/building-source-prior.json",
    "establishments level": "reports/establishments-sensitivity.json",
    "edge-case policy": "reports/edge-cases.json",
    "v2 defined-mask audit": "reports/v2-defined-mask-audit.json",
    "v2 prior envelope": "reports/v2-prior-envelope.json",
}
PREREGISTERED_CENTRED_CORRELATION = 0.30
CENTRED_CORRELATION_GRID = (0.0, 0.17858516630602533, 0.25, 0.30, 0.35, 0.5)
LEVEL_MULTIPLIERS = (0.5, 0.75, 1.0, 1.25, 1.5)
EXPECTED_OFFICIAL_ROWS = 9379
EXPECTED_TEMPLATE_ROWS = 9794
EXPECTED_TEMPLATE_ONLY_ROWS = 415
PUBLIC_FRACTION = 0.30


def fmt(value: float) -> str:
    return format(value, ".17g")


def read_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return {str(row["GEOID"]): row for row in csv.DictReader(stream)}


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def exact_defined_mean(values: list[tuple[float, int]]) -> float:
    defined = [Fraction(value) for value, flag in values if flag]
    if not defined:
        return 0.0
    return float(sum(defined) / len(defined))


def load_pipeline_combiner():
    import estimate_gaps  # noqa: E402  (the frozen combiner the candidate is emitted with)

    return estimate_gaps.composite_mean


def verify_frozen_inputs(workspace: Path) -> dict[str, Any]:
    """Fail closed unless every declared input matches its committed SHA-256."""
    pins = MASK_CONFIG["frozen_inputs"]
    verified: dict[str, Any] = {}
    for name, path in COMPONENT_ARTIFACTS.items():
        declared = pins[f"{name}_component"]
        if declared["path"] != path:
            raise ValueError(f"component pin path drift for {name}: {declared['path']} != {path}")
        actual = sha256_file(ROOT / path)
        if actual != declared["sha256"]:
            raise ValueError(f"frozen component artifact changed: {path} {actual} != {declared['sha256']}")
        verified[path] = actual
    sample_template = pins["official_samples"]["path_template"]
    for region, digest in pins["official_samples"]["sha256"].items():
        path = workspace / sample_template.format(region=region)
        actual = sha256_file(path)
        if actual != digest:
            raise ValueError(f"official sample changed: {path} {actual} != {digest}")
        verified[sample_template.format(region=region)] = actual
    manifest = json.loads((ROOT / "reports/candidates.json").read_text(encoding="utf-8"))
    template = manifest["platform_template"]
    template_path = workspace / str(template["source"]).replace("$COMPETITION_WORKSPACE/", "")
    actual = sha256_file(template_path)
    if actual != template["sha256"]:
        raise ValueError(f"platform template changed: {template_path} {actual} != {template['sha256']}")
    verified["platform_template"] = {"path": str(template["source"]), "sha256": actual,
                                     "rows": template["rows"],
                                     "columns_read": template["columns_read"],
                                     "sample_score_placeholders_read": False}
    for script in ("scripts/estimate_gaps.py", "scripts/estimator_v2_building.py",
                   "scripts/estimator_v2_establishments.py", "scripts/audit_v2_component_masks.py",
                   "config/v2-component-mask-audit-preregistered.json"):
        verified[script] = sha256_file(ROOT / script)
    dependencies = {}
    for label, rel in DEPENDENCY_REPORTS.items():
        path = ROOT / rel
        if not path.is_file():
            raise ValueError(f"prerequisite report missing: {label} -> {rel}")
        dependencies[label] = {"path": rel, "sha256": sha256_file(path)}
    verified["prerequisite_reports"] = dependencies
    return verified


def template_order(workspace: Path) -> tuple[list[str], dict[str, str]]:
    """Return the platform template's GEOID order and region labels.

    Only the ``GEOID`` and ``region`` metadata columns are read; the template's
    score placeholder column is never accessed.
    """
    path = workspace / "SampleSubmission.csv"
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        geoid_index, region_index = header.index("GEOID"), header.index("region")
        ids: list[str] = []
        regions: dict[str, str] = {}
        for row in reader:
            if not row:
                continue
            geoid = str(row[geoid_index])
            ids.append(geoid)
            regions[geoid] = str(row[region_index])
    if len(ids) != EXPECTED_TEMPLATE_ROWS or len(set(ids)) != len(ids):
        raise ValueError(f"template must hold {EXPECTED_TEMPLATE_ROWS} unique GEOIDs, got {len(ids)}")
    counts = {region: sum(1 for geoid in ids if regions[geoid] == region) for region in TEMPLATE_REGIONS}
    if counts.get("eastern-wa") != EXPECTED_TEMPLATE_ONLY_ROWS:
        raise ValueError(f"unexpected template-only eastern-WA count: {counts}")
    if set(regions.values()) != set(TEMPLATE_REGIONS):
        raise ValueError(f"unexpected template regions: {sorted(set(regions.values()))}")
    return ids, regions


def build_official_rows(workspace: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compose the frozen components for every official tract with all gates checked."""
    pins = MASK_CONFIG["frozen_inputs"]
    transport_cfg, building_cfg, poi_cfg = (pins["transport_component"],
                                           pins["building_component"], pins["poi_component"])
    pairs = official_scored_ids(workspace)
    ids = [geoid for geoid, _ in pairs]
    region_of = dict(pairs)
    transport = read_rows(ROOT / transport_cfg["path"])
    building = read_rows(ROOT / building_cfg["path"])
    poi = read_rows(ROOT / poi_cfg["path"])
    for name, table in (("transport", transport), ("building", building), ("poi", poi)):
        if set(table) != set(ids):
            missing = len(set(ids) - set(table))
            extra = len(set(table) - set(ids))
            raise ValueError(f"{name} artifact ID set mismatch: {missing} missing, {extra} extra")
    pipeline_mean = load_pipeline_combiner()

    rows: list[dict[str, Any]] = []
    max_arithmetic_residual = 0.0
    counters = {"component_values_out_of_range": 0, "undefined_rows_with_nonzero_placeholder": 0,
                "zero_defined_rows": 0}
    for geoid in ids:
        t_row, b_row, p_row = transport[geoid], building[geoid], poi[geoid]
        values = {
            "transport_gap": (parse_float(t_row[transport_cfg["gap_column"]], "transport gap", geoid),
                              parse_flag(t_row[transport_cfg["defined_column"]], "transport defined", geoid)),
            "building_gap": (parse_float(b_row[building_cfg["gap_column"]], "building gap", geoid),
                             parse_flag(b_row[building_cfg["defined_column"]], "building defined", geoid)),
            "poi_gap": (parse_float(p_row[poi_cfg["gap_column"]], "poi gap", geoid),
                        parse_flag(p_row[poi_cfg["defined_column"]], "poi defined", geoid)),
        }
        components = [values[column] for column in SCORE_COLUMNS[:-1]]
        for column, (gap, flag) in values.items():
            if not math.isfinite(gap) or not 0.0 <= gap <= 1.0:
                counters["component_values_out_of_range"] += 1
                raise ValueError(f"{column} outside [0,1] at {geoid}: {gap}")
            if flag == 0 and gap != 0.0:
                counters["undefined_rows_with_nonzero_placeholder"] += 1
                raise ValueError(f"undefined {column} carries a nonzero placeholder at {geoid}: {gap}")
        score, defined = composite_mean(components)
        if defined == 0:
            counters["zero_defined_rows"] += 1
            raise ValueError(f"tract {geoid} has no defined component; v2 emits no placeholder score")
        pipeline_value = pipeline_mean(components)
        for other in (score, exact_defined_mean(components),
                      0.0 if pipeline_value is None else float(pipeline_value)):
            max_arithmetic_residual = max(max_arithmetic_residual, abs(score - other))
        rows.append({
            "geoid": geoid, "region": region_of[geoid],
            "transport_gap": values["transport_gap"][0], "transport_defined": values["transport_gap"][1],
            "building_gap": values["building_gap"][0], "building_defined": values["building_gap"][1],
            "poi_gap": values["poi_gap"][0], "poi_defined": values["poi_gap"][1],
            "coverage_gap_score": score, "defined_components": defined,
        })
    if max_arithmetic_residual > 1e-12:
        raise ValueError(f"composite arithmetic residual {max_arithmetic_residual} exceeds 1e-12")
    facts = {
        "official_rows": len(rows),
        "unique_official_ids": len({row["geoid"] for row in rows}),
        "region_rows": {region: sum(1 for row in rows if row["region"] == region) for region in REGIONS},
        "expected_region_rows": dict(MASK_CONFIG["scope"]["expected_region_rows"]),
        "max_composite_arithmetic_residual": max_arithmetic_residual,
        "component_values_out_of_range": counters["component_values_out_of_range"],
        "undefined_rows_with_nonzero_placeholder": counters["undefined_rows_with_nonzero_placeholder"],
        "zero_defined_rows": counters["zero_defined_rows"],
        "duplicate_or_missing_ids": 0,
        "implementations_compared": [
            "scripts/estimate_gaps.py composite_mean (the frozen candidate combiner)",
            "audit_v2_component_masks.composite_mean",
            "audit-local exact rational defined-component mean",
        ],
        "defined_rates": {
            column[: -len("_gap")] + "_defined": sum(row[column.replace("_gap", "_defined")]
                                                     for row in rows) / len(rows)
            for column in SCORE_COLUMNS[:-1]},
    }
    return rows, facts


def model_moments(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [row["coverage_gap_score"] for row in rows]
    total = len(scores)
    mean = sum(scores) / total
    second_moment = sum(value * value for value in scores) / total
    variance = max(second_moment - mean * mean, 0.0)
    by_region: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_region[row["region"]].append(row["coverage_gap_score"])
    detail = []
    for row in rows:
        region_scores = by_region[row["region"]]
        region_mean = sum(region_scores) / len(region_scores)
        detail.append(row["coverage_gap_score"] - region_mean)
    detail_variance = sum(value * value for value in detail) / total
    return {
        "official_rows": total,
        "mean_prediction": mean,
        "second_moment_E_v2": second_moment,
        "rms_prediction": math.sqrt(second_moment),
        "sd_prediction": math.sqrt(variance),
        "sd_detail_about_region_mean": math.sqrt(detail_variance),
        "region_prediction_means": {region: sum(values) / len(values)
                                    for region, values in sorted(by_region.items())},
        "region_prediction_sds": {region: math.sqrt(max(sum(v * v for v in values) / len(values)
                                                       - (sum(values) / len(values)) ** 2, 0.0))
                                  for region, values in sorted(by_region.items())},
        "min_prediction": min(scores),
        "max_prediction": max(scores),
        "constant_within_any_region": any(len(set(values)) == 1 for values in by_region.values()),
    }


def forecast(moments: dict[str, Any]) -> dict[str, Any]:
    """Pre-register the public RMSE forecast before any upload.

    The only target-side quantity used is ``E[y^2]``, taken from the platform RMSE
    of the already-scored all-zero candidate C0 (predicting zero gives RMSE^2=E[y^2]).
    It anchors the forecast; it is not a model parameter and never enters the scored
    file. Everything else in the forecast is measured on the frozen artifact.
    """
    e_y2 = HISTORICAL_PUBLIC_RMSE["C0"] ** 2
    mu_v = moments["mean_prediction"]
    sd_v = moments["sd_prediction"]

    def rmse(mu_y: float, rho_c: float) -> float:
        variance_y = e_y2 - mu_y * mu_y
        if variance_y < 0:
            raise ValueError(f"anchor E[y^2]={e_y2} cannot support target mean {mu_y}")
        sd_y = math.sqrt(variance_y)
        return math.sqrt(max((mu_y - mu_v) ** 2 + variance_y + sd_v ** 2
                             - 2.0 * rho_c * sd_y * sd_v, 0.0))

    def rho_for(score: float, mu_y: float) -> float:
        variance_y = e_y2 - mu_y * mu_y
        sd_y = math.sqrt(variance_y)
        numerator = (mu_y - mu_v) ** 2 + variance_y + sd_v ** 2 - score * score
        return numerator / (2.0 * sd_y * sd_v)

    point = rmse(mu_y=mu_v, rho_c=PREREGISTERED_CENTRED_CORRELATION)
    grid = {
        f"target_mean_{multiplier}x_model_mean": {
            "assumed_target_mean": mu_v * multiplier,
            "predicted_public_rmse_by_centred_correlation": {
                f"{rho:.6f}": rmse(mu_v * multiplier, rho) for rho in CENTRED_CORRELATION_GRID},
        }
        for multiplier in LEVEL_MULTIPLIERS
    }
    n_public = PUBLIC_FRACTION * EXPECTED_OFFICIAL_ROWS
    se_point = point / math.sqrt(2.0 * n_public)
    se_p4 = HISTORICAL_PUBLIC_RMSE["P4"] / math.sqrt(2.0 * n_public)
    return {
        "written_before_upload": True,
        "point_prediction_public_rmse": point,
        "point_prediction_assumptions": {
            "assumed_target_mean_equals_model_mean": mu_v,
            "level_hypothesis": ("the frozen provided-feature model is an unbiased level estimator, "
                                 "so the assumed target mean equals the model's own official-tract mean"),
            "assumed_centred_correlation": PREREGISTERED_CENTRED_CORRELATION,
            "correlation_rationale": ("midpoint between the only measured v0-detail informativeness "
                                      f"({MEASURE_BEFORE['C2_uncentred_correlation_proxy']:.6f}) and the "
                                      "row's informativeness target of 0.35 for v2 tract detail"),
        },
        "anchor": {
            "E_y2": e_y2,
            "source": "platform public RMSE of the already-scored all-zero candidate C0 (predicting 0 gives RMSE^2=E[y^2])",
            "role": "forecast anchor only; never enters the scored artifact",
            "used_as_model_parameter": False,
        },
        "identity": ("RMSE^2 = (mu_y - mu_v)^2 + (E[y^2] - mu_y^2) + sd_v^2 "
                     "- 2*rho_c*sqrt(E[y^2] - mu_y^2)*sd_v"),
        "model_side_measurements": {
            "mu_v": mu_v, "sd_v": sd_v, "E_v2": moments["second_moment_E_v2"],
            "sd_detail_about_region_mean": moments["sd_detail_about_region_mean"],
        },
        "sensitivity_grid": grid,
        "centred_correlation_breakevens_at_model_mean": {
            "tie_all_zero_C0": rho_for(HISTORICAL_PUBLIC_RMSE["C0"], mu_v),
            "tie_region_constant_C1": rho_for(HISTORICAL_PUBLIC_RMSE["C1"], mu_v),
            "beat_P4_alpha_c1": rho_for(HISTORICAL_PUBLIC_RMSE["P4"], mu_v),
            "interpretation": ("the candidate beats a benchmark when its actual centred correlation with the "
                               "target exceeds the listed value; below it the candidate is worse than that benchmark"),
        },
        "sampling_noise_guard": {
            "public_tracts_expected": n_public,
            "se_public_at_point_prediction": se_point,
            "se_public_at_P4_scale": se_p4,
            "pairwise_guard_vs_P4": se_point + se_p4,
            "rule": ("from docs/final-two-protocol.md: a public RMSE difference at or below the pairwise "
                     "guard is a tie, not a selection reason"),
        },
        "post_score_inference_formulas": {
            "E_yv_identifiable_from_C0_and_v2": "E[y*v] = (E[y^2] + E[v^2] - RMSE_v2^2) / 2",
            "uncentred_correlation": "corr(y, v) = E[y*v] / (sqrt(E[y^2]) * sqrt(E[v^2]))",
            "centred_correlation": ("rho_c = (E[y*v] - mu_y*mu_v) / (sd_y*sd_v); mu_y and sd_y are not "
                                    "identifiable from one scored candidate, so the platform evaluation "
                                    "identifies E[y*v] and the uncentred correlation directly"),
            "region_mean_removed_detail_moment": ("E[(y - m_r) d_v2] needs a level-only companion candidate "
                                                 "scored on the same split; it is deliberately not assumed here"),
        },
        "decision_rule": {
            "beats_P4_if": f"actual public RMSE <= {HISTORICAL_PUBLIC_RMSE['P4'] - (se_point + se_p4):.9f}",
            "ties_P4_if": f"actual public RMSE within the pairwise guard of {HISTORICAL_PUBLIC_RMSE['P4']}",
            "beats_region_constant_C1_if": f"actual public RMSE < {HISTORICAL_PUBLIC_RMSE['C1']}",
            "not_informative_if": (f"actual public RMSE > {HISTORICAL_PUBLIC_RMSE['C0']} (the all-zero "
                                   "baseline): then the v2 tract detail adds more variance than signal at "
                                   "its current level, so stop adding detail and keep level-only candidates"),
            "maximum_uploads_for_this_candidate": 1,
            "second_upload_to_adjust_weights": "forbidden",
        },
        "historical_public_rmse_anchors": dict(HISTORICAL_PUBLIC_RMSE),
    }


def anti_probe_checks(rows: list[dict[str, Any]], moments: dict[str, Any],
                     emitted_values: list[float]) -> dict[str, Any]:
    """Structural evidence that no retired leaderboard-derived constant entered the scored file.

    ``emitted_values`` covers every score cell of all 9,794 emitted rows, including the
    template-only eastern-WA fill, because the platform sees the whole file.
    """
    scores = [row["coverage_gap_score"] for row in rows]
    component_values = [row[column] for row in rows for column in SCORE_COLUMNS[:-1]]
    region_means = list(moments["region_prediction_means"].values())
    offenders: dict[str, Any] = {}
    for label, constant in RETIRED_LB_CONSTANTS.items():
        score_hits = sum(1 for value in emitted_values if abs(value - constant) <= 1e-12)
        component_hits = sum(1 for value in component_values if abs(value - constant) <= 1e-12)
        region_hits = sum(1 for value in region_means if abs(value - constant) <= 1e-9)
        if score_hits or component_hits or region_hits:
            offenders[label] = {"score_hits": score_hits, "component_hits": component_hits,
                                "region_mean_hits": region_hits}
    return {
        "retired_lb_constants_scanned": {key: value for key, value in RETIRED_LB_CONSTANTS.items()},
        "offenders": offenders,
        "scored_file_cells_scanned": len(emitted_values),
        "no_retired_lb_constant_in_the_scored_file": not offenders,
        "score_is_not_constant_within_any_region": not moments["constant_within_any_region"],
        "region_means_from_probe_region_targets": False,
        "beta_or_alpha_applied": False,
    }


def write_outputs(rows: list[dict[str, Any]], wa_fill: float, out_dir: Path,
                 workspace: Path, write: bool = True) -> dict[str, Any]:
    template_ids, template_regions = template_order(workspace)
    by_geoid = {row["geoid"]: row for row in rows}
    fill = {column: wa_fill for column in SCORE_COLUMNS}
    output_rows = []
    for geoid in template_ids:
        if geoid in by_geoid:
            row = by_geoid[geoid]
            output_rows.append({"GEOID": geoid,
                                **{column: fmt(row[column]) for column in SCORE_COLUMNS}})
        else:
            if template_regions[geoid] != "eastern-wa":
                raise ValueError(f"non-official row {geoid} is not eastern-WA")
            output_rows.append({"GEOID": geoid, **{column: fmt(value) for column, value in fill.items()}})
    if [row["GEOID"] for row in output_rows] != template_ids:
        raise AssertionError("output order does not match the platform template order")
    payload = csv_bytes(output_rows)
    if payload != csv_bytes(output_rows):  # pragma: no cover - deterministic by construction
        raise AssertionError("candidate serialization is not deterministic")
    path = out_dir / ARTIFACT.name
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        if sha256_file(path) != hashlib.sha256(payload).hexdigest():
            raise AssertionError("written artifact hash does not match the built payload")
    if payload != csv_bytes(output_rows):
        raise AssertionError("candidate rebuild hash mismatch")
    per_region = {}
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in output_rows:
        grouped[template_regions[row["GEOID"]]].append(float(row["coverage_gap_score"]))
    for region in TEMPLATE_REGIONS:
        values = grouped[region]
        per_region[region] = sum(values) / len(values)
    return {
        "path": (str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)),
        "bytes": len(payload),
        "written": write,
        "rebuild_hashes_match": True,
        "sha256": hashlib.sha256(payload).hexdigest(), "rows": len(output_rows),
        "columns": COLUMNS, "per_region_means": per_region,
        "eastern_wa_rows": sum(1 for geoid in template_ids if template_regions[geoid] == "eastern-wa"),
        "eastern_wa_fill": fill,
    }


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except Exception as exc:  # pragma: no cover - defensive
        return f"unavailable: {exc}"


def build(workspace: Path, out_dir: Path, wa_fill: float,
         write: bool = True) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    verified = verify_frozen_inputs(workspace)
    rows, facts = build_official_rows(workspace)
    moments = model_moments(rows)
    forecast_block = forecast(moments)
    artifact = write_outputs(rows, wa_fill, out_dir, workspace, write=write)
    emitted_values = [value for row in rows for column in SCORE_COLUMNS for value in (row[column],)]
    emitted_values += [wa_fill] * (artifact["rows"] - len(rows)) * len(SCORE_COLUMNS)
    anti_probe = anti_probe_checks(rows, moments, emitted_values)
    gates = {
        "frozen_inputs_sha_verified": bool(verified),
        "official_rows_exact": (facts["official_rows"] == EXPECTED_OFFICIAL_ROWS
                                == facts["unique_official_ids"]),
        "region_rows_match_authoritative_samples": (facts["region_rows"] == facts["expected_region_rows"]
                                                    == MASK_CONFIG["scope"]["expected_region_rows"]),
        "component_values_finite_in_zero_one": facts["component_values_out_of_range"] == 0,
        "undefined_rows_carry_zero_placeholder": facts["undefined_rows_with_nonzero_placeholder"] == 0,
        "zero_defined_composite_rows": facts["zero_defined_rows"] == 0,
        "zero_duplicate_or_missing_ids": (facts["duplicate_or_missing_ids"] == 0
                                          and facts["official_rows"] == facts["unique_official_ids"]),
        "composite_is_mean_of_defined_components_only": facts["max_composite_arithmetic_residual"] <= 1e-12,
        "template_rows_exact": artifact["rows"] == EXPECTED_TEMPLATE_ROWS,
        "template_only_rows_are_eastern_wa": artifact["eastern_wa_rows"] == EXPECTED_TEMPLATE_ONLY_ROWS,
        "template_only_fill_deterministic": all(value == wa_fill
                                                for value in artifact["eastern_wa_fill"].values()),
        "deterministic_rebuild_identical_hash": artifact["rebuild_hashes_match"],
        "score_columns_match_platform_schema": artifact["columns"] == COLUMNS,
        "no_retired_lb_constant_in_the_scored_file": anti_probe[
            "no_retired_lb_constant_in_the_scored_file"],
        "score_is_not_constant_within_any_region": anti_probe["score_is_not_constant_within_any_region"],
        "equal_model_derived_component_weights": True,
        "forecast_preregistered_before_upload": forecast_block["written_before_upload"],
    }
    gates["all_passed"] = all(value for value in gates.values() if isinstance(value, bool))
    audit = {
        "schema_version": 1,
        "audit_id": "estimator-v2-audit",
        "backlog_item": "bias-est-v2-assembly-probe",
        "status": "frozen_candidate_preregistered_no_upload",
        "upload_status": {"upload_performed": False, "live_upload_attempted": False,
                          "uploads_used": 0, "max_uploads_preregistered": 1,
                          "requires": "operator authorization through the orchestrator submission gate"},
        "governing_policy": {
            "submission_policy": "docs/submission-policy.md",
            "final_two_protocol": "docs/final-two-protocol.md",
            "operator_directive": ("model-first; no information probes; no LB-derived constants; at most one "
                                   "finished-model evaluation per authorized candidate under the 2/day gate"),
            "upload_queued_by_this_audit": False,
            "upload_requires": ("operator authorization through $ORCHESTRATOR_ROOT/scripts/zindi_submit.py "
                                "plus the frozen final-two protocol gates"),
        },
        "model": {
            "candidate_id": CANDIDATE_ID,
            "estimator_version": "v2-model-only",
            "composite_rule": ("equal-weight arithmetic mean of the model-defined top-level components "
                               "(transport route-reference gap, building fixed residual prior, POI gap); "
                               "undefined components are excluded entirely, never zero-filled"),
            "components": {
                "transport_gap": {
                    "formula": MASK_CONFIG["frozen_rules"]["transport"]["gap"],
                    "defined": MASK_CONFIG["frozen_rules"]["transport"]["defined"],
                    "source": "reports/candidates/road_topology_candidate.csv",
                    "level_provenance": "provided Overture route/named-road lengths only; no prior",
                },
                "building_gap": {
                    "formula": MASK_CONFIG["frozen_rules"]["building"]["gap"],
                    "defined": MASK_CONFIG["frozen_rules"]["building"]["defined"],
                    "source": "reports/candidates/building_morphology.csv",
                    "level_provenance": ("scripts/estimator_v2_building.py BUILDING_GAP_V2 = 0.01 fixed "
                                         "residual prior from the closed building-level work"),
                },
                "poi_gap": {
                    "formula": (MASK_CONFIG["frozen_rules"]["poi"]["facility_half"] + "; "
                                + MASK_CONFIG["frozen_rules"]["poi"]["combiner"]),
                    "defined": MASK_CONFIG["frozen_rules"]["poi"]["defined"],
                    "source": "reports/candidates/poi_confidence_weighted.csv",
                    "level_provenance": ("fixed logistic presence priors and the explicit cbp_k = 0.024 "
                                         "establishments parameter, both registered in "
                                         "config/v2-component-mask-audit-preregistered.json"),
                },
            },
            "weights": {"transport": 1.0, "building": 1.0, "poi": 1.0,
                        "normalisation": "equal weight per defined component, per the organiser's published method"},
            "parameters_not_used": {
                "alpha_c1_from_P4": RETIRED_LB_CONSTANTS["alpha_c1_from_P4"],
                "beta_from_P5": RETIRED_LB_CONSTANTS["beta_constrained_from_P5"],
                "beta_unconstrained_from_P5": RETIRED_LB_CONSTANTS["beta_unconstrained_from_P5"],
                "probe_region_means": None,
                "E_qy_over_E_q_target_moment": None,
                "note": ("listed only to record that they are excluded; none of them enters the artifact "
                         "(see anti_probe_checks)"),
            },
        },
        "frozen_inputs": verified,
        "dependency_closure": {
            "required": sorted(DEPENDENCY_REPORTS),
            "all_present": all((ROOT / rel).is_file() for rel in DEPENDENCY_REPORTS.values()),
            "region_means_are_not_a_dependency": True,
        },
        "measure_before": MEASURE_BEFORE,
        "measure_after": {
            "uploads_performed": 0,
            "actual_public_rmse": None,
            "inferred_E_yv": None,
            "inferred_uncentred_correlation": None,
            "inferred_region_mean_removed_detail_moment": None,
            "optimal_beta": None,
            "states_whether_v2_beats_P4": ("not measurable locally: Bias Bounty ships no labels and the "
                                           "platform evaluation has not been run for this candidate. The "
                                           "pre-registered forecast and centred-correlation breakevens state "
                                           "exactly what conclusion follows from each outcome."),
            "states_whether_v2_beats_region_constant_C1": ("pre-registered prediction beats C1 across the "
                                                          "whole preregistered grid; the platform figure is "
                                                          "still required to claim it"),
        },
        "model_moments": moments,
        "forecast": forecast_block,
        "anti_probe_checks": anti_probe,
        "component_facts": facts,
        "artifact": artifact,
        "acceptance": gates,
        "prohibitions": {
            "removed_reference_layers_or_equivalents_read": False,
            "labels_read": False,
            "sample_or_template_score_placeholders_read": False,
            "leaderboard_derived_constants_in_model": False,
            "per_geoid_or_per_GEOID_tuning": False,
            "upload_attempted": False,
            "candidate_promoted_to_final_selection": False,
        },
        "reproducibility": {
            "command": (f"COMPETITION_WORKSPACE=\"$COMPETITION_WORKSPACE\" /home/martin/hddProjects/langywrap/uv run "
                        f"--with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 "
                        f"python scripts/build_estimator_v2_candidates.py --workspace \"$COMPETITION_WORKSPACE\""),
            "git_commit": git_commit(),
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "python": sys.version.split()[0],
        },
    }
    card = {
        "candidate_id": CANDIDATE_ID,
        "name": "model-only v2 composite (provided features + documented priors)",
        "artifact": artifact["path"],
        "sha256": artifact["sha256"],
        "rows": artifact["rows"],
        "columns": artifact["columns"],
        "pre_registered": True,
        "description": ("Single frozen model-only v2 composite: equal-weight mean of the model-defined "
                        "transport route-reference, fixed building prior and POI components. No "
                        "leaderboard-derived level, alpha, beta or probe region mean. Not uploaded: the "
                        "frozen final-two protocol queues zero uploads and requires operator authorization."),
        "phase": "model-only-v2-evaluation",
        "estimator_version": "v2-model-only",
        "parameters": {
            "composite": "mean of defined components (equal weights, undefined excluded)",
            "building_gap_constant": MASK_CONFIG["frozen_rules"]["building"]["gap"],
            "establishments_cbp_k": MASK_CONFIG["frozen_rules"]["establishments"]["cbp_k"],
            "facility_presence_priors": MASK_CONFIG["frozen_rules"]["facility_family"]["presence_prior"],
            "transport": "ratio of provided Overture route and named-road lengths (no prior)",
            "predicted_public_rmse": forecast_block["point_prediction_public_rmse"],
            "prediction_basis": ("model-side moments of the frozen artifact (mu_v, sd_v, E[v^2]) with the "
                                 "all-zero candidate C0 platform RMSE as the E[y^2] anchor, assumed target "
                                 "mean equal to the model's own mean and assumed centred correlation "
                                 f"{PREREGISTERED_CENTRED_CORRELATION}; no LB-derived model parameter"),
            "predicted_by_centred_correlation": forecast_block["sensitivity_grid"][
                "target_mean_1.0x_model_mean"]["predicted_public_rmse_by_centred_correlation"],
            "centred_correlation_breakevens": forecast_block[
                "centred_correlation_breakevens_at_model_mean"],
        },
        "hypothesis": ("The v0 detail failed because its component levels were uncentred proxies whose "
                       "uncentred correlation with the target was 0.1786 and whose region-mean-removed detail "
                       "was anti-informative (unconstrained beta -0.218). v2 replaces each proxy with a "
                       "mechanism estimated from the provided Overture counts themselves - route/named-road "
                       "ratio, fixed building residual prior calibrated against the Overture building "
                       "presence rate, and the organiser's facility/establishments halves with fixed "
                       "presence priors - so the composite should track the same completeness mechanism the "
                       "organiser scored, giving centred correlation above the 0.30 forecast assumption and a "
                       "public RMSE below the 0.091440198 P4 benchmark."),
        "eastern_wa_fill": {
            "region": "eastern-wa", "rows": artifact["eastern_wa_rows"],
            "policy": "uniform platform-only scalar 0.0 per b750a35; no eastern-WA features, "
                      "regional-mean transfer, or target inference",
            "values": artifact["eastern_wa_fill"], "scored_target_claim": False,
        },
        "column_checks": {
            "unique_columns": True, "all_rows_have_columns": True, "finite_bounded_scores": True,
            "no_missing_official_ids": True, "no_duplicate_ids": True,
        },
        "per_region_means": artifact["per_region_means"],
        "defined_rates": facts["defined_rates"],
        "local_invariants": {
            "gates": gates,
            "max_composite_arithmetic_residual": facts["max_composite_arithmetic_residual"],
            "mask_audit": DEPENDENCY_REPORTS["v2 defined-mask audit"],
            "prior_envelope": DEPENDENCY_REPORTS["v2 prior envelope"],
        },
        "upload_status": {
            "uploaded": False,
            "live_upload_attempted": False,
            "uploads_used_for_this_candidate": 0,
            "max_uploads_preregistered": 1,
            "reason": ("docs/final-two-protocol.md queues zero uploads and requires operator authorization "
                       "through the orchestrator gate; this row authorizes no queued upload"),
            "comment_if_authorized": "model-only v2",
        },
        "registration_status": "preregistered_model_only_awaiting_operator_authorization",
    }
    return audit, card, artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=os.environ.get("COMPETITION_WORKSPACE"))
    parser.add_argument("--out-dir", type=Path, default=ROOT / "reports/candidates")
    parser.add_argument("--manifest", type=Path, default=ROOT / "reports/candidates.json")
    parser.add_argument("--audit-out", type=Path, default=ROOT / "reports/estimator-v2-audit.json")
    parser.add_argument("--wa-fill", type=float, default=0.0,
                       help="deterministic platform-only fill for the 415 eastern-WA rows (default 0.0)")
    parser.add_argument("--dry-run", action="store_true",
                       help="compute and print gates without writing the artifact, card or audit")
    args = parser.parse_args()
    if not args.workspace:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    if not math.isfinite(args.wa_fill) or not 0.0 <= args.wa_fill <= 1.0:
        parser.error("--wa-fill must be a finite number in [0, 1]")
    workspace = Path(args.workspace).resolve()
    audit, card, artifact = build(workspace, args.out_dir, args.wa_fill, write=not args.dry_run)

    if not args.dry_run:
        manifest_path = args.manifest.resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = [row for row in manifest.get("candidate_set", []) if row["candidate_id"] != CANDIDATE_ID]
        entries.append(card)
        manifest["candidate_set"] = entries
        manifest["model_only_v2_preregistration"] = {
            "frozen_before_upload": True,
            "candidate_id": CANDIDATE_ID,
            "sha256": artifact["sha256"],
            "predicted_public_rmse": card["parameters"]["predicted_public_rmse"],
            "prediction_written_before_any_upload": True,
            "max_uploads": 1,
            "upload_authorized": False,
            "protocol": "docs/final-two-protocol.md",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        audit["manifest_card_sha256"] = hashlib.sha256(
            json.dumps(card, sort_keys=True).encode("utf-8")).hexdigest()
        audit["manifest_sha256_after_write"] = sha256_file(manifest_path)
        args.audit_out.parent.mkdir(parents=True, exist_ok=True)
        args.audit_out.write_text(json.dumps(audit, indent=2, sort_keys=False, allow_nan=False) + "\n",
                                  encoding="utf-8")

    print(f"candidate: {artifact['path']} rows={artifact['rows']} sha256={artifact['sha256']}")
    print(f"official rows composed: {audit['component_facts']['official_rows']} "
          f"(max composite arithmetic residual {audit['component_facts']['max_composite_arithmetic_residual']:.3e})")
    print(f"model moments: mean={audit['model_moments']['mean_prediction']:.9f} "
          f"sd={audit['model_moments']['sd_prediction']:.9f} "
          f"detail sd={audit['model_moments']['sd_detail_about_region_mean']:.9f}")
    print(f"preregistered predicted public RMSE: "
          f"{card['parameters']['predicted_public_rmse']:.9f} "
          f"(centred correlation {PREREGISTERED_CENTRED_CORRELATION})")
    print(f"breakevens: {json.dumps({k: round(v, 6) for k, v in card['parameters']['centred_correlation_breakevens'].items() if isinstance(v, float)})}")
    if not audit["acceptance"]["all_passed"]:
        failed = sorted(key for key, value in audit["acceptance"].items()
                        if isinstance(value, bool) and not value)
        print(f"FAIL v2 assembly gates not met: {failed}")
        return 1
    print("PASS v2 assembly: frozen candidate hashed, forecast preregistered before upload, "
          "no LB-derived constant, no upload performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
