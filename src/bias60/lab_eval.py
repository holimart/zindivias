"""Lab-side evaluation of the 60 estimators (spec 3.2-3.3), with a contract the build must satisfy.

The lab is measured on external tracts only. This module is the *consumer* of the artifacts that
``bias60-external-lab-build`` must produce, so it defines their contract precisely and refuses to
publish anything unless every rail holds:

* the artifact set (manifest, tracts, estimators, labels, definedness) exists and is complete;
* the manifest carries source versions, SHA-256s, retrieval dates, the recipe variant and the results
  of the two RULES.md self-checks;
* no lab tract is an official or platform-template GEOID, and every lab tract records a
  nearest-study-tract distance at or beyond the preregistered buffer;
* undefined rows are never scored as zero, and only *defined* rows enter an RMSE;
* at least five groups are held out, and the Track B weight vector is accepted only if it beats equal
  weights in at least 75% of them;
* the only thing allowed to travel from the lab to a submission is the weight vector plus its
  normalisation constants, recorded with a SHA-256 and the lab commit.

When the lab is absent the module returns ``measurement_status: not_run_missing_lab`` with every
measurement field null. It never invents a number, and the contract gates still all have to pass so a
half-built lab cannot quietly become an evaluation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pyarrow.parquet as pq

from bias60.determinism import canonical_bytes, normalized_bytes  # noqa: E402
from bias60 import lab_plan

ARTIFACTS = ("manifest.json", "tracts.parquet", "estimators.parquet", "labels.parquet",
             "defineness.json")

TRACT_COLUMNS = ["lab_tract_id", "lab_group", "state_fips", "county_fips", "centroid_x",
                 "centroid_y", "nearest_study_tract_km"]
ESTIMATOR_COLUMNS = ["lab_tract_id", "lab_group", "idea", "family", "component", "channel",
                     "value", "defined"]
LABEL_COLUMNS = ["lab_tract_id", "lab_group", "component", "label", "defined", "recipe_variant_id"]

MANIFEST_REQUIRED = ["lab_commit", "recipe_variant_id", "exclusion_policy_sha256", "buffer_km",
                     "sources", "self_checks", "groups", "study_polygon_source"]

# A reference-layer path appearing anywhere in this row's access log would mean the evaluator started
# computing targets itself instead of consuming the build's artifacts.
REFERENCE_LAYER_TOKENS = ["prisecroads", "usbuildingfootprints", "globalmlbuildingfootprints",
                          "nationalmap", "sciencebase", "cbp23", "zbp23", "tl_2020_", "tl_2024_",
                          "overturemaps-us-west-2"]


# --------------------------------------------------------------------------- artifact loading
class LabAbsent(Exception):
    """Raised when the lab artifacts are not there at all, as opposed to present and wrong."""


def label_component_for(channel: str) -> str:
    """Spec 3.2 fits twice: per component, then a composite layer. A channel names its target.

    ``composite`` scores every implemented idea against the composite label; ``target:R`` scores only
    the ideas of component R against that component's label. Anything else is a contract violation.
    """
    if channel == "composite":
        return "composite"
    if channel.startswith("target:") and channel.split("target:", 1)[1]:
        return channel.split("target:", 1)[1]
    raise ValueError(f"channel {channel!r} is not 'composite' or 'target:<COMPONENT>'")


def _table(lab_root: Path, name: str, required: list[str]) -> dict[str, list[Any]]:
    path = lab_root / name
    schema = pq.read_schema(path)
    present = [field.name for field in schema]
    missing = [column for column in required if column not in present]
    if missing:
        raise ValueError(f"{name}: missing columns {missing}")
    extra = [column for column in present if column not in required]
    if extra:
        raise ValueError(f"{name}: unexpected columns {extra} (the contract is exact)")
    table = pq.read_table(path, columns=required)
    return {column: table.column(column).to_pylist() for column in required}


def load_lab(lab_root: Path) -> dict[str, Any]:
    """Load the lab artifact set, or raise :class:`LabAbsent` when it is not built."""
    lab_root = Path(lab_root)
    if not lab_root.is_dir() or not any((lab_root / name).exists() for name in ARTIFACTS):
        raise LabAbsent(str(lab_root))
    missing = [name for name in ARTIFACTS if not (lab_root / name).is_file()]
    if missing:
        raise ValueError(f"lab artifact set incomplete, missing {missing}")
    manifest = json.loads((lab_root / "manifest.json").read_text(encoding="utf-8"))
    definedness = json.loads((lab_root / "defineness.json").read_text(encoding="utf-8"))
    missing_fields = [field for field in MANIFEST_REQUIRED if field not in manifest]
    if missing_fields:
        raise ValueError(f"manifest is missing fields {missing_fields}")
    return {"root": str(lab_root),
            "manifest": manifest,
            "defineness": definedness,
            "tracts": _table(lab_root, "tracts.parquet", TRACT_COLUMNS),
            "estimators": _table(lab_root, "estimators.parquet", ESTIMATOR_COLUMNS),
            "labels": _table(lab_root, "labels.parquet", LABEL_COLUMNS)}


# --------------------------------------------------------------------------- contract checks
def check_provenance(manifest: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    problems: list[str] = []
    if not str(manifest.get("lab_commit", "")).strip():
        problems.append("lab_commit is empty")
    if not str(manifest.get("recipe_variant_id", "")).strip():
        problems.append("recipe_variant_id is empty")
    buffer_km = manifest.get("buffer_km")
    if not isinstance(buffer_km, (int, float)) or buffer_km < config["exclusion"]["buffer_km_min"]:
        problems.append(f"buffer_km {buffer_km!r} is below the preregistered minimum")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        problems.append("sources must be a non-empty list")
    else:
        for entry in sources:
            for field in ("id", "url", "version", "sha256", "retrieved_utc"):
                if not str(entry.get(field, "")).strip():
                    problems.append(f"source {entry.get('id', '?')} is missing {field}")
    checks = manifest.get("self_checks") or {}
    road = checks.get("state_road_ratio_in_organiser_band") or {}
    hospital = checks.get("hospital_ratio_check") or {}
    band = config["recipe"]["road_band"]
    if not road.get("passed"):
        problems.append("state road-ratio self-check did not pass")
    elif not (band[0] - road.get("tolerance", 0.0) <= road.get("worst_ratio")
              <= band[1] + road.get("tolerance", 0.0)):
        problems.append(f"worst road ratio {road.get('worst_ratio')} outside the frozen band")
    if not hospital.get("passed"):
        problems.append("hospital-ratio self-check did not pass")
    elif abs(hospital.get("observed_ratio", -1.0) - config["recipe"]["hospital_target"]) \
            > config["recipe"]["hospital_relative_tolerance"] * config["recipe"]["hospital_target"]:
        problems.append(f"hospital ratio {hospital.get('observed_ratio')} too far from "
                        f"{config['recipe']['hospital_target']}")
    if not str(manifest.get("study_polygon_source", "")).strip():
        problems.append("study_polygon_source is empty; the buffer cannot be audited")
    return {"problems": sorted(set(problems)),
            "groups": sorted({str(group) for group in manifest.get("groups", [])}),
            "source_count": len(sources) if isinstance(sources, list) else 0}


def check_exclusion(lab: dict[str, Any], policy: lab_plan.ExclusionPolicy,
                    config: dict[str, Any]) -> dict[str, Any]:
    tracts = lab["tracts"]
    ids = [str(value) for value in tracts["lab_tract_id"]]
    collisions = sorted(set(ids) & policy.excluded_geoids)
    buffer_km = float(lab["manifest"]["buffer_km"])
    too_close: list[str] = []
    for lab_id, distance in zip(ids, tracts["nearest_study_tract_km"]):
        if distance is None or float(distance) < config["exclusion"]["buffer_km_min"]:
            too_close.append(lab_id)
    study_states = {value.zfill(2) for value in policy.study_states}
    same_state_no_buffer = sorted(
        lab_id for lab_id, state in zip(ids, tracts["state_fips"])
        if str(state).zfill(2) in study_states and buffer_km <= 0.0)
    duplicate_ids = sorted({lab_id for lab_id in ids if ids.count(lab_id) > 1})
    return {"lab_tracts": len(ids),
            "distinct_lab_tracts": len(set(ids)),
            "collisions_with_official_or_template": collisions,
            "nearest_distance_below_buffer": too_close,
            "same_state_with_no_buffer": same_state_no_buffer,
            "duplicate_lab_tract_ids": duplicate_ids,
            "policy_union_excluded": len(policy.excluded_geoids)}


def check_rows(lab: dict[str, Any], implemented: list[str],
               config: dict[str, Any]) -> dict[str, Any]:
    """Cross-check estimators against labels and the idea registry, and count definedness."""
    estimators = lab["estimators"]
    labels = lab["labels"]
    problems: list[str] = []
    ideas = sorted({str(idea) for idea in estimators["idea"]})
    if ideas != sorted(implemented):
        problems.append(f"estimator ideas {ideas} differ from the implemented registry set "
                        f"{sorted(implemented)}")
    pairs = list(zip(estimators["lab_tract_id"], estimators["idea"], estimators["channel"]))
    duplicated = sorted({pair for pair in pairs if pairs.count(pair) > 1})
    if duplicated:
        problems.append(f"{len(duplicated)} duplicated (tract, idea, channel) rows")
    tract_ids = {str(value) for value in lab["tracts"]["lab_tract_id"]}
    unknown = sorted({str(tract) for tract in estimators["lab_tract_id"]} - tract_ids)
    if unknown:
        problems.append(f"{len(unknown)} estimator rows name an unknown lab tract")
    undefined_rows = [index for index, flag in enumerate(estimators["defined"]) if not flag]
    miszeroed = [estimators["lab_tract_id"][index] for index in undefined_rows
                 if float(estimators["value"][index]) != 0.0]
    if miszeroed:
        problems.append(f"{len(miszeroed)} undefined estimator rows are not exactly 0.0")
    outside_unit = [estimators["lab_tract_id"][index] for index, flag in
                    enumerate(estimators["defined"]) if flag
                    and not (0.0 <= float(estimators["value"][index]) <= 1.0)]
    if outside_unit:
        problems.append(f"{len(outside_unit)} defined estimator values fall outside [0, 1]")
    label_pairs = list(zip(labels["lab_tract_id"], labels["component"]))
    duplicated_labels = sorted({pair for pair in label_pairs if label_pairs.count(pair) > 1})
    if duplicated_labels:
        problems.append(f"{len(duplicated_labels)} duplicated (tract, component) label rows")
    bad_labels = [labels["lab_tract_id"][index] for index, flag in enumerate(labels["defined"])
                  if flag and not (0.0 <= float(labels["label"][index]) <= 1.0)]
    if bad_labels:
        problems.append(f"{len(bad_labels)} defined labels fall outside [0, 1]")
    coverage = definedness_coverage(lab)
    for group, per_component in coverage.items():
        for component, share in per_component.items():
            if share < config["acceptance"]["minimum_labelled_share"]:
                problems.append(f"group {group} component {component} defined share "
                                f"{share:.4f} below the floor")
    channels_seen = sorted({str(channel) for channel in estimators["channel"]})
    for channel in channels_seen:
        label_component_for(channel)
        component = label_component_for(channel)
        if component != "composite" and component not in {str(value) for value in labels["component"]}:
            problems.append(f"channel {channel} targets component {component}, which has no labels")
    if "composite" not in channels_seen:
        problems.append("no composite channel; the composite layer of spec 3.2 cannot be fitted")
    groups = sorted({str(group) for group in lab["tracts"]["lab_group"]})
    if len(groups) < config["acceptance"]["minimum_held_out_groups"]:
        problems.append(f"{len(groups)} groups is below the required "
                        f"{config['acceptance']['minimum_held_out_groups']} held-out groups")
    return {"problems": sorted(set(problems)), "ideas": ideas, "groups": groups,
            "undefined_rows": len(undefined_rows), "coverage": coverage,
            "label_rows": len(label_pairs), "estimator_rows": len(pairs)}


def definedness_coverage(lab: dict[str, Any]) -> dict[str, dict[str, float]]:
    tracts = lab["tracts"]
    per_group_total: dict[str, int] = {}
    for group in tracts["lab_group"]:
        per_group_total[str(group)] = per_group_total.get(str(group), 0) + 1
    labels = lab["labels"]
    defined: dict[str, dict[str, int]] = {}
    for group, component, flag in zip(labels["lab_group"], labels["component"], labels["defined"]):
        if flag:
            defined.setdefault(str(group), {}).setdefault(str(component), 0)
            defined[str(group)][str(component)] += 1
    coverage: dict[str, dict[str, float]] = {}
    for group, total in sorted(per_group_total.items()):
        coverage[group] = {component: round(defined.get(group, {}).get(component, 0) / total, 6)
                           for component in sorted(config_components(lab))}
    return coverage


def config_components(lab: dict[str, Any]) -> list[str]:
    return sorted({str(component) for component in lab["labels"]["component"]})


# --------------------------------------------------------------------------- numeric core
def _prepare_matrix(columns: dict[str, dict[str, float]], order: list[str],
                    defined_mask: dict[str, bool]) -> tuple[np.ndarray, list[str]]:
    names = sorted(columns)
    matrix = np.column_stack([np.array([columns[name][geoid] for geoid in order], dtype=float)
                              for name in names])
    return matrix, names


def rmse(predicted: np.ndarray, actual: np.ndarray) -> float:
    if predicted.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean((np.asarray(predicted, float) - np.asarray(actual, float)) ** 2)))


def nnls_ridge(evidence: np.ndarray, target: np.ndarray, w0: np.ndarray, lam: float,
               big_m: float | None = None) -> np.ndarray:
    """min ||Ew - y||^2 + lam ||w - w0||^2 subject to w >= 0, solved by Lawson-Hanson NNLS.

    The ridge term enters as the augmented rows ``sqrt(lam) I`` against ``sqrt(lam) w0``, so the
    positivity constraint from spec 3.2 stays exactly a cone constraint. ``big_m`` appends a row of
    ``big_m`` ones against ``big_m`` to push ``sum(w)`` towards one (the convex-combination option).
    """
    evidence = np.asarray(evidence, float)
    target = np.asarray(target, float).reshape(-1)
    columns = evidence.shape[1]
    blocks = [evidence, np.sqrt(lam) * np.eye(columns)]
    targets = [target, np.sqrt(lam) * np.asarray(w0, float).reshape(-1)]
    if big_m is not None:
        blocks.append(big_m * np.ones((1, columns)))
        targets.append(np.array([big_m], float))
    system = np.vstack(blocks)
    right = np.concatenate(targets)
    return _lawson_hanson(system, right)


def _lawson_hanson(a: np.ndarray, b: np.ndarray, tolerance: float = 1e-12,
                   max_iterations: int = 5000) -> np.ndarray:
    """Lawson-Hanson active-set NNLS; deterministic and dependency-free."""
    rows, columns = a.shape
    passive: list[int] = []
    active = list(range(columns))
    weights = np.zeros(columns)
    for _ in range(max_iterations):
        gradient = a.T @ (b - a @ weights)
        if not active or np.max(gradient[active]) <= tolerance:
            break
        pick = active[int(np.argmax(gradient[active]))]
        passive.append(pick)
        active.remove(pick)
        for _inner in range(max_iterations):
            sub = a[:, passive]
            solution, *_ = np.linalg.lstsq(sub, b, rcond=None)
            if np.min(solution) > -tolerance:
                break
            if not passive:
                break
            ratios = [weights[index] / (weights[index] - value)
                      for index, value in zip(passive, solution) if value <= tolerance]
            if not ratios:
                break
            theta = min(ratios)
            weights[:] = 0.0
            for position, index in enumerate(passive):
                weights[index] = weights[index] + theta * (solution[position] - weights[index])
            zeros = [index for position, index in enumerate(passive)
                     if abs(weights[index]) <= tolerance]
            for index in zeros:
                passive.remove(index)
                active.append(index)
            if not passive:
                break
        if passive:
            sub = a[:, passive]
            solution, *_ = np.linalg.lstsq(sub, b, rcond=None)
            weights[:] = 0.0
            for position, index in enumerate(passive):
                weights[index] = max(float(solution[position]), 0.0)
    return weights


def group_folds(groups: list[str]) -> list[tuple[str, list[int], list[int]]]:
    """Leave-one-state-group-out folds; deterministic ordering by group name."""
    unique = sorted(set(groups))
    folds = []
    for held_out in unique:
        test = [index for index, group in enumerate(groups) if group == held_out]
        train = [index for index, group in enumerate(groups) if group != held_out]
        folds.append((held_out, train, test))
    return folds


def _inner_folds(counties: list[str], groups: list[str],
                 folds: int) -> tuple[list[tuple[list[int], list[int]]], str]:
    """Deterministic inner folds: county-blocked, or group-blocked if a fold set is too thin.

    Spec 3.2 asks for county-blocked inner folds for lambda. A tiny lab (one county per held-out
    group) cannot supply them, so the fallback is group-blocked folds, and a single-county single-group
    fit falls back to in-sample selection; the mode is recorded rather than hidden.
    """
    if len({county for county in counties}) >= 2:
        return county_blocked_folds(counties, groups, folds), "county_blocked"
    if len({group for group in groups}) >= 2:
        return group_inner_folds(groups, folds), "group_blocked_fallback"
    rows = list(range(len(groups)))
    return [([index for index in rows if index != row], [row])
            for row in rows], "in_sample_fallback"


def group_inner_folds(groups: list[str], folds: int) -> list[tuple[list[int], list[int]]]:
    unique = sorted(set(groups))
    assignment = {group: index % max(1, folds) for index, group in enumerate(unique)}
    out = []
    for fold in range(max(1, folds)):
        test = [index for index, group in enumerate(groups) if assignment[group] == fold]
        train = [index for index, group in enumerate(groups) if assignment[group] != fold]
        if train and test:
            out.append((train, test))
    return out


def county_blocked_folds(counties: list[str], groups: list[str],
                         folds: int) -> list[tuple[list[int], list[int]]]:
    """Deterministic county-blocked folds: whole counties stay together, sorted assignment."""
    unique = sorted(set(counties))
    buckets: dict[str, list[str]] = {}
    for county, group in zip(counties, groups):
        buckets.setdefault(group, []).append(county)
    ordered = [county for group in sorted(buckets) for county in sorted(set(buckets[group]))]
    seen: list[str] = []
    for county in ordered:
        if county not in seen:
            seen.append(county)
    assignment = {county: index % max(1, folds) for index, county in enumerate(seen)}
    out = []
    for fold in range(max(1, folds)):
        test = [index for index, county in enumerate(counties) if assignment[county] == fold]
        train = [index for index, county in enumerate(counties) if assignment[county] != fold]
        out.append((train, test))
    return out


def fit_with_lambda_grid(evidence: np.ndarray, target: np.ndarray, groups: list[str],
                         counties: list[str], w0: np.ndarray, grid: list[float],
                         inner_folds: int, convex: bool) -> dict[str, Any]:
    """Choose lambda by county-blocked inner CV, then refit on everything."""
    big_m = 1e4 if convex else None
    folds, inner_mode = _inner_folds(counties, groups, inner_folds)
    best = None
    for lam in grid:
        errors: list[float] = []
        for train, test in folds:
            if not train or not test:
                continue
            weights = nnls_ridge(evidence[train], target[train], w0, lam, big_m)
            errors.append(rmse(evidence[test] @ weights, target[test]))
        if not errors:
            continue
        mean_error = float(np.mean(errors))
        if best is None or mean_error < best["inner_cv_rmse"] - 1e-15:
            best = {"lam": lam, "inner_cv_rmse": mean_error,
                    "worst_inner_fold": float(np.max(errors))}
    if best is None:
        raise ValueError("no usable inner fold; the lab design is broken")
    weights = nnls_ridge(evidence, target, w0, best["lam"], big_m)
    total = float(np.sum(weights))
    return {"lam": best["lam"], "inner_cv_rmse": best["inner_cv_rmse"],
            "inner_fold_mode": inner_mode,
            "worst_inner_fold": best["worst_inner_fold"],
            "weights": [float(value) for value in weights],
            "weights_sum": total,
            "convex_sum_deviation": abs(total - 1.0) if convex else None,
            "weights_sha256": hashlib.sha256(
                canonical_bytes([round(float(value), 12) for value in weights])).hexdigest()}


def grouped_cv_predictions(evidence: np.ndarray, target: np.ndarray, groups: list[str],
                           counties: list[str], w0: np.ndarray, grid: list[float],
                           inner_folds: int, convex: bool) -> tuple[
                               np.ndarray, list[dict[str, Any]], list[dict[str, Any]]]:
    """Outer leave-one-group-out loop: fit on the other groups, predict the held-out one."""
    predicted = np.zeros(evidence.shape[0])
    per_group: list[dict[str, Any]] = []
    weights_report: list[dict[str, Any]] = []
    for held_out, train, test in group_folds(groups):
        fit = fit_with_lambda_grid(evidence[train], target[train],
                                  [groups[index] for index in train],
                                  [counties[index] for index in train], w0, grid, inner_folds,
                                  convex)
        predicted[test] = evidence[test] @ np.asarray(fit["weights"], float)
        per_group.append({"group": held_out, "rows": len(test),
                          "rmse": fit_rmse(evidence[test], target[test], fit["weights"]),
                          "rows_train": len(train)})
        weights_report.append({"group": held_out, **fit})
    return predicted, per_group, weights_report


def fit_rmse(evidence: np.ndarray, target: np.ndarray, weights: list[float]) -> float:
    return rmse(evidence @ np.asarray(weights, float), target)


def equal_weight_predictions(evidence: np.ndarray, w0: np.ndarray) -> np.ndarray:
    return evidence @ w0


def win_rate(per_group: list[dict[str, Any]], equal_per_group: dict[str, float]) -> dict[str, Any]:
    comparisons = []
    for entry in per_group:
        baseline = equal_per_group.get(entry["group"])
        if baseline is None or not np.isfinite(baseline) or not np.isfinite(entry["rmse"]):
            continue
        comparisons.append({"group": entry["group"], "track_b_rmse": entry["rmse"],
                            "equal_weight_rmse": baseline,
                            "beats_equal_weights": bool(entry["rmse"] < baseline - 1e-12)})
    wins = sum(1 for entry in comparisons if entry["beats_equal_weights"])
    total = len(comparisons)
    return {"comparisons": comparisons, "groups_compared": total, "groups_won": wins,
            "win_rate": (wins / total) if total else None,
            "rule": "accepted only if the win rate is at least 75% of held-out groups"}


def partial_correlation(matrix: np.ndarray, target: np.ndarray, index: int) -> float:
    """Correlation of the i-th column and the target after removing every other column."""
    others = [column for column in range(matrix.shape[1]) if column != index]
    if not others:
        return float("nan")
    design = np.column_stack([np.ones(matrix.shape[0]), matrix[:, others]])
    left = matrix[:, index] - design @ np.linalg.lstsq(design, matrix[:, index], rcond=None)[0]
    right = target - design @ np.linalg.lstsq(design, target, rcond=None)[0]
    if np.std(left) == 0.0 or np.std(right) == 0.0:
        return float("nan")
    return float(np.corrcoef(left, right)[0, 1])


def residual_correlation(matrix: np.ndarray, target: np.ndarray,
                         names: list[str]) -> dict[str, Any]:
    residuals = target[:, None] - matrix
    correlation = np.corrcoef(residuals.T) if residuals.shape[1] > 1 else np.zeros((0, 0))
    off = [abs(float(correlation[i, j])) for i in range(len(names)) for j in range(i + 1, len(names))
           if np.isfinite(correlation[i, j])]
    return {"names": names,
            "matrix": [[None if not np.isfinite(value) else round(float(value), 12)
                        for value in row] for row in correlation],
            "off_diagonal_mass": float(np.mean(off)) if off else None,
            "pairs": len(off)}


def krogh_vedelsby_ambiguity(members: np.ndarray, target: np.ndarray) -> float | None:
    """A = mean over outputs of mean over members of (f_i - ybar)^2 (Krogh & Vedelsby)."""
    if members.ndim != 2 or members.shape[0] == 0:
        return None
    mean_prediction = np.mean(members, axis=0)
    return float(np.mean(np.mean((members - mean_prediction[None, :]) ** 2, axis=0)))


def mtmm(matrix: np.ndarray, names: list[str], components: dict[str, str]) -> dict[str, Any]:
    correlation = np.corrcoef(matrix.T) if matrix.shape[0] > 1 and matrix.shape[1] > 1 \
        else np.zeros((len(names), len(names)))
    within: list[float] = []
    between: list[float] = []
    for i, left in enumerate(names):
        for j in range(i + 1, len(names)):
            value = correlation[i, j] if correlation.size else float("nan")
            if not np.isfinite(value):
                continue
            if components.get(left) == components.get(names[j]):
                within.append(abs(float(value)))
            else:
                between.append(abs(float(value)))
    return {"single_member": len(names) < 2,
            "within_component_mean_abs_rho": float(np.mean(within)) if within else None,
            "between_component_mean_abs_rho": float(np.mean(between)) if between else None,
            "within_pairs": len(within), "between_pairs": len(between),
            "convergent_minus_discriminant": ((float(np.mean(within)) - float(np.mean(between)))
                                              if within and between else None)}


def shift_weights(lab_features: np.ndarray, study_features: np.ndarray,
                  clip_percentiles: tuple[float, float], seed: int) -> dict[str, Any]:
    """Shimodaira density-ratio weights from a deterministic logistic domain classifier.

    Label-free features only; the study side never contributes a label, and the weights are used to
    reweight lab tracts, never to score a study tract.
    """
    features = np.vstack([lab_features, study_features])
    labels = np.concatenate([np.zeros(lab_features.shape[0]), np.ones(study_features.shape[0])])
    standardised = (features - features.mean(axis=0)) / np.where(features.std(axis=0) == 0, 1.0,
                                                                 features.std(axis=0))
    design = np.column_stack([np.ones(standardised.shape[0]), standardised])
    order = np.argsort(np.arange(design.shape[0]) * 2654435761 % (design.shape[0] + 1) + seed)
    design, labels = design[order], labels[order]
    coefficients = np.zeros(design.shape[1])
    for _ in range(2000):
        logits = design @ coefficients
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -30.0, 30.0)))
        gradient = design.T @ (probabilities - labels) / design.shape[0]
        coefficients -= 0.5 * gradient
    lab_design = design[:lab_features.shape[0]]
    logits = lab_design @ coefficients
    probability = 1.0 / (1.0 + np.exp(-np.clip(logits, -30.0, 30.0)))
    ratio = probability / np.clip(1.0 - probability, 1e-9, None)
    low, high = np.percentile(ratio, clip_percentiles[0]), np.percentile(ratio, clip_percentiles[1])
    clipped = np.clip(ratio, low, high)
    return {"clip_low": float(low), "clip_high": float(high),
            "weight_mean": float(np.mean(clipped)), "weight_max": float(np.max(clipped)),
            "effective_sample_size": float(clipped.sum() ** 2 / np.sum(clipped ** 2)),
            "weights_sha256": hashlib.sha256(
                canonical_bytes([round(float(value), 12) for value in clipped])).hexdigest()}


def area_of_applicability(lab_features: np.ndarray, study_features: np.ndarray) -> dict[str, Any]:
    """Meyer-Pebesma style applicability: Mahalanobis distance in the lab covariate space."""
    centre = np.mean(lab_features, axis=0)
    covariance = np.cov(lab_features, rowvar=False)
    covariance = np.atleast_2d(covariance) + 1e-9 * np.eye(centre.shape[0])
    inverse = np.linalg.pinv(covariance)
    def distances(block: np.ndarray) -> np.ndarray:
        delta = block - centre
        return np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", delta, inverse, delta), 0.0))
    lab_distance = distances(lab_features)
    study_distance = distances(study_features)
    if lab_distance.size == 0 or study_distance.size == 0:
        return {"outside_share": None}
    threshold = float(np.max(lab_distance))
    return {"lab_max_distance": threshold,
            "study_outside_share": float(np.mean(study_distance > threshold)),
            "study_median_distance": float(np.median(study_distance)),
            "study_max_distance": float(np.max(study_distance))}


def stability_selection(evidence: np.ndarray, target: np.ndarray, groups: list[str],
                        w0: np.ndarray, grid: list[float], inner_folds: int, draws: int,
                        seed: int, threshold: float) -> dict[str, Any]:
    """Bootstrap over whole groups; keep columns nonzero in at least ``threshold`` of the fits."""
    unique = sorted(set(groups))
    rng = np.random.default_rng(seed)
    names = list(range(evidence.shape[1]))
    nonzero = np.zeros(len(names))
    successes = 0
    for _ in range(draws):
        picked = [int(index) for index in rng.choice(len(unique), size=len(unique), replace=True)]
        rows = [index for index, group in enumerate(groups)
                if group in {unique[position] for position in picked}]
        if len({groups[index] for index in rows}) < 2:
            continue
        weights = nnls_ridge(evidence[rows], target[rows], w0, grid[len(grid) // 2])
        nonzero += (weights > 1e-9)
        successes += 1
    share = [float(value / successes) if successes else None for value in nonzero]
    return {"bootstrap_draws_used": successes, "bootstrap_draws_requested": draws,
            "seed": seed, "nonzero_share": share,
            "selected_indices": [name for name, value in zip(names, share)
                                 if value is not None and value >= threshold],
            "threshold": threshold}


# --------------------------------------------------------------------------- Track A variants
TRACK_A_REQUIREMENTS = {
    "A1": {"name": "physical anchors and equal weights", "requires_ideas": [], "runnable": True},
    "A2": {"name": "one-factor / spectral weighting", "requires_ideas": [], "runnable": True,
           "minimum_members_per_component": 3},
    "A3": {"name": "Dawid-Skene for facility misses",
           "requires_ideas": ["X1", "Sc2", "Mx5", "P3", "S3", "D2", "X5"]},
    "A4": {"name": "cross-component self-consistency ridge prior",
           "requires_ideas": ["T5", "Tm4", "M1", "M5"]},
}


def factor_weights(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rank-one factor loadings and unique variances from the off-diagonal covariance."""
    covariance = np.cov(matrix, rowvar=False)
    covariance = np.atleast_2d(covariance)
    off = covariance.copy()
    np.fill_diagonal(off, 0.0)
    loadings_squared = np.maximum(np.max(off, axis=1), 0.0)
    loadings = np.sqrt(loadings_squared)
    unique = np.maximum(np.diag(covariance) - loadings_squared, 1e-9)
    return loadings, unique


def score_track_a_variant(variant: str, matrix: np.ndarray, target: np.ndarray,
                          groups: list[str], implemented: list[str],
                          components: dict[str, str]) -> dict[str, Any]:
    """How a rule-clean Track A variant scores on the lab. Never writes a study-side prediction."""
    requirement = TRACK_A_REQUIREMENTS[variant]
    missing = sorted(set(requirement.get("requires_ideas", [])) - set(implemented))
    if missing:
        return {"variant": variant, "name": requirement["name"], "status": "not_runnable",
                "reason": "the ideas it consumes are not implemented yet",
                "missing_ideas": missing, "rmse": None, "per_group": []}
    if variant == "A2":
        members = len(components)
        if members < requirement["minimum_members_per_component"]:
            return {"variant": variant, "name": requirement["name"], "status": "not_runnable",
                    "reason": (f"spectral weighting needs at least "
                               f"{requirement['minimum_members_per_component']} members per "
                               f"component and the registry has {members} implemented ideas"),
                    "missing_ideas": [], "rmse": None, "per_group": []}
    per_group: list[dict[str, Any]] = []
    for held_out, train, test in group_folds(groups):
        if variant == "A1":
            weights = np.full(matrix.shape[1], 1.0 / matrix.shape[1])
        else:
            loadings, unique = factor_weights(matrix[train])
            weights = loadings / unique
            total = float(np.sum(weights))
            weights = weights / total if total > 0 else np.full(matrix.shape[1],
                                                                1.0 / matrix.shape[1])
        per_group.append({"group": held_out, "rmse": rmse(matrix[test] @ weights, target[test])})
    rmse_values = [entry["rmse"] for entry in per_group if np.isfinite(entry["rmse"])]
    return {"variant": variant, "name": requirement["name"], "status": "scored",
            "missing_ideas": [], "rmse": float(np.mean(rmse_values)) if rmse_values else None,
            "worst_group_rmse": float(np.max(rmse_values)) if rmse_values else None,
            "per_group": per_group}


# --------------------------------------------------------------------------- measurement run
def channel_matrix(lab: dict[str, Any], channel: str,
                   implemented: list[str]) -> tuple[np.ndarray, np.ndarray, list[str],
                                                    list[str], list[str], dict[str, str]]:
    """Aligned evidence matrix, composite label, groups and counties for one scoring channel."""
    estimators = lab["estimators"]
    tracts = lab["tracts"]
    tract_meta = {str(tract): {"group": str(group), "county": str(county)}
                  for tract, group, county in zip(tracts["lab_tract_id"], tracts["lab_group"],
                                                  tracts["county_fips"])}
    columns: dict[str, dict[str, float]] = {}
    components: dict[str, str] = {}
    label_component = label_component_for(channel)
    for index in range(len(estimators["lab_tract_id"])):
        if str(estimators["channel"][index]) != channel or not estimators["defined"][index]:
            continue
        idea = str(estimators["idea"][index])
        components.setdefault(idea, str(estimators["component"][index]))
        columns.setdefault(idea, {})[str(estimators["lab_tract_id"][index])] = float(
            estimators["value"][index])
    names = sorted(set(columns) & set(implemented))
    if not names:
        return np.zeros((0, 0)), np.zeros(0), [], [], [], {}
    usable = sorted(tract for tract in tract_meta
                    if all(tract in columns[name] for name in names)
                    and np.isfinite(_label_for(lab["labels"], tract, label_component)))
    matrix = np.column_stack([[columns[name][tract] for tract in usable] for name in names])
    target = np.array([_label_for(lab["labels"], tract, label_component) for tract in usable],
                      float)
    groups = [tract_meta[tract]["group"] for tract in usable]
    counties = [tract_meta[tract]["county"] for tract in usable]
    return matrix, target, groups, counties, names, components


def _label_for(labels: dict[str, list[Any]], tract: str, component: str) -> float:
    for candidate, name, value, defined in zip(labels["lab_tract_id"], labels["component"],
                                               labels["label"], labels["defined"]):
        if str(candidate) == tract and str(name) == component and defined:
            return float(value)
    return float("nan")


def measure(lab: dict[str, Any], config: dict[str, Any], implemented: list[str]) -> dict[str, Any]:
    """Run the whole spec 3.2-3.3 lab evaluation on loaded lab rows."""
    channels = sorted({str(channel) for channel in lab["estimators"]["channel"]})
    grid = [float(value) for value in config["weights"]["lambda_grid"]]
    per_channel: dict[str, Any] = {}
    for channel in channels:
        matrix, target, groups, counties, names, components = channel_matrix(
            lab, channel, implemented)
        if matrix.shape[0] == 0 or matrix.shape[1] == 0:
            per_channel[channel] = {"status": "no_usable_rows", "ideas": []}
            continue
        w0 = np.full(matrix.shape[1], 1.0 / matrix.shape[1])
        predicted, per_group, _weights_report = grouped_cv_predictions(
            matrix, target, groups, counties, w0, grid, config["weights"]["inner_folds"],
            config["weights"]["convex"])
        equal_predicted = equal_weight_predictions(matrix, w0)
        equal_per_group = {held_out: rmse(equal_predicted[test], target[test])
                           for held_out, _train, test in group_folds(groups)}
        overall = rmse(predicted, target)
        baseline = rmse(equal_predicted, target)
        win = win_rate(per_group, equal_per_group)
        accepted = bool(win["win_rate"] is not None
                        and win["win_rate"] >= config["acceptance"]["minimum_track_b_win_rate"])
        final_fit = fit_with_lambda_grid(matrix, target, groups, counties, w0, grid,
                                        config["weights"]["inner_folds"],
                                        config["weights"]["convex"])
        per_idea = []
        for position, name in enumerate(names):
            drop_rmse = None
            if matrix.shape[1] > 1:
                without = np.delete(matrix, position, axis=1)
                w0_other = np.full(without.shape[1], 1.0 / without.shape[1])
                predicted_other, _, _ = grouped_cv_predictions(without, target, groups, counties,
                                                               w0_other, grid,
                                                               config["weights"]["inner_folds"],
                                                               config["weights"]["convex"])
                drop_rmse = rmse(predicted_other, target) - overall
            per_idea.append({
                "idea": name, "component": components.get(name, "unknown"), "rows": matrix.shape[0],
                "correlation_with_label": (round(float(np.corrcoef(matrix[:, position],
                                                                   target)[0, 1]), 12)
                                           if np.std(matrix[:, position]) > 0 else None),
                "partial_correlation_with_label": (round(partial_correlation(matrix, target,
                                                                             position), 12)
                                                   if matrix.shape[1] > 1 else None),
                "drop_column_delta_rmse": (round(drop_rmse, 12) if drop_rmse is not None else None)})
        per_channel[channel] = {
            "status": "measured", "ideas": names, "rows": int(matrix.shape[0]),
            "groups": sorted(set(groups)),
            "rmse_track_b": overall, "rmse_equal_weights": baseline,
            "per_group_track_b": per_group,
            "per_group_equal_weights": [{"group": key, "rmse": value}
                                        for key, value in sorted(equal_per_group.items())],
            "worst_group_rmse": (max((entry["rmse"] for entry in per_group
                                      if np.isfinite(entry["rmse"])), default=None)),
            "win_rate_vs_equal": win,
            "track_b_accepted_by_rule": accepted,
            "reported_as_not_beating_equal_weights": bool(not accepted),
            "weight_vector": final_fit["weights"],
            "weight_vector_sha256": final_fit["weights_sha256"],
            "lambda_chosen": final_fit["lam"],
            "convex_sum_deviation": final_fit["convex_sum_deviation"],
            "incremental_value": per_idea,
            "residual_correlation": residual_correlation(matrix, target, names),
            # every idea is gap-scale, so each column is itself a candidate member prediction of
            # the label and the Krogh-Vedelsby ambiguity of that member set is meaningful
            "krogh_vedelsby_ambiguity": krogh_vedelsby_ambiguity(matrix, target),
            "mtmm": mtmm(matrix, names, components),
            "stability_selection": stability_selection(
                matrix, target, groups, w0, grid, config["weights"]["inner_folds"],
                config["weights"]["stability_draws"], config["weights"]["stability_seed"],
                config["weights"]["stability_threshold"]),
            "track_a_variants": {variant: score_track_a_variant(variant, matrix, target, groups,
                                                                implemented, components)
                                 for variant in sorted(TRACK_A_REQUIREMENTS)},
        }
    shift = {"status": "not_wired",
             "reason": ("the Shimodaira shift correction and the area-of-applicability measure need "
                        "study-side covariates, and this row opens no study-side file; the functions "
                        "are implemented and unit-tested, and they may only be wired behind a "
                        "preregistered study-feature allowlist"),
             "implemented": True, "requires": "study_side_label_free_feature_allowlist",
             "allowed_when_status": ["measured"],
             "traineable": bool(shift_weights and area_of_applicability)}
    track_a_summary: dict[str, list[str]] = {}
    for variant in sorted(TRACK_A_REQUIREMENTS):
        statuses = sorted({channel["track_a_variants"][variant]["status"]
                           for channel in per_channel.values() if channel.get("track_a_variants")})
        track_a_summary[variant] = statuses or ["no_channel"]
    return {"channels": per_channel, "channels_seen": channels,
            "track_a_summary": track_a_summary, "shift_correction": shift}


def crossing_surface(measurement: dict[str, Any], lab: dict[str, Any]) -> dict[str, Any]:
    """Everything allowed to leave the lab: the weight vector and its constants, only."""
    vectors = []
    for channel, entry in measurement.get("channels", {}).items():
        if entry.get("status") != "measured":
            continue
        vectors.append({"channel": channel, "weights": entry["weight_vector"],
                        "weights_sha256": entry["weight_vector_sha256"],
                        "lambda": entry["lambda_chosen"]})
    return {"allowed_fields": ["weights", "intercept", "shrinkage", "normalisation_constants",
                               "lab_commit", "lab_manifest_sha256"],
            "weight_vectors": vectors,
            "lab_commit": lab["manifest"]["lab_commit"],
            "lab_manifest_sha256": hashlib.sha256(canonical_bytes(lab["manifest"])).hexdigest(),
            "forbidden_fields_observed": []}
