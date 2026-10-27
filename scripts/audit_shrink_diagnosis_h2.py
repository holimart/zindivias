#!/usr/bin/env python3
"""Cross-construction reliability screen and preregistered shrink candidate builder.

Only frozen feature-derived predictions are consumed. This estimates agreement among
constructions, not accuracy against the hidden organizer targets.
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

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
CONFIG_PATH = ROOT / "config/shrink-diagnosis-preregistered.json"
REPORT_PATH = ROOT / "reports/shrink-diagnosis.json"
CANDIDATE_ID = "S_crossmethod_reliability_shrink_v1"
CANDIDATE_PATH = ROOT / "reports/candidates/S_crossmethod_reliability_shrink_v1.csv"
CONSTRUCTION_IDS = ["C2", "A1_physical_equal", "A2_spectral", "A3_dawid_skene"]
CANDIDATE_FILES = {
    "C2": ROOT / "reports/candidates/C2_component_estimator_v0.csv",
    "A1_physical_equal": ROOT / "reports/candidates/A1_physical_equal.csv",
    "A2_spectral": ROOT / "reports/candidates/A2_spectral.csv",
    "A3_dawid_skene": ROOT / "reports/candidates/A3_dawid_skene.csv",
}
SCORE_COLUMN = "coverage_gap_score"
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
REGION_SPECIFICITY_THRESHOLD = 0.10


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_score_csv(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {"GEOID", SCORE_COLUMN}.issubset(reader.fieldnames):
            raise ValueError(f"{path}: expected GEOID and {SCORE_COLUMN}")
        for row in reader:
            geoid = str(row["GEOID"])
            if geoid in out:
                raise ValueError(f"{path}: duplicate GEOID {geoid}")
            value = float(row[SCORE_COLUMN])
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{path}: invalid score for {geoid}")
            out[geoid] = value
    return out


def covariance_and_shares(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Population covariance for tract x construction matrix; returns cov, variances, shares."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 2 or x.shape[0] < 2 or x.shape[1] < 2 or not np.isfinite(x).all():
        raise ValueError("need a finite tract-by-construction matrix with at least two rows and methods")
    xc = x - x.mean(axis=0, keepdims=True)
    cov = (xc.T @ xc) / x.shape[0]
    variances = np.diag(cov).copy()
    shares = np.zeros(x.shape[1], dtype=np.float64)
    for j in range(x.shape[1]):
        others = [k for k in range(x.shape[1]) if k != j]
        if variances[j] > 0:
            shares[j] = float(np.mean(cov[j, others]) / variances[j])
    return cov, variances, np.clip(shares, 0.0, 1.0)


def reliability_weights(shares: np.ndarray) -> np.ndarray:
    weights = np.maximum(np.asarray(shares, dtype=np.float64), 0.0)
    if weights.sum() <= 0:
        return np.full(len(weights), 1.0 / len(weights))
    return weights / weights.sum()


def signal_total_c(cov: np.ndarray, weights: np.ndarray, total_variance: float | None = None) -> tuple[float, float, float]:
    """Return off-diagonal signal variance, total variance and clipped signal share c."""
    w = np.asarray(weights, dtype=np.float64)
    pair_weights = np.outer(w, w)
    np.fill_diagonal(pair_weights, 0.0)
    pair_mass = float(pair_weights.sum())
    signal = float((pair_weights * cov).sum() / pair_mass) if pair_mass > 0 else 0.0
    total = float(w @ cov @ w) if total_variance is None else float(total_variance)
    c = float(np.clip(signal / total, 0.0, 1.0)) if total > 0 else 0.0
    return signal, total, c


def summarize(values: np.ndarray) -> dict[str, float | int]:
    vals = np.asarray(values, dtype=np.float64)
    return {
        "n": int(vals.size), "mean": float(vals.mean()), "std_population": float(vals.std()),
        "min": float(vals.min()), "q05": float(np.quantile(vals, .05)),
        "median": float(np.quantile(vals, .5)), "q95": float(np.quantile(vals, .95)),
        "max": float(vals.max()), "exact_zero_count": int((vals == 0).sum()),
        "exact_one_count": int((vals == 1).sum()),
    }


def fit_policy(matrices: dict[str, np.ndarray], method_ids: list[str]) -> dict[str, Any]:
    """Fit global reliability weights and region/pool c using only named construction matrices."""
    idx = [CONSTRUCTION_IDS.index(name) for name in method_ids]
    regional_cov: dict[str, np.ndarray] = {}
    regional_vars: dict[str, np.ndarray] = {}
    regional_shares: dict[str, np.ndarray] = {}
    region_sizes: dict[str, int] = {}
    for region in REGIONS:
        x = matrices[region][:, idx]
        cov, var, shares = covariance_and_shares(x)
        regional_cov[region], regional_vars[region], regional_shares[region] = cov, var, shares
        region_sizes[region] = x.shape[0]
    total_var_by_method = sum(region_sizes[r] * regional_vars[r] for r in REGIONS)
    cross_cov_by_method = np.zeros(len(idx), dtype=np.float64)
    for r in REGIONS:
        cov = regional_cov[r]
        for j in range(len(idx)):
            cross_cov_by_method[j] += region_sizes[r] * np.mean([cov[j, k] for k in range(len(idx)) if k != j])
    pooled_shares = np.divide(cross_cov_by_method, total_var_by_method,
                              out=np.zeros_like(cross_cov_by_method), where=total_var_by_method > 0)
    pooled_shares = np.clip(pooled_shares, 0.0, 1.0)
    weights = reliability_weights(pooled_shares)
    region_metrics = {}
    for r in REGIONS:
        signal, total, c = signal_total_c(regional_cov[r], weights)
        region_metrics[r] = {"signal_variance": signal, "total_variance": total, "c_raw": signal / total if total > 0 else 0.0,
                             "c": c, "n": region_sizes[r]}
    pooled_signal = sum(region_sizes[r] * region_metrics[r]["signal_variance"] for r in REGIONS) / sum(region_sizes.values())
    pooled_total = sum(region_sizes[r] * region_metrics[r]["total_variance"] for r in REGIONS) / sum(region_sizes.values())
    pooled_c_raw = pooled_signal / pooled_total if pooled_total > 0 else 0.0
    pooled_c = float(np.clip(pooled_c_raw, 0.0, 1.0))
    range_c = max(region_metrics[r]["c"] for r in REGIONS) - min(region_metrics[r]["c"] for r in REGIONS)
    use_region_c = range_c >= REGION_SPECIFICITY_THRESHOLD
    return {
        "method_ids": method_ids,
        "pooled_signal_share_by_construction": {m: float(v) for m, v in zip(method_ids, pooled_shares)},
        "global_weights": {m: float(v) for m, v in zip(method_ids, weights)},
        "regions": region_metrics,
        "pooled_signal_variance": pooled_signal, "pooled_total_variance": pooled_total,
        "pooled_c_raw": pooled_c_raw, "pooled_c": pooled_c,
        "regional_c_range": range_c, "region_specificity_threshold": REGION_SPECIFICITY_THRESHOLD,
        "use_region_specific_c": use_region_c,
        "selected_c": {r: (region_metrics[r]["c"] if use_region_c else pooled_c) for r in REGIONS},
    }


def build_report_and_candidate(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from bias60.inputs import official_scored_pairs

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not config.get("registered_before_h2_measurement"):
        raise ValueError("H2 preregistration missing")
    for method in CONSTRUCTION_IDS:
        path = CANDIDATE_FILES[method]
        expected = next(row["sha256"] for row in config["input_constructions"]
                        if row["candidate_id"] == method)
        if sha256_file(path) != expected:
            raise ValueError(f"frozen input artifact hash changed: {method}")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    official_region = dict(official)
    if len(official) != 9379 or len(official_region) != 9379 or set(official_region.values()) != set(REGIONS):
        raise ValueError("official score-free GEOID scope mismatch")
    candidate_maps = {name: read_score_csv(path) for name, path in CANDIDATE_FILES.items()}
    for name, mapping in candidate_maps.items():
        if not set(official_region).issubset(mapping):
            raise ValueError(f"{name}: official GEOID coverage incomplete")
    matrices: dict[str, np.ndarray] = {}
    geoid_order: dict[str, list[str]] = {}
    for region in REGIONS:
        ids = [g for g, r in official if r == region]
        geoid_order[region] = ids
        matrices[region] = np.asarray([[candidate_maps[name][g] for name in CONSTRUCTION_IDS] for g in ids], dtype=np.float64)
    full_fit = fit_policy(matrices, CONSTRUCTION_IDS)

    # Leave one construction out. Training-only covariances determine weights and c;
    # the omitted construction is used only as a construction-disagreement diagnostic.
    fold_records = {}
    all_sq_shrunk, all_sq_unshrunk = [], []
    for heldout in CONSTRUCTION_IDS:
        train = [name for name in CONSTRUCTION_IDS if name != heldout]
        fold_fit = fit_policy(matrices, train)
        fold_sq, fold_sq_unshrunk = [], []
        region_folds = {}
        for region in REGIONS:
            xtrain = matrices[region][:, [CONSTRUCTION_IDS.index(name) for name in train]]
            target = matrices[region][:, CONSTRUCTION_IDS.index(heldout)]
            w = np.asarray([fold_fit["global_weights"][name] for name in train], dtype=np.float64)
            consensus = xtrain @ w
            mean = float(consensus.mean())
            c = fold_fit["selected_c"][region]
            pred = mean + c * (consensus - mean)
            unshrunk = consensus
            sq = (pred - target) ** 2
            sq0 = (unshrunk - target) ** 2
            region_folds[region] = {
                "n": len(target), "training_methods": train,
                "selected_c": c, "shrink_proxy_rmse": float(np.sqrt(sq.mean())),
                "unshrunk_consensus_proxy_rmse": float(np.sqrt(sq0.mean())),
                "mse_improvement": float(sq0.mean() - sq.mean()),
            }
            fold_sq.extend(sq.tolist())
            fold_sq_unshrunk.extend(sq0.tolist())
        all_sq_shrunk.extend(fold_sq)
        all_sq_unshrunk.extend(fold_sq_unshrunk)
        fold_records[heldout] = {
            "heldout_construction": heldout, "regions": region_folds,
            "pooled_shrink_proxy_rmse": float(np.sqrt(np.mean(fold_sq))),
            "pooled_unshrunk_proxy_rmse": float(np.sqrt(np.mean(fold_sq_unshrunk))),
        }
    cv_shrunk_rmse = float(np.sqrt(np.mean(all_sq_shrunk)))
    cv_unshrunk_rmse = float(np.sqrt(np.mean(all_sq_unshrunk)))
    cv_improvement = cv_unshrunk_rmse ** 2 - cv_shrunk_rmse ** 2
    # Preregistered gate: positive pooled LOCO MSE gain and no more than one region
    # may have negative average regional LOCO MSE gain.
    regional_avg_gain = {}
    for region in REGIONS:
        regional_avg_gain[region] = float(np.mean([
            fold_records[heldout]["regions"][region]["mse_improvement"]
            for heldout in CONSTRUCTION_IDS
        ]))
    worsened_regions = [r for r, gain in regional_avg_gain.items() if gain < 0.0]
    gate_pass = cv_improvement > 0.0 and len(worsened_regions) <= 1
    if not gate_pass:
        raise ValueError(f"preregistered H2 local validation gate failed: pooled MSE gain={cv_improvement}; worsened regions={worsened_regions}")

    # Use global reliability weights and selected pooled/region c to make exactly one candidate.
    selected_predictions: dict[str, dict[str, float]] = {}
    region_model_summary = {}
    for region in REGIONS:
        x = matrices[region]
        consensus = x @ np.asarray([full_fit["global_weights"][name] for name in CONSTRUCTION_IDS])
        mean = float(consensus.mean())
        c = full_fit["selected_c"][region]
        score = mean + c * (consensus - mean)
        if np.any(score < -1e-12) or np.any(score > 1 + 1e-12):
            raise ValueError(f"{region}: shrunk candidate out of [0,1]")
        score = np.clip(score, 0.0, 1.0)
        selected_predictions[region] = dict(zip(geoid_order[region], score.tolist()))
        region_model_summary[region] = {
            "n": len(score), "regional_consensus_mean_toward_which_we_shrink": mean,
            "selected_c": c, "unshrunk_consensus": summarize(consensus),
            "shrunk_candidate": summarize(score),
            "mean_score_preserved": math.isclose(float(score.mean()), mean, abs_tol=1e-12),
            "component_constructions": {
                method: summarize(x[:, j]) for j, method in enumerate(CONSTRUCTION_IDS)
            },
        }

    # The platform template is read only for GEOID/region. The 415 out-of-scope rows
    # receive the repository's documented formatting-only zero fill.
    template_path = workspace / "SampleSubmission.csv"
    with template_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {"GEOID", "region"}.issubset(reader.fieldnames):
            raise ValueError("platform template lacks GEOID/region")
        template = [(str(row["GEOID"]), str(row["region"])) for row in reader]
    if len(template) != 9794 or len({g for g, _ in template}) != 9794:
        raise ValueError("platform template must have exactly 9,794 unique GEOIDs")
    rows = []
    for geoid, region in template:
        if region in selected_predictions and geoid in selected_predictions[region]:
            score = selected_predictions[region][geoid]
        elif region == "eastern-wa" and geoid not in official_region:
            score = 0.0
        else:
            raise ValueError(f"template row not assigned to official region or formatting fill: {geoid}")
        rows.append((geoid, score))
    CANDIDATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CANDIDATE_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["GEOID", SCORE_COLUMN])
        writer.writerows((g, f"{v:.12f}") for g, v in rows)
    candidate_sha = sha256_file(CANDIDATE_PATH)

    # Local-only score: leave-one-construction-out RMSE to omitted constructions.
    # It is explicitly not presented as organizer-target RMSE.
    predicted_score = cv_shrunk_rmse
    h1_path = ROOT / "scripts/audit_shrink_diagnosis_h1.py"
    h1_spec = importlib.util.spec_from_file_location("audit_shrink_diagnosis_h1", h1_path)
    if h1_spec is None or h1_spec.loader is None:
        raise RuntimeError("unable to load deterministic H1 report builder")
    h1_module = importlib.util.module_from_spec(h1_spec)
    h1_spec.loader.exec_module(h1_module)
    report = h1_module.build_report(workspace)
    h1_json = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    h1_report_sha256 = hashlib.sha256(h1_json.encode("utf-8")).hexdigest()
    report["status"] = "H1 and H2 measured; exactly one local-proxy shrink candidate frozen; no upload"
    report["H2"] = {
        "verdict": "construction-agreement reliability supports region-mean-centered shrink in the frozen candidate ensemble; not target-accuracy evidence",
        "method": "Cross-candidate covariance across C2, A1, A2 and A3 composite predictions; no LB-calibrated V2/P4 input.",
        "data_use": {
            "official_tracts": 9379,
            "labels_or_sample_score_placeholders_read": False,
            "removed_reference_layers_or_equivalents_read": False,
            "external_data_read": False,
            "leaderboard_or_submission_scores_read": False,
            "target_fitting": False,
            "candidate_uploads": 0,
            "candidate_inputs_are_prediction_artifacts_only": True,
            "platform_template_columns_read": ["GEOID", "region"],
        },
        "formula": config["reliability_definition"],
        "global_construction_signal_shares": full_fit["pooled_signal_share_by_construction"],
        "global_construction_weights": full_fit["global_weights"],
        "region_specificity_rule": config["region_specificity_rule"],
        "reliability_by_region": full_fit["regions"],
        "pooled_reliability": {
            "signal_variance": full_fit["pooled_signal_variance"],
            "total_variance": full_fit["pooled_total_variance"],
            "c": full_fit["pooled_c"],
            "regional_c_range": full_fit["regional_c_range"],
            "region_specific_c_used": full_fit["use_region_specific_c"],
        },
        "local_leave_one_construction_out": {
            "definition": config["local_prediction_protocol"],
            "heldout_folds": fold_records,
            "predicted_score": predicted_score,
            "predicted_score_metric": "tract-weighted LOCO RMSE against held-out candidate construction outputs",
            "unshrunk_consensus_proxy_rmse": cv_unshrunk_rmse,
            "mse_improvement_vs_unshrunk_consensus": cv_improvement,
            "region_average_mse_improvement": regional_avg_gain,
            "worsened_regions": worsened_regions,
            "preregistered_gate_passed": gate_pass,
            "target_RMSE_forecast": None,
            "target_RMSE_not_identifiable": "No local organizer-reference labels exist; the numeric predicted_score is only the specified cross-construction proxy.",
        },
        "candidate_S": {
            "candidate_id": CANDIDATE_ID,
            "artifact": str(CANDIDATE_PATH.relative_to(ROOT)),
            "sha256": candidate_sha,
            "rows": len(rows),
            "official_scored_rows": 9379,
            "formatting_only_eastern_wa_rows": 415,
            "purpose": "improvement",
            "predicted_score": predicted_score,
            "predicted_score_scope": "cross-construction LOCO proxy only; not official RMSE forecast",
            "candidate_predictions_by_region": region_model_summary,
            "shrink_target": "the feature-only ensemble mean within each region; it is retained as an estimated intercept, while only less-reliable within-region deviations are attenuated",
            "upload_authorized": False,
        },
        "limitations": [
            "Cross-method covariance measures shared construction structure only if method errors are uncorrelated; all four constructions share some underlying provided features and may share systematic error.",
            "The four methods are not independent label measurements; their covariance is a proxy reliability estimate, not target accuracy.",
            "Only four constructions are available, so signal-share and LOCO estimates are method-set-sensitive; no spatial-independence claim is made for tract rows.",
            "No local estimate of the hidden target regional mean or actual organizer RMSE is possible without labels.",
        ],
        "implementation": {
            "script": str(Path(__file__).resolve().relative_to(ROOT)),
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "h1_report_sha256_before_h2": h1_report_sha256,
        },
        "preregistration": {
            "path": str(CONFIG_PATH.relative_to(ROOT)),
            "sha256": sha256_file(CONFIG_PATH),
            "registered_before_h2_measurement": True,
            "candidate_method_fixed_before_outcome": True,
        },
        "input_sha256": {m: sha256_file(CANDIDATE_FILES[m]) for m in CONSTRUCTION_IDS},
    }
    report["candidate_registration_pending"] = True
    report["candidate_registration_instruction"] = "Register the single S candidate in reports/candidates.json with the exact artifact hash and local proxy score; do not upload."
    return report, {"candidate_id": CANDIDATE_ID, "artifact": CANDIDATE_PATH, "sha256": candidate_sha,
                    "predicted_score": predicted_score, "local_cv_shrunk_rmse": cv_shrunk_rmse,
                    "local_cv_unshrunk_rmse": cv_unshrunk_rmse, "cv_mse_improvement": cv_improvement,
                    "gate_passed": gate_pass}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report, candidate = build_report_and_candidate(args.workspace)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"candidate": {**candidate, "artifact": str(candidate["artifact"])},
                      "region_c": {r: report["H2"]["reliability_by_region"][r]["c"] for r in REGIONS},
                      "weights": report["H2"]["global_construction_weights"],
                      "local_cv_proxy_rmse": report["H2"]["local_leave_one_construction_out"]["predicted_score"],
                      "unshrunk_proxy_rmse": report["H2"]["local_leave_one_construction_out"]["unshrunk_consensus_proxy_rmse"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
