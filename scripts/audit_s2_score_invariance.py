#!/usr/bin/env python3
"""Audit frozen S2 under row/column permutations and stable serialization.

Non-mutating. It uses official GEOID lists, provided feature/strata tables,
frozen C2 values and the frozen S2 recipe only; no target or score feedback.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import audit_shrink_h1b_rule_levels as h1b  # noqa: E402
import build_shrink_s2_assumption_bound as s2recipe  # noqa: E402

REGIONS = h1b.REGIONS
PREREG = ROOT / "config/s2-score-invariance-preregistered.json"
REPORT = ROOT / "reports/s2-score-invariance.json"
S2_PATH = ROOT / "reports/candidates/S2_h1b_assumption_bound_v1.csv"
S2B_PATH = ROOT / "reports/candidates/S2b_c2_expected_facility_mask_v1.csv"
C2_PATH = ROOT / "reports/candidates/C2_component_estimator_v0.csv"
ESTIMATES_PATH = ROOT / "reports/gap-estimates.csv"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def exact_id_index(ids: list[str], expected: set[str], label: str) -> dict[str, int]:
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label}: duplicate GEOID")
    actual = set(ids)
    missing, extra = expected - actual, actual - expected
    if missing or extra:
        raise ValueError(f"{label}: ID mismatch missing={len(missing)} extra={len(extra)}")
    return {geoid: i for i, geoid in enumerate(ids)}


def csv_map(path: Path, required: list[str], official_ids: set[str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Index official rows by GEOID; never parse prediction values off official scope."""
    out: dict[str, dict[str, str]] = {}
    order: list[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not set(required).issubset(reader.fieldnames):
            raise ValueError(f"{path}: invalid header")
        for row in reader:
            geoid = str(row["GEOID"])
            if geoid not in official_ids:
                continue
            if geoid in out:
                raise ValueError(f"{path}: duplicate official GEOID {geoid}")
            order.append(geoid)
            out[geoid] = {name: row[name] for name in required if name != "GEOID"}
    exact_id_index(order, official_ids, str(path))
    return out, order


def validate_maps(data: dict[str, dict[str, Any]], ids: list[str], expected_ids: set[str], label: str) -> None:
    exact_id_index(ids, expected_ids, label)
    for name, mapping in data.items():
        if set(mapping) != expected_ids or len(mapping) != len(expected_ids):
            raise ValueError(f"{label}/{name}: missing or duplicate IDs")


def compute_s2(official_order: list[tuple[str, str]], c2: dict[str, dict[str, Any]],
               estimates: dict[str, dict[str, Any]], features: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    scores: dict[str, dict[str, Any]] = {}
    for geoid, region in official_order:
        c2row, est, feature = c2[geoid], estimates[geoid], features[geoid]
        c2_components = {name: s2recipe.to_float(c2row[f"{name}_gap"], f"C2 {name}")
                         for name in ("transport", "building", "poi")}
        flags0 = {name: s2recipe.binary_flag(est[f"{name}_defined"], f"{name}_defined")
                  for name in ("transport", "building", "poi")}
        c2_composite = s2recipe.to_float(c2row["coverage_gap_score"], "C2 coverage")
        if abs(s2recipe.component_composite(c2_components, flags0) - c2_composite) > 1e-10:
            raise ValueError(f"C2 component contract mismatch for {geoid}")
        components, flags = s2recipe.adjusted_components(c2_components, est, feature)
        score = s2recipe.component_composite(components, flags)
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError(f"invalid recomputed S2 score for {geoid}")
        scores[geoid] = {"region": region, "components": components, "flags": flags, "score": score}
    return scores


def score_digest(scores: dict[str, dict[str, Any]], official_order: list[tuple[str, str]]) -> str:
    lines = ["GEOID,coverage_gap_score\n"]
    lines.extend(f"{geoid},{format(float(scores[geoid]['score']), '.17g')}\n" for geoid, _ in official_order)
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def max_score_delta(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]], expected: set[str]) -> float:
    if set(a) != expected or set(b) != expected:
        raise ValueError("score maps do not exactly cover official IDs")
    return max((abs(float(a[g]["score"]) - float(b[g]["score"])) for g in expected), default=0.0)


def reverse_rows(data: dict[str, dict[str, Any]], ordered_ids: list[str]) -> dict[str, dict[str, Any]]:
    return {g: dict(data[g]) for g in reversed(ordered_ids)}


def reverse_columns(data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {g: dict(reversed(list(row.items()))) for g, row in data.items()}


def stable_round_trip(data: Any) -> Any:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return json.loads(encoded)


def toy_hand_check() -> dict[str, Any]:
    """Independent small fixture with a manual component/composite calculation."""
    c2 = {"transport": 0.2, "building": 0.8, "poi": 0.6}
    estimates = {
        "transport_defined": "1", "building_defined": "1", "poi_defined": "1",
        "fire_defined": "1", "fire_gap": "0.4",
        "ems_defined": "1", "ems_gap": "0.9",
        "schools_defined": "1", "schools_gap": "0.2",
        "cbp_defined": "1", "cbp_gap": "0.8",
    }
    feature = {"overture_building_microsoft_source_share": 0.25,
               "overture_poi_fire": 1, "overture_poi_ems": 0, "overture_poi_schools": 2}
    components, flags = s2recipe.adjusted_components(c2, estimates, feature)
    score = s2recipe.component_composite(components, flags)
    # Hand: building=.8*(1-.25)=.6; facilities=(.4+.2)/2=.3;
    # POI=(.3+.8)/2=.55; final=(.2+.6+.55)/3=.45.
    expected_components = {"transport": 0.2, "building": 0.6, "poi": 0.55}
    if any(abs(components[k] - v) > 1e-12 for k, v in expected_components.items()) or abs(score - 0.45) > 1e-12:
        raise AssertionError(f"toy fixture differs from hand calculation: {components}, {score}")
    return {"components": components, "defined": flags, "score": score,
            "hand_calculated_score": 0.45, "matches": True}


def read_all(workspace: Path) -> dict[str, Any]:
    config = json.loads(PREREG.read_text(encoding="utf-8"))
    if not config.get("registered_before_audit_output"):
        raise ValueError("invariance protocol was not preregistered")
    expected_hashes = {
        "S2": (S2_PATH, config["frozen_outputs"]["S2"]["sha256"]),
        "S2b": (S2B_PATH, config["frozen_outputs"]["S2b"]["sha256"]),
        "C2_components": (C2_PATH, config["frozen_inputs"]["C2_components"]["sha256"]),
        "gap_estimates": (ESTIMATES_PATH, config["frozen_inputs"]["gap_estimates"]["sha256"]),
        "S2_recipe_source": (Path(__file__).resolve().parents[0] / "build_shrink_s2_assumption_bound.py",
                             config["frozen_inputs"]["S2_recipe_source"]["sha256"]),
    }
    for name, (path, expected) in expected_hashes.items():
        if sha256_file(path) != expected:
            raise ValueError(f"frozen hash mismatch {name}: {path}")

    official_order, region_by_id, ids_by_region = h1b.read_official_ids(workspace)
    official_ids = set(region_by_id)
    config_counts = config["contract"]["regions"]
    if len(official_order) != config["contract"]["official_rows"] or {r: len(ids_by_region[r]) for r in REGIONS} != config_counts:
        raise ValueError("official sample scope differs from frozen contract")
    cfg_s2 = json.loads((ROOT / "config/shrink-s2-assumption-bound-preregistered.json").read_text())
    sample_hashes = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        expected = cfg_s2["frozen_inputs"]["official_samples"][region]["sha256"]
        actual = sha256_file(sample_path)
        if actual != expected:
            raise ValueError(f"official GEOID sample hash mismatch for {region}")
        sample_hashes[region] = actual

    c2_cols = ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]
    s2_cols = c2_cols
    c2, c2_template_order = s2recipe.read_csv_official(C2_PATH, official_ids, c2_cols, full_order=True)
    s2, s2_template_order = s2recipe.read_csv_official(S2_PATH, official_ids, s2_cols, full_order=True)
    s2b, s2b_template_order = s2recipe.read_csv_official(S2B_PATH, official_ids, s2_cols, full_order=True)
    if len(c2_template_order) != 9794 or len(set(c2_template_order)) != 9794 or not (c2_template_order == s2_template_order == s2b_template_order):
        raise ValueError("C2/S2/S2b template ID order mismatch")
    if any(set(x) != official_ids for x in (c2, s2, s2b)):
        raise ValueError("frozen C2/S2/S2b does not have exact official ID coverage")
    estimates = h1b.load_csv_map(ESTIMATES_PATH, h1b.GAP_COLUMNS, official_ids)
    if set(estimates) != official_ids:
        raise ValueError("gap estimate map does not cover exact official IDs")

    h1b_cfg = json.loads((ROOT / "config/shrink-h1b-rule-level-preregistered.json").read_text())
    features: dict[str, dict[str, Any]] = {}
    strata: dict[str, dict[str, Any]] = {}
    feature_hashes: dict[str, Any] = {}
    import pyarrow.parquet as pq
    for region in REGIONS:
        allowed = set(ids_by_region[region])
        fpath = workspace / "gap-estimator/features" / f"{region}-overture-tract-summary.parquet"
        spath = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        fp = h1b_cfg["pinned_inputs"]["provided_overture_tract_summaries"][region]["sha256"]
        stp = h1b_cfg["pinned_inputs"]["provided_strata"][region]["sha256"]
        if sha256_file(fpath) != fp or sha256_file(spath) != stp:
            raise ValueError(f"provided features/strata changed for {region}")
        feature_table = pq.read_table(fpath, columns=h1b.FEATURE_COLUMNS)
        strata_table = pq.read_table(spath, columns=h1b.STRATA_COLUMNS)
        # Named-column extraction is exercised independently of source schema order.
        features.update(h1b.load_parquet_map(fpath, h1b.FEATURE_COLUMNS, allowed))
        strata.update(h1b.load_parquet_map(spath, h1b.STRATA_COLUMNS, allowed))
        if set(str(r["GEOID"]) for r in feature_table.to_pylist() if str(r["GEOID"]) in allowed) != allowed:
            raise ValueError(f"feature source missing official IDs in {region}")
        if set(str(r["GEOID"]) for r in strata_table.to_pylist() if str(r["GEOID"]) in allowed) != allowed:
            raise ValueError(f"strata source missing official IDs in {region}")
        feature_hashes[region] = {"overture_sha256": fp, "strata_sha256": stp,
                                  "feature_schema_columns": feature_table.column_names,
                                  "strata_schema_columns": strata_table.column_names}
    if set(features) != official_ids or set(strata) != official_ids:
        raise ValueError("feature/strata inputs do not cover exact official IDs")
    return {"config": config, "official_order": official_order, "ids_by_region": ids_by_region,
            "official_ids": official_ids, "c2": c2, "s2": s2, "s2b": s2b,
            "estimates": estimates, "features": features, "strata": strata,
            "sample_hashes": sample_hashes, "feature_hashes": feature_hashes,
            "template_order": c2_template_order}


def run_audit(workspace: Path) -> dict[str, Any]:
    data = read_all(workspace)
    ids = data["official_ids"]
    order = data["official_order"]
    official_ids = [g for g, _ in order]
    # Baseline from frozen official feature and C2 maps.
    baseline_scores = compute_s2(order, data["c2"], data["estimates"], data["features"])
    frozen_by_id = {g: float(data["s2"][g]["coverage_gap_score"]) for g in ids}
    recompute_delta = max(abs(float(baseline_scores[g]["score"]) - frozen_by_id[g]) for g in ids)
    if recompute_delta > 1e-10:
        raise ValueError(f"frozen S2 recipe recomputation differs by {recompute_delta}")
    for g in ids:
        if not math.isfinite(frozen_by_id[g]) or not 0 <= frozen_by_id[g] <= 1:
            raise ValueError(f"frozen S2 score invalid for {g}")

    transformations: dict[str, Any] = {}
    # Reverse complete official-row iteration independently in all input maps.
    row_order = list(reversed(official_ids))
    row_maps = {name: reverse_rows(data[name], official_ids)
                for name in ("c2", "estimates", "features", "strata")}
    validate_maps(row_maps, row_order, ids, "row_permutation")
    row_scores = compute_s2(list(reversed(order)), row_maps["c2"], row_maps["estimates"], row_maps["features"])
    transformations["row_order"] = {"maximum_absolute_score_delta": max_score_delta(baseline_scores, row_scores, ids),
                                      "exact_id_coverage": len(row_maps["features"]) == len(ids),
                                      "input_row_order": "reversed independently; indexed by GEOID",
                                      "output_sha256": score_digest(row_scores, order)}

    # Reverse the order in every feature/component dictionary; code must use names.
    col_maps = {name: reverse_columns(data[name]) for name in ("c2", "estimates", "features", "strata")}
    validate_maps(col_maps, official_ids, ids, "column_permutation")
    col_scores = compute_s2(order, col_maps["c2"], col_maps["estimates"], col_maps["features"])
    transformations["column_order"] = {"maximum_absolute_score_delta": max_score_delta(baseline_scores, col_scores, ids),
                                         "exact_id_coverage": len(col_maps["features"]) == len(ids),
                                         "input_column_order": "reversed named fields; score computation uses named fields",
                                         "output_sha256": score_digest(col_scores, order)}

    # Canonical JSON round-trip catches unstable feature/map serialization.
    round_maps = stable_round_trip({name: data[name] for name in ("c2", "estimates", "features", "strata")})
    validate_maps(round_maps, official_ids, ids, "stable_serialization")
    json_scores = compute_s2(order, round_maps["c2"], round_maps["estimates"], round_maps["features"])
    transformations["stable_serialization"] = {"maximum_absolute_score_delta": max_score_delta(baseline_scores, json_scores, ids),
                                                "exact_id_coverage": len(round_maps["features"]) == len(ids),
                                                "serialization": "canonical JSON sort_keys=true, compact separators, allow_nan=false",
                                                "output_sha256": score_digest(json_scores, order)}

    toy = toy_hand_check()
    max_delta = max(x["maximum_absolute_score_delta"] for x in transformations.values())
    if max_delta > 1e-12:
        raise ValueError(f"semantics-preserving transformation exceeded 1e-12: {max_delta}")
    # Region counts before/after and duplicate/missing checks are explicit.
    coverage = {region: {"expected": len(region_ids), "observed": sum(1 for _, r in order if r == region),
                         "exact": sum(1 for _, r in order if r == region) == len(region_ids)}
                for region, region_ids in data["ids_by_region"].items()}
    report = {
        "schema_version": 1,
        "analysis_id": "S2_SCORE_INVARIANCE_V1",
        "result": "PASS",
        "preregistration": {"path": str(PREREG.relative_to(ROOT)), "sha256": sha256_file(PREREG)},
        "scope": {"official_rows": len(ids), "official_unique_ids": len(set(official_ids)),
                  "four_region_coverage": coverage, "all_ids_exactly_once": True,
                  "template_rows_in_frozen_outputs": len(data["template_order"]),
                  "template_only_rows_excluded_from_measurement": len(data["template_order"]) - len(ids)},
        "measure_before": {"frozen_S2_sha256": sha256_file(S2_PATH), "frozen_S2b_sha256": sha256_file(S2B_PATH),
                           "C2_sha256": sha256_file(C2_PATH), "gap_estimates_sha256": sha256_file(ESTIMATES_PATH),
                           "feature_strata_sha256_by_region": data["feature_hashes"],
                           "S2_recomputation_max_delta_vs_frozen": recompute_delta,
                           "no_prior_row_column_serialization_invariance_report": True},
        "toy_hand_fixture": toy,
        "transformations": transformations,
        "validation": {"max_transformation_delta": max_delta, "required_max_delta": 1e-12,
                       "all_transformations_pass": max_delta <= 1e-12,
                       "duplicate_ids": 0, "missing_or_extra_ids": 0,
                       "scores_finite_and_bounded_0_1": True,
                       "labels_or_reference_layers_or_external_data_used": False,
                       "leaderboard_or_scorecard_values_used": False,
                       "candidate_or_upload_selection": False},
        "output_hashes": {"frozen_S2_artifact_sha256": sha256_file(S2_PATH),
                          "recomputed_official_score_vector_sha256": score_digest(baseline_scores, order)},
        "input_hashes": {"audit_script_sha256": sha256_file(Path(__file__).resolve()),
                         "S2_recipe_source_sha256": sha256_file(ROOT / "scripts/build_shrink_s2_assumption_bound.py"),
                         "S2_preregistration_sha256": sha256_file(ROOT / "config/shrink-s2-assumption-bound-preregistered.json"),
                         "official_samples_sha256": data["sample_hashes"],
                         "feature_strata_sha256_by_region": data["feature_hashes"]},
        "reproduction": {"command": "uv run --with-requirements requirements.txt python scripts/audit_s2_score_invariance.py --workspace $COMPETITION_WORKSPACE",
                         "tests": "uv run --with-requirements requirements.txt --with pytest python -m pytest -q tests/test_s2_score_invariance.py"},
        "interpretation": "This is a non-mutating implementation-invariance check, not target validation or accuracy evidence.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report = run_audit(args.workspace.resolve())
    print(json.dumps({"result": report["result"], "official_rows": report["scope"]["official_rows"],
                      "max_delta": report["validation"]["max_transformation_delta"],
                      "report": str(REPORT)}, sort_keys=True))


if __name__ == "__main__":
    main()
