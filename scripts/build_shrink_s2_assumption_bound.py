#!/usr/bin/env python3
"""Build the preregistered assumption-bound S2 variant and comparison table.

This is a label-free rule counterfactual using only official sample GEOIDs,
frozen C2 component outputs, existing per-type feature-only gap estimates, and
provided Overture/strata tables. It reads no leaderboard data, C0, or removed
reference data, and never modifies S. The 415 template-only rows are written as
formatting fills and are excluded from all comparisons.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
CANDIDATE_ID = "S2_h1b_assumption_bound_v1"
CANDIDATE_REL = f"reports/candidates/{CANDIDATE_ID}.csv"
CANDIDATE_PATH = ROOT / CANDIDATE_REL
PREREG_PATH = ROOT / "config/shrink-s2-assumption-bound-preregistered.json"
COMPARE_PATH = ROOT / "reports/shrink-s2-assumption-bound-comparison.json"
WRITEUP_PATH = ROOT / "docs/writeup/shrink-s2-assumption-bound.md"
REGISTRY_PATH = ROOT / "reports/candidates.json"
C2_PATH = ROOT / "reports/candidates/C2_component_estimator_v0.csv"
S_PATH = ROOT / "reports/candidates/S_crossmethod_reliability_shrink_v1.csv"
ESTIMATES_PATH = ROOT / "reports/gap-estimates.csv"
H1B_SCRIPT_PATH = ROOT / "scripts/audit_shrink_h1b_rule_levels.py"
H1B_PREREG_PATH = ROOT / "config/shrink-h1b-rule-level-preregistered.json"

H1B_SPEC = importlib.util.spec_from_file_location("audit_shrink_h1b_rule_levels", H1B_SCRIPT_PATH)
if H1B_SPEC is None or H1B_SPEC.loader is None:
    raise RuntimeError("cannot import frozen H1b stress-test helpers")
H1B = importlib.util.module_from_spec(H1B_SPEC)
H1B_SPEC.loader.exec_module(H1B)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_csv_official(path: Path, official_ids: set[str], required: list[str], *, full_order: bool = False) -> tuple[dict[str, dict[str, str]], list[str]]:
    data: dict[str, dict[str, str]] = {}
    order: list[str] = []
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if header is None or len(header) != len(set(header)) or not set(required).issubset(header):
            raise ValueError(f"{path}: invalid/missing columns; required={required}")
        gi = header.index("GEOID")
        indices = {name: header.index(name) for name in required if name != "GEOID"}
        for row in reader:
            if len(row) != len(header):
                raise ValueError(f"{path}: malformed row width")
            geoid = row[gi]
            if full_order:
                order.append(geoid)
            if geoid not in official_ids:
                # Deliberately do not inspect any prediction fields on format-only rows.
                continue
            if geoid in data:
                raise ValueError(f"{path}: duplicate official GEOID {geoid}")
            data[geoid] = {name: row[idx] for name, idx in indices.items()}
    return data, order


def to_float(value: Any, name: str, *, allow_missing: bool = False) -> float | None:
    if value is None or (isinstance(value, str) and value.strip() in ("", "NA", "nan", "NaN")):
        if allow_missing:
            return None
        raise ValueError(f"{name}: missing numeric value")
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name}: invalid numeric value") from exc
    if not math.isfinite(result):
        if allow_missing:
            return None
        raise ValueError(f"{name}: non-finite numeric value")
    return result


def binary_flag(value: Any, name: str) -> int:
    flag = int(value)
    if flag not in (0, 1):
        raise ValueError(f"{name}: expected binary flag")
    return flag


def component_composite(components: dict[str, float], flags: dict[str, int]) -> float:
    defined = [components[name] for name in ("transport", "building", "poi") if flags[name]]
    return sum(defined) / len(defined) if defined else 0.0


def adjusted_components(c2: dict[str, float], estimates: dict[str, str], feature: dict[str, Any]) -> tuple[dict[str, float], dict[str, int]]:
    flags = {name: binary_flag(estimates[f"{name}_defined"], f"{name}_defined")
             for name in ("transport", "building")}
    source_share = to_float(feature.get("overture_building_microsoft_source_share"),
                            "overture_building_microsoft_source_share", allow_missing=True)
    if source_share is not None and not 0.0 <= source_share <= 1.0:
        raise ValueError("Microsoft source share outside [0,1]")
    building = c2["building"] * (1.0 - source_share) if source_share is not None else c2["building"]

    retained_facilities = []
    for kind in H1B.FACILITIES:
        raw_count = to_float(feature.get(f"overture_poi_{kind}"), f"overture_poi_{kind}")
        if raw_count < 0:
            raise ValueError(f"negative Overture count for {kind}")
        per_type_defined = binary_flag(estimates[f"{kind}_defined"], f"{kind}_defined")
        per_type_gap = to_float(estimates[f"{kind}_gap"], f"{kind}_gap")
        if not 0.0 <= per_type_gap <= 1.0:
            raise ValueError(f"per-type {kind} gap outside [0,1]")
        if raw_count != 0 and per_type_defined:
            retained_facilities.append((per_type_gap, 1))
        else:
            retained_facilities.append((per_type_gap, 0))
    cbp_gap = to_float(estimates["cbp_gap"], "cbp_gap")
    if not 0.0 <= cbp_gap <= 1.0:
        raise ValueError("CBP-proxy gap outside [0,1]")
    cbp_defined = binary_flag(estimates["cbp_defined"], "cbp_defined")
    poi, poi_defined = H1B.compose_poi(retained_facilities, cbp_gap, cbp_defined)
    components = {"transport": c2["transport"], "building": building, "poi": poi}
    flags["poi"] = int(poi_defined)
    for name, val in components.items():
        if not 0.0 <= val <= 1.0:
            raise ValueError(f"S2 {name} component outside [0,1]")
        if not flags[name] and val != 0.0:
            components[name] = 0.0
    return components, flags


def registry_array_end(raw: str, marker: str = "candidate_set") -> int:
    key_at = raw.find(f'"{marker}"')
    if key_at < 0:
        raise ValueError(f"registry has no {marker} array")
    start = raw.find("[", key_at)
    if start < 0:
        raise ValueError("registry candidate_set opening bracket missing")
    stack: list[str] = []
    quoted = escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            opener = stack.pop()
            if (opener, char) not in (("[", "]"), ("{", "}")):
                raise ValueError("registry JSON bracket mismatch")
            if not stack:
                if char != "]":
                    raise ValueError("candidate_set is not an array")
                return index
    raise ValueError("unterminated candidate_set")


def insert_candidate_card(raw: str, card: dict[str, Any]) -> str:
    marker = f'"candidate_id": "{CANDIDATE_ID}"'
    if marker in raw:
        marker_at = raw.find(marker)
        start = raw.rfind("{", 0, marker_at)
        stack: list[str] = []
        quoted = escaped = False
        end = None
        for index in range(start, len(raw)):
            char = raw[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
                continue
            if char == '"':
                quoted = True
            elif char in "[{":
                stack.append(char)
            elif char in "]}":
                opener = stack.pop()
                if (opener, char) not in (("[", "]"), ("{", "}")):
                    raise ValueError("S2 registry entry JSON bracket mismatch")
                if not stack:
                    end = index
                    break
        if start < 0 or end is None:
            raise ValueError("could not locate existing S2 registry card")
        old_card = json.loads(raw[start:end + 1])
        if (old_card.get("candidate_id") != CANDIDATE_ID
                or old_card.get("artifact") != card.get("artifact")
                or old_card.get("sha256") != card.get("sha256")
                or old_card.get("purpose") != "improvement"
                or old_card.get("upload_authorized") is not False):
            raise ValueError("existing S2 registry entry does not match frozen artifact")
        if old_card == card:
            return raw
        replacement = json.dumps(card, indent=2, ensure_ascii=False)
        replacement = "\n".join("    " + line for line in replacement.splitlines())
        return raw[:start] + replacement + raw[end + 1:]
    end = registry_array_end(raw)
    insertion = json.dumps(card, indent=2, ensure_ascii=False)
    insertion = "\n".join("    " + line for line in insertion.splitlines())
    return raw[:end].rstrip() + ",\n" + insertion + raw[end:]


def summarize(values: list[float], flags: list[int]) -> dict[str, Any]:
    defined = [float(value) for value, defined_flag in zip(values, flags) if defined_flag]
    return {
        "defined_rows": len(defined),
        "defined_mean": sum(defined) / len(defined) if defined else None,
        "undefined_rows": len(values) - len(defined),
    }


def run_build(workspace: Path) -> dict[str, Any]:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    if not prereg.get("registered_before_s2_value_computation"):
        raise ValueError("S2 protocol was not preregistered")
    expected = prereg["frozen_inputs"]
    pinned_paths = {
        "C2_components": C2_PATH, "gap_estimates": ESTIMATES_PATH,
        "estimator_source": ROOT / "scripts/estimate_gaps.py",
        "S_comparison_artifact": S_PATH, "H1b_stress_test_source": H1B_SCRIPT_PATH,
        "H1b_preregistration": H1B_PREREG_PATH,
    }
    input_hashes = {}
    for key, path in pinned_paths.items():
        actual = sha256_file(path)
        input_hashes[key] = actual
        if actual != expected[key]["sha256"]:
            raise ValueError(f"frozen input hash mismatch: {key}")

    official_order, official_to_region, ids_by_region = H1B.read_official_ids(workspace)
    official_ids = set(official_to_region)
    sample_hashes = {r: sha256_file(workspace / "samples" / f"{r}-sample-submission.csv") for r in REGIONS}
    for region in REGIONS:
        if sample_hashes[region] != expected["official_samples"][region]["sha256"]:
            raise ValueError(f"official sample hash mismatch: {region}")

    c2_data, template_order = read_csv_official(
        C2_PATH, official_ids,
        ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"],
        full_order=True)
    s_data, s_template_order = read_csv_official(
        S_PATH, official_ids, ["GEOID", "coverage_gap_score"], full_order=True)
    if len(template_order) != prereg["scope"]["platform_template_rows"] or len(set(template_order)) != len(template_order):
        raise ValueError("C2 template GEOID order/uniqueness mismatch")
    if s_template_order != template_order:
        raise ValueError("S and C2 full-template GEOID orders differ")
    if len(set(template_order) - official_ids) != prereg["scope"]["platform_format_only_rows"]:
        raise ValueError("unexpected template-only GEOID count")
    if set(c2_data) != official_ids or set(s_data) != official_ids:
        raise ValueError("C2/S official scored GEOID coverage mismatch")

    estimate_rows = H1B.load_csv_map(ESTIMATES_PATH, H1B.GAP_COLUMNS, official_ids)
    if set(estimate_rows) != official_ids:
        raise ValueError("gap-estimates official ID coverage mismatch")
    pconfig = json.loads(H1B_PREREG_PATH.read_text(encoding="utf-8"))
    feature_inputs, strata_inputs = {}, {}
    input_provenance = {}
    for region in REGIONS:
        feature_path = workspace / "gap-estimator/features" / f"{region}-overture-tract-summary.parquet"
        strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        feature_pin = pconfig["pinned_inputs"]["provided_overture_tract_summaries"][region]["sha256"]
        strata_pin = pconfig["pinned_inputs"]["provided_strata"][region]["sha256"]
        if sha256_file(feature_path) != feature_pin or sha256_file(strata_path) != strata_pin:
            raise ValueError(f"provided feature/strata hash mismatch: {region}")
        allowed = set(ids_by_region[region])
        feature_inputs.update(H1B.load_parquet_map(feature_path, H1B.FEATURE_COLUMNS, allowed))
        strata_inputs.update(H1B.load_parquet_map(strata_path, H1B.STRATA_COLUMNS, allowed))
        input_provenance[region] = {
            "feature_summary_sha256": sha256_file(feature_path),
            "feature_columns_read": H1B.FEATURE_COLUMNS,
            "strata_sha256": sha256_file(strata_path),
            "strata_columns_read": H1B.STRATA_COLUMNS,
            "official_rows": len(allowed),
        }
    if set(feature_inputs) != official_ids or set(strata_inputs) != official_ids:
        raise ValueError("feature/strata official ID coverage mismatch")

    s2: dict[str, dict[str, Any]] = {}
    c2_composites: dict[str, float] = {}
    s_composites: dict[str, float] = {}
    for geoid, region in official_order:
        c2row = c2_data[geoid]
        c2_components = {name: to_float(c2row[f"{name}_gap"], f"C2 {name}_gap")
                         for name in ("transport", "building", "poi")}
        if any(not 0.0 <= value <= 1.0 for value in c2_components.values()):
            raise ValueError(f"C2 component outside [0,1] for {geoid}")
        estimates = estimate_rows[geoid]
        original_flags = {name: binary_flag(estimates[f"{name}_defined"], f"{name}_defined")
                          for name in ("transport", "building", "poi")}
        c2_composite = to_float(c2row["coverage_gap_score"], "C2 coverage_gap_score")
        recomposed_c2 = component_composite(c2_components, original_flags)
        if not math.isclose(c2_composite, recomposed_c2, abs_tol=1e-10):
            raise ValueError(f"C2 composite differs from defined-component mean for {geoid}")
        s_score = to_float(s_data[geoid]["coverage_gap_score"], "S coverage_gap_score")
        components, flags = adjusted_components(c2_components, estimates, feature_inputs[geoid])
        s2_score = component_composite(components, flags)
        if not 0.0 <= s_score <= 1.0 or not 0.0 <= c2_composite <= 1.0 or not 0.0 <= s2_score <= 1.0:
            raise ValueError(f"out-of-range composite at {geoid}")
        s_composites[geoid] = s_score
        c2_composites[geoid] = c2_composite
        s2[geoid] = {"components": components, "flags": flags, "score": s2_score, "region": region}

    table: dict[str, Any] = {}
    for region in REGIONS:
        ids = ids_by_region[region]
        s_vals = [s_composites[g] for g in ids]
        c2_vals = [c2_composites[g] for g in ids]
        s2_vals = [s2[g]["score"] for g in ids]
        entries = {
            "S_crossmethod_reliability_shrink_v1": {
                "mean_composite_level": sum(s_vals) / len(ids),
                "component_means": None,
                "component_means_note": "Not identifiable from S artifact; it contains only GEOID and composite coverage_gap_score.",
                "share_tracts_changed_vs_S": 0.0,
                "share_tracts_changed_vs_C2": sum(abs(s_composites[g] - c2_composites[g]) > 1e-12 for g in ids) / len(ids),
            },
            "S2_h1b_assumption_bound_v1": {
                "mean_composite_level": sum(s2_vals) / len(ids),
                "component_means": {
                    name: summarize([s2[g]["components"][name] for g in ids], [s2[g]["flags"][name] for g in ids])
                    for name in ("transport", "building", "poi")
                },
                "share_tracts_changed_vs_S": sum(abs(s2[g]["score"] - s_composites[g]) > 1e-12 for g in ids) / len(ids),
                "share_tracts_changed_vs_C2": sum(abs(s2[g]["score"] - c2_composites[g]) > 1e-12 for g in ids) / len(ids),
            },
            "C2_component_estimator_v0": {
                "mean_composite_level": sum(c2_vals) / len(ids),
                "component_means": {
                    name: summarize([to_float(c2_data[g][f"{name}_gap"], f"C2 {name}") for g in ids],
                                    [binary_flag(estimate_rows[g][f"{name}_defined"], f"{name}_defined") for g in ids])
                    for name in ("transport", "building", "poi")
                },
                "share_tracts_changed_vs_S": sum(abs(c2_composites[g] - s_composites[g]) > 1e-12 for g in ids) / len(ids),
                "share_tracts_changed_vs_C2": 0.0,
            },
        }
        table[region] = {"official_tracts": len(ids), "models": entries}

    # Freeze a full-template-shaped artifact, using only C2's GEOID ordering. Do not read
    # any C2 or S prediction cells for the 415 format-only rows.
    candidate_path = CANDIDATE_PATH
    with candidate_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"])
        for geoid in template_order:
            if geoid in s2:
                row = s2[geoid]
                writer.writerow([geoid, *(format(row["components"][name], ".17g") for name in ("transport", "building", "poi")), format(row["score"], ".17g")])
            else:
                writer.writerow([geoid, "0", "0", "0", "0"])
    artifact_sha = sha256_file(candidate_path)

    assumptions = [
        prereg["explicit_recipe"]["building"]["assumption"],
        prereg["explicit_recipe"]["poi_facilities"]["assumption"],
        "Feature-derived C2 component estimates and internal establishment proxy are not hidden reference values.",
        "The 415 eastern-WA rows are deterministic formatting fills only and are outside the official 9,379 scored tracts.",
        "The result is a label-free model construction; hidden-target accuracy and target RMSE are not identifiable.",
    ]
    card = {
        "candidate_id": CANDIDATE_ID,
        "name": "S2 H1b assumption-bound rule counterfactual",
        "artifact": CANDIDATE_REL,
        "sha256": artifact_sha,
        "rows": len(template_order),
        "columns": ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"],
        "pre_registered": True,
        "preregistered_card_path": "config/shrink-s2-assumption-bound-preregistered.json",
        "preregistered_card_sha256": sha256_file(PREREG_PATH),
        "build_script_sha256": sha256_file(Path(__file__).resolve()),
        "purpose": "improvement",
        "description": "Explicit H1b assumption-bound transformation of frozen C2 component estimates; not target-accuracy evidence and not an upload authorization.",
        "predicted_score": None,
        "predicted_score_scope": "Label-free tract-level candidate predictions only; organizer target score and target RMSE are not identifiable without labels/reference counts.",
        "predicted_score_basis": "C2 feature-only component outputs transformed under the preregistered Microsoft-source-overlap and Overture-zero-facility assumptions; no fitted parameters.",
        "target_RMSE_forecast": None,
        "target_RMSE_forecast_limitation": "No labels or permitted organizer-reference counts are available; neither S2 nor S accuracy can be inferred from this comparison.",
        "assumptions": assumptions,
        "parameters": {"fitted_coefficients": [], "S_style_shrink_applied": False,
                        "official_composite_rule": "mean of defined transport, building, and POI components"},
        "eastern_wa_fill": {"region": "eastern-wa", "rows": len(template_order) - len(official_ids),
                            "value": 0.0, "scored_target_claim": False},
        "column_checks": {"unique_columns": True, "all_rows_have_columns": True,
                           "finite_bounded_scores": True},
        "upload_authorized": False,
        "registration_status": "frozen_no_upload",
    }
    raw_registry = REGISTRY_PATH.read_text(encoding="utf-8")
    updated_registry = insert_candidate_card(raw_registry, card)
    json.loads(updated_registry)  # structural check only; never inspect existing score fields.
    if updated_registry != raw_registry:
        REGISTRY_PATH.write_text(updated_registry, encoding="utf-8")

    report = {
        "schema_version": 1,
        "analysis": "S2 H1b assumption-bound candidate build and label-free comparison",
        "preregistration": {"path": "config/shrink-s2-assumption-bound-preregistered.json",
                            "sha256": sha256_file(PREREG_PATH),
                            "registered_before_s2_value_computation": True},
        "candidate": {"candidate_id": CANDIDATE_ID, "artifact": CANDIDATE_REL,
                      "sha256": artifact_sha, "registered_artifact_sha256": artifact_sha,
                      "rows": len(template_order), "official_scored_rows": len(official_ids),
                      "formatting_only_rows": len(template_order) - len(official_ids),
                      "upload_authorized": False, "upload_performed": False,
                      "target_accuracy_identifiable": False},
        "assumptions": assumptions,
        "recipe": prereg["explicit_recipe"],
        "comparison_scope": {"official_tracts": len(official_ids),
                             "region_counts": {r: len(ids_by_region[r]) for r in REGIONS},
                             "official_sample_score_placeholders_read": False,
                             "formatting_rows_excluded_from_comparison": True},
        "side_by_side_by_region": table,
        "overall_interpretation": "S2 is a deterministic assumption-bound alternate construction, not evidence that its levels or masks match the organizer target. S is likewise supported only as a label-free construction-agreement hypothesis. The comparison is descriptive and does not select an upload candidate.",
        "provenance": {"build_script_sha256": sha256_file(Path(__file__).resolve()),
                       "frozen_inputs_sha256": input_hashes,
                       "official_samples_sha256": sample_hashes,
                       "region_input_sha256": input_provenance,
                       "registry_sha256_after_freeze": hashlib.sha256(updated_registry.encode("utf-8")).hexdigest(),
                       "C2_template_geoid_order_rows": len(template_order),
                       "C2_template_unique_geoids": len(set(template_order))},
        "data_use": {"provided_data_only": True, "official_sample_GEOID_only": True,
                     "sample_score_placeholders_read": False, "C0_read_or_used": False,
                     "public_or_private_leaderboard_scores_or_scorecards_read_or_used": False,
                     "labels_or_target_values_read": False,
                     "removed_references_or_equivalents_read": False,
                     "external_data_read": False,
                     "S_artifact_used_only_as_label_free_comparison_baseline": True,
                     "A_family_component_decomposition_invented": False,
                     "existing_S_artifact_modified": False, "uploads": 0},
        "reproduction": {"command": "uv run --with-requirements requirements.txt python scripts/build_shrink_s2_assumption_bound.py --workspace $COMPETITION_WORKSPACE",
                         "tests": "uv run --with-requirements requirements.txt --with pytest python -m pytest -q tests/test_shrink_s2_assumption_bound.py"},
    }
    COMPARE_PATH.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    writeup = make_writeup(table, artifact_sha)
    WRITEUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    WRITEUP_PATH.write_text(writeup, encoding="utf-8")
    return report


def make_writeup(table: dict[str, Any], artifact_sha: str) -> str:
    lines = [
        "# S2: H1b assumption-bound rule counterfactual",
        "",
        "S2 is a label-free alternative construction built from frozen C2 component estimates, not an accuracy estimate. It applies two explicit, unverified assumptions: a Microsoft-sourced Overture building record is treated as exact overlap with the removed Microsoft reference, and a zero Overture fire/EMS/school category count is treated as a zero USGS reference count for dropping that facility type from the POI facilities half. The building component is multiplied by one minus the per-tract source share; missing shares leave C2 unchanged. POI is recomputed from retained defined facility types and the unchanged feature-only establishment half, and the final score is the mean of whichever transport, building, and POI components are defined. There are no fitted coefficients and no S-style shrinkage is stacked on top.",
        "",
        "Neither assumption is verified: Overture provenance does not prove footprint-level overlap, and an Overture category zero does not establish a USGS zero. C2 expected counts are feature-derived proxies, not organizer reference counts. S itself is a composite-only cross-construction artifact; its component decomposition is not identifiable from its file. Accordingly, component means for S are reported as unavailable rather than inferred. No labels, removed/equivalent references, external data, sample score placeholders, C0, leaderboard scores, or scorecards were used. S2 versus S versus C2 means and changed-tract shares are descriptive candidate-output comparisons only; they do not establish hidden-target RMSE or select an upload. No upload is authorized.",
        "",
        f"Frozen artifact: `{CANDIDATE_REL}` (SHA-256 `{artifact_sha}`); preregistration: `config/shrink-s2-assumption-bound-preregistered.json`.",
        "",
        "| Region | Model | Mean composite | Transport defined mean | Building defined mean | POI defined mean | Changed vs S | Changed vs C2 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for region in REGIONS:
        for model, values in table[region]["models"].items():
            comps = values.get("component_means") or {}
            comp_cols = [comps.get(name, {}).get("defined_mean") if isinstance(comps.get(name), dict) else None
                         for name in ("transport", "building", "poi")]
            fmt = lambda x: "not identifiable" if x is None else f"{x:.6f}"
            lines.append("| {} | {} | {} | {} | {} | {} | {:.3f} | {:.3f} |".format(
                region, model, fmt(values["mean_composite_level"]), *(fmt(x) for x in comp_cols),
                values["share_tracts_changed_vs_S"], values["share_tracts_changed_vs_C2"]))
    lines += ["", "The comparison table is in `reports/shrink-s2-assumption-bound-comparison.json`. These descriptive summaries are not validation against the organizer target.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report = run_build(args.workspace)
    print(json.dumps({"candidate_id": CANDIDATE_ID, "artifact_sha256": report["candidate"]["sha256"],
                      "official_scored_rows": report["candidate"]["official_scored_rows"],
                      "formatting_only_rows": report["candidate"]["formatting_only_rows"],
                      "comparison_report": str(COMPARE_PATH), "upload_authorized": False,
                      "upload_performed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
