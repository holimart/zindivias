#!/usr/bin/env python3
"""Integrity-only preflight for the registered S shrink artifact.

Only the S prediction fields are parsed, solely to validate CSV shape and finite
[0,1] values; no prediction values are emitted, aggregated, compared for accuracy,
or used to modify/register/submit a candidate. Official sample files are read by
GEOID only; their sample-score placeholders are not consumed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_ID = "S_crossmethod_reliability_shrink_v1"
CANDIDATE_REL = "reports/candidates/S_crossmethod_reliability_shrink_v1.csv"
RECORDED_SHA = "26be20c569f72f94eb21711a41656eea909a92f3fb4495c1ec3b30f7a26a367a"
PREREG = ROOT / "config/shrink-candidate-preflight-preregistered.json"
REGISTRY = ROOT / "reports/candidates.json"
FROZEN_REPORT = ROOT / "reports/shrink-diagnosis.json"
REGISTRATION_SCRIPT = ROOT / "scripts/register_shrink_diagnosis_candidate.py"
SHRINK_PREREG = ROOT / "config/shrink-diagnosis-preregistered.json"
SUBMISSIONS_LOG = ROOT / "reports/submissions.jsonl"
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_COUNTS = {"maricopa-az": 1593, "northern-ca": 591,
                   "eastern-ok": 1192, "south-central-tx": 6003}
EXPECTED_SAMPLE_SHA = {
    "maricopa-az": "1640b801d56ab308e68c65583b88cfb9e1555b05a6d0b922eaba22ac09960a31",
    "northern-ca": "f38017b5284667b80b451685fd146388c108e1b30308301289c7a96ecfe60902",
    "eastern-ok": "56cb40fba4008d646bc63c62c91e211871459224b3de8b3a886ff813803fd0d4",
    "south-central-tx": "afa59c0eb30c40be25ce82098f144447591b79cb9692e669b2f2fe7b7b1ee58e",
}
REPORT = ROOT / "reports/shrink-candidate-preflight.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_ids(candidate_ids: list[str], official_by_region: dict[str, list[str]]) -> dict[str, Any]:
    official_owner = {geoid: region for region, ids in official_by_region.items() for geoid in ids}
    all_official = set(official_owner)
    counts = Counter(candidate_ids)
    unique_candidate = set(candidate_ids)
    region_results = {}
    for region in REGIONS:
        expected = official_by_region[region]
        in_scope = [geoid for geoid in candidate_ids if geoid in official_owner and official_owner[geoid] == region]
        found = set(in_scope)
        missing = set(expected) - found
        region_results[region] = {
            "official_expected_rows": len(expected),
            "candidate_official_rows": len(in_scope),
            "unique_candidate_official_rows": len(found),
            "missing_official_ids": len(missing),
            "extra_official_region_ids": len(found - set(expected)),
            "duplicate_official_ids": sum(n - 1 for g, n in counts.items() if g in set(expected) and n > 1),
            "ordered_ids_match_official_sample": in_scope == expected,
        }
    extra_ids = unique_candidate - all_official
    missing_all = all_official - unique_candidate
    duplicate_ids = sum(n - 1 for n in counts.values() if n > 1)
    official_subset_pass = (
        not missing_all
        and not duplicate_ids
        and all(r["missing_official_ids"] == 0 and r["extra_official_region_ids"] == 0
                and r["duplicate_official_ids"] == 0 and r["ordered_ids_match_official_sample"]
                for r in region_results.values())
    )
    strict_artifact_pass = official_subset_pass and not extra_ids and len(candidate_ids) == len(all_official)
    return {
        "official_scored_rows": len(all_official),
        "candidate_artifact_rows": len(candidate_ids),
        "candidate_unique_ids": len(unique_candidate),
        "candidate_duplicate_id_excess": duplicate_ids,
        "official_ids_missing": len(missing_all),
        "candidate_ids_outside_official_scope": len(extra_ids),
        "official_scored_subset_exact_ordered": official_subset_pass,
        "whole_artifact_exactly_official_scope": strict_artifact_pass,
        "per_region": region_results,
    }


def validate_prediction_fields(header: list[str], row: list[str]) -> dict[str, int]:
    if len(row) != len(header):
        return {"malformed_rows": 1, "missing_values": 0, "nonfinite_values": 0,
                "out_of_range_values": 0, "numeric_values_checked": 0}
    geoid_idx = header.index("GEOID")
    stats = {"malformed_rows": 0, "missing_values": 0, "nonfinite_values": 0,
             "out_of_range_values": 0, "numeric_values_checked": 0}
    for i, text in enumerate(row):
        if i == geoid_idx:
            continue
        if text is None or text.strip() == "":
            stats["missing_values"] += 1
            continue
        try:
            value = float(text)
        except (TypeError, ValueError):
            stats["nonfinite_values"] += 1
            continue
        stats["numeric_values_checked"] += 1
        if not math.isfinite(value):
            stats["nonfinite_values"] += 1
        elif value < 0.0 or value > 1.0:
            stats["out_of_range_values"] += 1
    return stats


def read_official_samples(workspace: Path) -> tuple[dict[str, list[str]], dict[str, Any]]:
    official: dict[str, list[str]] = {}
    info = {}
    for region in REGIONS:
        path = workspace / "samples" / f"{region}-sample-submission.csv"
        digest = sha256_file(path)
        if digest != EXPECTED_SAMPLE_SHA[region]:
            raise ValueError(f"official sample hash mismatch for {region}")
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream)
            header = next(reader, None)
            if header != ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]:
                raise ValueError(f"official sample header mismatch for {region}")
            ids = []
            for row in reader:
                if len(row) != len(header):
                    raise ValueError(f"official sample row shape mismatch for {region}")
                # Access and retain only GEOID; never inspect placeholder cells.
                ids.append(row[0])
        if len(ids) != EXPECTED_COUNTS[region] or len(ids) != len(set(ids)):
            raise ValueError(f"official sample count/uniqueness mismatch for {region}")
        official[region] = ids
        info[region] = {
            "path": str(path), "sha256": digest, "rows": len(ids),
            "header_matches_contract": True, "GEOID_only_consumed": True,
            "score_placeholder_values_consumed": False,
        }
    return official, info


def find_registry_entry(obj: Any) -> list[dict[str, Any]]:
    found = []
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("candidate_id") == CANDIDATE_ID:
                found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(obj)
    return found


def no_submission_reference() -> bool:
    if not SUBMISSIONS_LOG.exists():
        return True
    tokens = (CANDIDATE_ID, CANDIDATE_REL, RECORDED_SHA)
    with SUBMISSIONS_LOG.open("r", encoding="utf-8") as stream:
        for line in stream:
            if any(token in line for token in tokens):
                return False
    return True


def run_preflight(workspace: Path, out: Path = REPORT) -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    candidate_path = ROOT / CANDIDATE_REL
    hashes = {
        "artifact_before": sha256_file(candidate_path),
        "candidate_registry": sha256_file(REGISTRY),
        "frozen_shrink_report": sha256_file(FROZEN_REPORT),
        "shrink_preregistration": sha256_file(SHRINK_PREREG),
        "registration_script": sha256_file(REGISTRATION_SCRIPT),
        "preflight_preregistration": sha256_file(PREREG),
    }
    expected = prereg["frozen_provenance"]
    expected_checks = {
        "candidate_registry": expected["candidate_registry"]["sha256"],
        "frozen_shrink_report": expected["shrink_diagnosis_manifest"]["sha256"],
        "shrink_preregistration": expected["shrink_diagnosis_preregistration"]["sha256"],
        "registration_script": expected["registration_script"]["sha256"],
    }
    provenance_hashes_match = {name: hashes[name] == value for name, value in expected_checks.items()}

    registry_json = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry_matches = find_registry_entry(registry_json)
    if len(registry_matches) != 1:
        raise ValueError(f"expected one registry entry for {CANDIDATE_ID}, found {len(registry_matches)}")
    reg = registry_matches[0]
    frozen = json.loads(FROZEN_REPORT.read_text(encoding="utf-8"))
    frozen_candidate = frozen["H2"]["candidate_S"]
    registration = frozen["candidate_registration"]
    artifact_sha_match = (
        hashes["artifact_before"] == RECORDED_SHA
        and reg.get("sha256") == RECORDED_SHA
        and frozen_candidate.get("sha256") == RECORDED_SHA
        and registration.get("card_sha256") == RECORDED_SHA
    )
    identity_match = (
        reg.get("candidate_id") == CANDIDATE_ID
        and reg.get("artifact") == CANDIDATE_REL
        and frozen_candidate.get("candidate_id") == CANDIDATE_ID
        and frozen_candidate.get("artifact") == CANDIDATE_REL
        and registration.get("candidate_id") == CANDIDATE_ID
        and registration.get("registry_path") == "reports/candidates.json"
        and registration.get("registry_sha256") == hashes["candidate_registry"]
        and registration.get("status") == "registered"
    )
    authorization_false = (
        reg.get("upload_authorized") is False
        and frozen_candidate.get("upload_authorized") is False
        and registration.get("upload_authorized") is False
    )
    frozen_counts = {
        "official_scored_rows": frozen_candidate.get("official_scored_rows"),
        "formatting_only_eastern_wa_rows": frozen_candidate.get("formatting_only_eastern_wa_rows"),
        "total_rows": frozen_candidate.get("rows"),
    }
    if not provenance_hashes_match["candidate_registry"]:
        raise ValueError("candidate registry differs from the frozen registration manifest")
    if not provenance_hashes_match["frozen_shrink_report"]:
        raise ValueError("frozen shrink manifest hash differs from preregistration")
    if not provenance_hashes_match["shrink_preregistration"] or not provenance_hashes_match["registration_script"]:
        raise ValueError("frozen shrink provenance hash mismatch")

    official_by_region, official_file_info = read_official_samples(workspace)
    official_owner = {g: r for r, ids in official_by_region.items() for g in ids}
    candidate_ids = []
    candidate_value_stats = Counter()
    with candidate_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if not header or len(header) != len(set(header)) or "GEOID" not in header or "coverage_gap_score" not in header:
            raise ValueError("S artifact header lacks unique GEOID and coverage_gap_score columns")
        if not set(header).issubset({"GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"}):
            raise ValueError("S artifact includes an unexpected non-contract column")
        for row in reader:
            if len(row) != len(header):
                candidate_value_stats["malformed_rows"] += 1
                continue
            geoid = row[header.index("GEOID")]
            candidate_ids.append(geoid)
            for key, value in validate_prediction_fields(header, row).items():
                candidate_value_stats[key] += value
    id_result = check_ids(candidate_ids, official_by_region)
    no_submission = no_submission_reference()
    hashes["artifact_after"] = sha256_file(candidate_path)
    unchanged = hashes["artifact_before"] == hashes["artifact_after"]
    frozen_counts_match = (
        frozen_counts["official_scored_rows"] == 9379
        and frozen_counts["formatting_only_eastern_wa_rows"] == 415
        and frozen_counts["total_rows"] == len(candidate_ids)
    )
    value_contract_pass = (
        candidate_value_stats["malformed_rows"] == 0
        and candidate_value_stats["missing_values"] == 0
        and candidate_value_stats["nonfinite_values"] == 0
        and candidate_value_stats["out_of_range_values"] == 0
        and candidate_value_stats["numeric_values_checked"] == len(candidate_ids) * (len(header) - 1)
    )
    no_upload = authorization_false and no_submission
    strict_acceptance = (
        artifact_sha_match and identity_match and all(provenance_hashes_match.values())
        and frozen_counts_match and id_result["whole_artifact_exactly_official_scope"]
        and value_contract_pass and no_upload and unchanged
    )
    # The registered frozen artifact explicitly carries 415 format-only template IDs.
    # They are reported, never silently filtered or written back to the candidate.
    expected_format_fills = (
        frozen_counts_match
        and id_result["candidate_ids_outside_official_scope"] == 415
        and id_result["candidate_artifact_rows"] == 9794
    )
    report = {
        "schema_version": 1,
        "audit": "integrity and submission-contract preflight for the sole registered S shrink candidate",
        "candidate": {
            "candidate_id": CANDIDATE_ID,
            "artifact": CANDIDATE_REL,
            "artifact_sha256": hashes["artifact_before"],
            "recorded_artifact_sha256": RECORDED_SHA,
            "artifact_hash_matches_record_registry_and_frozen_candidate_record": artifact_sha_match,
            "header": header,
            "candidate_prediction_fields_checked": candidate_value_stats["numeric_values_checked"],
            "malformed_rows": candidate_value_stats["malformed_rows"],
            "missing_values": candidate_value_stats["missing_values"],
            "nonfinite_or_non_numeric_values": candidate_value_stats["nonfinite_values"],
            "out_of_range_values": candidate_value_stats["out_of_range_values"],
            "prediction_values_emitted": False,
            "artifact_unchanged_after_preflight": unchanged,
            "frozen_candidate_record_counts": frozen_counts,
        },
        "official_scored_scope": {
            "expected_rows": 9379,
            "official_scored_subset_exact_ordered": id_result["official_scored_subset_exact_ordered"],
            "missing_official_ids": id_result["official_ids_missing"],
            "duplicate_candidate_ids": id_result["candidate_duplicate_id_excess"],
            "per_region": id_result["per_region"],
            "official_reference_sample_files": official_file_info,
            "sample_values_read": False,
        },
        "whole_artifact_scope": {
            "candidate_artifact_rows": id_result["candidate_artifact_rows"],
            "unique_candidate_ids": id_result["candidate_unique_ids"],
            "candidate_ids_outside_official_9379": id_result["candidate_ids_outside_official_scope"],
            "whole_artifact_exactly_9379_official_ids": id_result["whole_artifact_exactly_official_scope"],
            "extra_rows_match_frozen_format_only_metadata": expected_format_fills,
            "interpretation": "The frozen S file is a 9,794-row platform-template artifact: 9,379 official scored IDs plus 415 manifest-declared Eastern-WA formatting-only rows. These extras are excluded from the official scored-scope comparison but remain in the frozen artifact; they were not removed or changed.",
        },
        "provenance": {
            "preregistration_path": str(PREREG.relative_to(ROOT)),
            "preregistration_sha256": hashes["preflight_preregistration"],
            "hashes": hashes,
            "frozen_provenance_hash_matches": provenance_hashes_match,
            "registry_candidate_metadata": {
                "candidate_id": reg.get("candidate_id"), "artifact": reg.get("artifact"),
                "sha256": reg.get("sha256"), "purpose": reg.get("purpose"),
                "upload_authorized": reg.get("upload_authorized"),
            },
            "frozen_candidate_registration": {
                "candidate_id": registration.get("candidate_id"),
                "status": registration.get("status"),
                "upload_authorized": registration.get("upload_authorized"),
                "candidate_card_sha256": registration.get("card_sha256"),
                "registry_path": registration.get("registry_path"),
                "registry_sha256": registration.get("registry_sha256"),
            },
            "frozen_candidate_identity_match": identity_match,
        },
        "authorization_and_side_effects": {
            "registry_upload_authorized": reg.get("upload_authorized"),
            "frozen_candidate_upload_authorized": frozen_candidate.get("upload_authorized"),
            "candidate_registration_upload_authorized": registration.get("upload_authorized"),
            "authorization_all_false": authorization_false,
            "candidate_id_or_artifact_or_sha_found_in_submission_log": not no_submission,
            "upload_performed": False,
            "submission_side_effect_found": False if no_submission else True,
        },
        "result": {
            "official_scored_subset_contract_pass": id_result["official_scored_subset_exact_ordered"] and value_contract_pass,
            "strict_whole_artifact_9379_row_acceptance_met": strict_acceptance,
            "status": "negative_strict_scope_mismatch" if not strict_acceptance else "passed",
            "strict_scope_failure_reason": None if strict_acceptance else (
                "The frozen registered artifact includes 415 explicit formatting-only IDs outside the 9,379 official scored IDs. The row's literal whole-artifact acceptance requires zero extras. Candidate modification is forbidden; therefore the extras were not removed and no upload is authorized."
                if id_result["candidate_ids_outside_official_scope"] else "One or more integrity/provenance/value/authorization checks failed; see fields above."
            ),
            "frozen_format_only_rows_present": expected_format_fills,
            "upload_authorized": False,
            "upload_performed": False,
            "candidate_modified": False,
            "registration_modified": False,
            "final_two_protocol_changed": False,
        },
        "data_use": {
            "official_sample_GEOID_only": True,
            "sample_score_placeholders_consumed": False,
            "candidate_prediction_values_used_only_for_numeric_contract_validation": True,
            "candidate_prediction_values_reported_or_used_for_accuracy": False,
            "labels_or_target_values_read": False,
            "leaderboard_or_scorecard_information_used": False,
            "removed_reference_layers_or_equivalents_read": False,
            "external_data_read": False,
            "candidate_or_registry_modified": False,
            "uploads": 0,
        },
        "reproduction": {
            "command": "uv run --with-requirements requirements.txt python scripts/verify_shrink_candidate_preflight.py --workspace $COMPETITION_WORKSPACE --out reports/shrink-candidate-preflight.json",
            "tests": "uv run --with-requirements requirements.txt --with pytest python -m pytest -q tests/test_verify_shrink_candidate_preflight.py",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--out", type=Path, default=REPORT)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    result = run_preflight(args.workspace, args.out)
    print(json.dumps({
        "candidate_id": result["candidate"]["candidate_id"],
        "artifact_sha256": result["candidate"]["artifact_sha256"],
        "official_scored_subset_exact_ordered": result["official_scored_scope"]["official_scored_subset_exact_ordered"],
        "candidate_artifact_rows": result["whole_artifact_scope"]["candidate_artifact_rows"],
        "format_only_extras": result["whole_artifact_scope"]["candidate_ids_outside_official_9379"],
        "strict_whole_artifact_9379_row_acceptance_met": result["result"]["strict_whole_artifact_9379_row_acceptance_met"],
        "upload_authorized": False,
        "upload_performed": False,
        "report": str(args.out),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
