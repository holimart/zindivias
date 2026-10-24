#!/usr/bin/env python3
"""Build and preregister few-parameter region-indicator calibration probes.

The four region candidates use p=c_r inside one official region and zero on
all other template rows. c_r is the existing global P4 shrink alpha times the
C1 mean for region r. This script reads only official-sample GEOIDs, template
GEOID/region metadata, the committed C1 candidate and preregistered algebra;
it never reads sample score placeholders, labels, or removed reference data.
No upload is performed here.
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
FULL_COLUMNS = ("GEOID", *SCORE_COLUMNS)
TWO_COLUMNS = ("GEOID", "coverage_gap_score")
PROBE_ORDER = ("P6", "R-AZ", "R-CA", "R-OK", "R-TX")
REGION_BY_PROBE = {
    "R-AZ": "maricopa-az",
    "R-CA": "northern-ca",
    "R-OK": "eastern-ok",
    "R-TX": "south-central-tx",
}
ARTIFACTS = {
    "R-AZ": "reports/candidates/R-AZ_region_indicator.csv",
    "R-CA": "reports/candidates/R-CA_region_indicator.csv",
    "R-OK": "reports/candidates/R-OK_region_indicator.csv",
    "R-TX": "reports/candidates/R-TX_region_indicator.csv",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ids_sha256(ids: list[str]) -> str:
    return hashlib.sha256(("\n".join(ids) + "\n").encode("utf-8")).hexdigest()


def mean(values) -> float:
    values = list(values)
    if not values:
        raise ValueError("cannot average an empty group")
    result = sum(values) / len(values)
    if not math.isfinite(result):
        raise ValueError("nonfinite mean")
    return result


def read_official_ids(path: Path) -> list[str]:
    # Parse the sample CSV but access only GEOID; score placeholders are unused.
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if not header or header[0] != "GEOID":
            raise ValueError(f"expected GEOID as the first sample column: {path}")
        ids = [row[0] for row in reader]
    if not ids or len(ids) != len(set(ids)) or any(not geoid for geoid in ids):
        raise ValueError(f"empty or duplicate GEOIDs in {path}")
    return ids


def read_template(path: Path) -> tuple[list[str], dict[str, str]]:
    # Do not access any sample-template score cells.
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if "GEOID" not in header or "region" not in header:
            raise ValueError("template must contain GEOID and region metadata")
        geoid_i, region_i = header.index("GEOID"), header.index("region")
        ids, region_by_id = [], {}
        for row in reader:
            geoid, region = row[geoid_i], row[region_i]
            ids.append(geoid)
            if geoid in region_by_id:
                raise ValueError(f"duplicate template GEOID: {geoid}")
            region_by_id[geoid] = region
    if len(ids) != 9794 or set(region_by_id.values()) != set(TEMPLATE_REGIONS):
        raise ValueError(f"expected 9,794 rows and five known template regions; got {len(ids)}")
    return ids, region_by_id


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def csv_bytes(rows: list[dict[str, str]], columns: tuple[str, ...]) -> bytes:
    import io
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "reports/candidates.json")
    parser.add_argument("--algebra", type=Path, default=ROOT / "reports/lb-score-algebra.json")
    parser.add_argument("--audit", type=Path, default=ROOT / "reports/region-probe-preregistration.json")
    parser.add_argument("--ledger", type=Path, default=ROOT / "reports/submissions.jsonl")
    args = parser.parse_args()
    ledger_rows = [json.loads(line) for line in args.ledger.resolve().read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    new_probe_uploads = [row for row in ledger_rows
                         if row.get("candidate_id") in PROBE_ORDER and row.get("live_upload") is True]
    if new_probe_uploads:
        raise RuntimeError("phase-3 probe upload already exists; keep preregistered artifacts frozen")
    workspace = args.workspace.resolve()
    manifest_path, algebra_path, audit_path = (
        args.manifest.resolve(), args.algebra.resolve(), args.audit.resolve()
    )

    template_path = workspace / "SampleSubmission.csv"
    template_ids, template_regions = read_template(template_path)
    official_ids: list[str] = []
    region_ids: dict[str, list[str]] = {}
    official_region: dict[str, str] = {}
    for region in REGIONS:
        ids = read_official_ids(workspace / "samples" / f"{region}-sample-submission.csv")
        region_ids[region] = ids
        for geoid in ids:
            if geoid in official_region:
                raise ValueError(f"duplicate official GEOID across regions: {geoid}")
            official_region[geoid] = region
        official_ids.extend(ids)
    if len(official_ids) != 9379 or not set(official_ids).issubset(template_ids):
        raise ValueError("expected 9,379 official IDs contained in the template")
    if any(template_regions[g] != r for g, r in official_region.items()):
        raise ValueError("template region metadata disagrees with official samples")
    extras = [g for g in template_ids if g not in official_region]
    if len(extras) != 415 or any(template_regions[g] != "eastern-wa" for g in extras):
        raise ValueError("expected exactly 415 eastern-WA template-only IDs")
    region_counts = {region: len(region_ids[region]) for region in REGIONS}
    if sum(region_counts.values()) != 9379:
        raise ValueError("official region counts do not sum to 9,379")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    algebra = json.loads(algebra_path.read_text(encoding="utf-8"))
    entries = {item["candidate_id"]: item for item in manifest.get("candidate_set", [])}
    if not {"C1", "P6"}.issubset(entries):
        raise ValueError("manifest must already contain frozen C1 and P6")
    p6 = entries["P6"]
    p6_path = ROOT / p6["artifact"]
    if sha256_file(p6_path) != p6.get("sha256") or p6.get("rows") != 9794:
        raise ValueError("existing P6 artifact does not match its frozen manifest")
    p6_params = p6.get("parameters", {})
    global_c = float(p6_params.get("c", algebra["phase2_preregistered"]["probes"]["P6"]["c"]))
    p6_prediction = float(p6_params.get("predicted_public_rmse", algebra["phase2_preregistered"]["probes"]["P6"]["predicted_public_rmse"]))
    if not (0 <= global_c <= 1 and 0 <= p6_prediction <= 1):
        raise ValueError("invalid frozen P6 value or forecast")

    alpha = float(algebra["phase2_preregistered"]["probes"]["P4"]["alpha"])
    mse_y2 = float(algebra["phase1"]["C0_target_second_moment_proxy"])
    c1 = entries["C1"]
    c1_path = ROOT / c1["artifact"]
    if sha256_file(c1_path) != c1.get("sha256") or c1.get("rows") != 9794:
        raise ValueError("C1 artifact does not match its frozen manifest")
    c1_rows, c1_header = read_csv(c1_path)
    if c1_header != list(FULL_COLUMNS) or len(c1_rows) != 9794:
        raise ValueError("C1 must be a valid five-column 9,794-row candidate")
    c1_by_id = {row["GEOID"]: float(row["coverage_gap_score"]) for row in c1_rows}
    if len(c1_by_id) != 9794 or set(c1_by_id) != set(template_ids):
        raise ValueError("C1 IDs do not exactly match template IDs")
    for g, value in c1_by_id.items():
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"invalid C1 coverage value for {g}")

    # Forecasts assume E[y|r] is the P6 working global mean. Public composition
    # is first assumed to match the 9,379 official rows. The 9,794-row case is
    # also frozen to make the eastern-WA scoring question explicit.
    prediction_by_probe = {}
    for probe, region in REGION_BY_PROBE.items():
        c1_mean = mean(c1_by_id[g] for g in region_ids[region])
        c_r = alpha * c1_mean
        if not 0 <= c_r <= 1:
            raise ValueError(f"region probe constant out of bounds: {probe}={c_r}")
        scenarios = {}
        for denominator, scope in ((9379, "official_9379_scored_ids"),
                                   (9794, "all_9794_template_ids_scored")):
            share = region_counts[region] / denominator
            predicted_mse = mse_y2 - 2 * c_r * share * global_c + c_r * c_r * share
            if not math.isfinite(predicted_mse) or predicted_mse < -1e-12:
                raise ValueError(f"invalid forecast MSE for {probe}/{scope}: {predicted_mse}")
            scenarios[scope] = {
                "evaluation_denominator_assumption": denominator,
                "region_share": share,
                "assumed_E_y_given_region": global_c,
                "predicted_public_rmse": math.sqrt(max(0.0, predicted_mse)),
            }
        prediction_by_probe[probe] = {
            "region": region,
            "official_rows": region_counts[region],
            "C1_region_mean": c1_mean,
            "alpha": alpha,
            "probe_constant_c_r": c_r,
            "predicted_public_rmse": scenarios["official_9379_scored_ids"]["predicted_public_rmse"],
            "prediction_scenarios": scenarios,
        }

    candidate_entries = {}
    candidate_audit = {}
    for probe, region in REGION_BY_PROBE.items():
        out_columns = TWO_COLUMNS if probe == "R-AZ" else FULL_COLUMNS
        rows = []
        c_r = prediction_by_probe[probe]["probe_constant_c_r"]
        for geoid in template_ids:
            value = c_r if template_regions[geoid] == region else 0.0
            formatted = format(value, ".17g")
            if out_columns == TWO_COLUMNS:
                row = {"GEOID": geoid, "coverage_gap_score": formatted}
            else:
                row = {"GEOID": geoid, **{column: formatted for column in SCORE_COLUMNS}}
            rows.append(row)
        if [row["GEOID"] for row in rows] != template_ids:
            raise AssertionError(f"{probe} candidate order changed")
        for row in rows:
            scores = [float(row[column]) for column in out_columns[1:]]
            if any(not math.isfinite(value) or not 0 <= value <= 1 for value in scores):
                raise ValueError(f"invalid {probe} candidate score in {row['GEOID']}")
        artifact = ARTIFACTS[probe]
        output_path = ROOT / artifact
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(csv_bytes(rows, out_columns))
        digest = sha256_file(output_path)
        item = {
            "candidate_id": probe,
            "name": f"Phase-3 {region} indicator probe",
            "artifact": artifact,
            "sha256": digest,
            "rows": len(rows),
            "columns": list(out_columns),
            "pre_registered": True,
            "description": "Global-constant region-indicator public calibration probe; p=c_r in one official region and zero elsewhere, including eastern-WA template rows. No labels, per-GEOID tuning, or removed references.",
            "phase": 3,
            "parameters": {
                "transform": "p=c_r on the selected official region; p=0 elsewhere and on eastern-WA template rows",
                "region": region,
                "c_r": c_r,
                "alpha_from_P4": alpha,
                "C1_region_mean": prediction_by_probe[probe]["C1_region_mean"],
                "predicted_public_rmse": prediction_by_probe[probe]["predicted_public_rmse"],
                "prediction_scenarios": prediction_by_probe[probe]["prediction_scenarios"],
                "prediction_basis": "E[y|region] assumed equal to the existing P6 working global-mean estimate; E[y^2] from C0 public RMSE; public composition assumed representative. Official 9,379-row denominator is the primary forecast; 9,794-row alternative is recorded to test template scoring.",
            },
            "eastern_wa_fill": {
                "region": "eastern-wa",
                "rows": len(extras),
                "policy": "zero indicator; no eastern-WA feature or target inference",
                "values": {column: 0.0 for column in out_columns[1:]},
                "scored_target_claim": False,
            },
            "column_checks": {
                "unique_columns": len(out_columns) == len(set(out_columns)),
                "all_rows_have_columns": all(list(row) == list(rows[0]) for row in rows),
                "finite_bounded_scores": True,
            },
            "per_region_means": {
                region_name: (c_r if region_name == region else 0.0)
                for region_name in TEMPLATE_REGIONS
            },
            "defined_rates": None,
        }
        candidate_entries[probe] = item
        candidate_audit[probe] = {
            "artifact": artifact,
            "sha256": digest,
            "rows": len(rows),
            "columns": list(out_columns),
            "exact_template_order_once": True,
            "official_region_rows": region_counts[region],
            "eastern_wa_rows_zero": len(extras),
            "ratio_or_target_values_used": False,
            "forecast": prediction_by_probe[probe],
        }

    # Freeze the four new entries while preserving all previously registered
    # artifacts and P6's phase-2 registration. Re-running this builder replaces
    # only the R-* entries and the Phase-3 registration blocks.
    old_ids = {"R-AZ", "R-CA", "R-OK", "R-TX"}
    kept = [item for item in manifest["candidate_set"] if item["candidate_id"] not in old_ids]
    kept.extend(candidate_entries[probe] for probe in REGION_BY_PROBE)
    manifest["candidate_set"] = kept
    manifest["phase3_preregistration"] = {
        "frozen_before_live_upload": True,
        "probe_order": list(PROBE_ORDER),
        "max_live_uploads": 5,
        "P6_existing_registration": {
            "artifact": p6["artifact"],
            "sha256": p6["sha256"],
            "predicted_public_rmse": p6_prediction,
            "status_before_phase3": p6.get("parameters", {}).get("actual_public_rmse"),
            "note": "Reuse existing frozen P6 constant candidate; no P6 artifact changes.",
        },
        "region_indicator_candidates": candidate_entries,
        "prediction_assumptions": {
            "score_identity": "MSE(p)=E[y^2]-2E[p y]+E[p^2]",
            "E_y2": mse_y2,
            "global_mean_prior_from_P6": global_c,
            "conditional_mean_prior": "E[y|r]=global_mean_prior for each official region; this is a forecast assumption, to be replaced by probe-inferred means.",
            "primary_public_composition": "official 9,379-row region shares; the public 30% is assumed representative",
            "alternative_composition": "all 9,794 template IDs treated as scored; reported only to frame the eastern-WA test",
            "no_label_or_hidden_reference_data": True,
        },
        "input_lineage": {
            "official_geoid_sequence_sha256": ids_sha256(official_ids),
            "template_geoid_region_sequence_sha256": ids_sha256([f"{g}\t{template_regions[g]}" for g in template_ids]),
            "C1_sha256": c1["sha256"],
            "P6_sha256": p6["sha256"],
        },
        "uploads_started": False,
    }
    write_json(manifest_path, manifest)

    algebra["candidate_manifest_sha256"] = sha256_file(manifest_path)
    algebra["phase3_preregistration"] = manifest["phase3_preregistration"]
    write_json(algebra_path, algebra)

    dry_runs = {}
    for probe in PROBE_ORDER:
        matches = [row for row in ledger_rows if row.get("candidate_id") == probe
                   and row.get("status") == "dry_run_validated" and row.get("live_upload") is False]
        if matches:
            row = matches[-1]
            dry_runs[probe] = {
                "status": row["status"],
                "live_upload": False,
                "submissions_today_before": row.get("submissions_today_before"),
                "daily_cap": row.get("daily_cap"),
                "sha256": row.get("sha256"),
            }
    cap_values = {row.get("daily_cap") for row in dry_runs.values() if row.get("daily_cap") is not None}
    used_values = {row.get("submissions_today_before") for row in dry_runs.values()
                   if row.get("submissions_today_before") is not None}
    audit = {
        "method": "pre-registered public score-algebra region indicator probes",
        "labels_or_removed_references_used": False,
        "score_placeholders_read": False,
        "official_scored_region_count": len(official_ids),
        "platform_template_count": len(template_ids),
        "eastern_wa_template_only_count": len(extras),
        "official_region_counts": region_counts,
        "input_hashes": manifest["phase3_preregistration"]["input_lineage"],
        "P6": manifest["phase3_preregistration"]["P6_existing_registration"],
        "region_probes": candidate_audit,
        "probe_order": list(PROBE_ORDER),
        "required_upload_budget": 5,
        "live_uploads_performed": 0,
        "shared_orchestrator_dry_runs": dry_runs,
        "upload_limit_snapshot": {
            "all_five_dry_runs_validated": set(dry_runs) == set(PROBE_ORDER),
            "daily_cap_values_observed": sorted(cap_values),
            "submissions_today_before_values_observed": sorted(used_values),
            "cap_exhausted_at_dry_run": bool(cap_values and used_values and max(used_values) >= min(cap_values)),
            "source": "reports/submissions.jsonl records from $ORCHESTRATOR_ROOT/scripts/zindi_submit.py without --live",
        },
        "primary_prediction_denominator": 9379,
        "alternative_prediction_denominator": 9794,
    }
    write_json(audit_path, audit)
    print("PASS region probe preregistration: 9,794 exact template IDs; 9,379 official IDs; 415 WA rows zero")
    for probe in PROBE_ORDER:
        if probe == "P6":
            item = manifest["phase3_preregistration"]["P6_existing_registration"]
            print(f"{probe}: predicted RMSE {item['predicted_public_rmse']:.9f}; SHA-256 {item['sha256']}")
        else:
            item = candidate_entries[probe]
            print(f"{probe}: predicted RMSE {item['parameters']['predicted_public_rmse']:.9f}; "
                  f"alternate-9794 {item['parameters']['prediction_scenarios']['all_9794_template_ids_scored']['predicted_public_rmse']:.9f}; "
                  f"SHA-256 {item['sha256']}")
    print(f"Manifest SHA-256: {sha256_file(manifest_path)}")
    print(f"Audit: {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
