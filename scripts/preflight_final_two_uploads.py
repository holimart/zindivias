#!/usr/bin/env python3
"""Preflight the frozen A3/V2 calibration packages without uploading.

The guarded uploader's no-``--live`` dry runs should be run separately before
this script; those records are checked here. This report builder is local-only
and never reads scores or changes candidate packages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = Path("/home/martin/hddProjects/competitionsall")
COMPETITION = "bias-bounty-mapping-equity-challenge"
SCHEDULED_DATE = "2026-10-27"
CANDIDATES = ("A3_dawid_skene", "V2_model")
CARD_PATH = "reports/final-candidate-cards/track-a-cohort-2026-10-26.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def make_report(as_of: str) -> dict[str, Any]:
    manifest_path = ROOT / "reports/candidates.json"
    final_path = ROOT / "reports/final-two.json"
    card_path = ROOT / CARD_PATH
    ledger_path = ROOT / "reports/lb-calibration.jsonl"
    submissions_path = ROOT / "reports/submissions.jsonl"
    candidates = {row["candidate_id"]: row for row in load_json(manifest_path)["candidate_set"]}
    registrations = {row["candidate_id"]: row for row in load_json(final_path)["candidate_registrations"]}
    card_doc = load_json(card_path)
    cards = {row["candidate_id"]: row for row in card_doc["cards"]}
    final = load_json(final_path)
    calibration_rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    submission_rows = [json.loads(line) for line in submissions_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    card_hash = digest(card_path)
    card_registration = "reports/final-candidate-cards/track-a-cohort-2026-10-26.json"
    schedule = [row for row in final["upload_budget"]["queued_uploads"] if row.get("scheduled_utc") == SCHEDULED_DATE]
    budget = next(row for row in final["upload_budget"]["dates_with_upload_budget"] if row["date_utc"] == SCHEDULED_DATE)
    daily_cap = int(final["upload_budget"]["effective_lane_ceiling_per_day"])
    scheduled_ids = [row["candidate_id"] for row in schedule]
    today_uploads = [row for row in final["upload_budget"]["completed_uploads"] if str(row.get("submitted_utc", "")).startswith(SCHEDULED_DATE)]
    checks: list[dict[str, Any]] = []
    candidates_out: dict[str, Any] = {}

    for candidate_id in CANDIDATES:
        candidate = candidates[candidate_id]
        registration = registrations[candidate_id]
        card = cards[candidate_id]
        artifact_path = ROOT / candidate["artifact"]
        artifact_hash = digest(artifact_path)
        manifest_path_for_candidate = ROOT / card["build_manifest"] if card.get("build_manifest") and "#" not in card["build_manifest"] else None
        build_manifest_hash = digest(manifest_path_for_candidate) if manifest_path_for_candidate else None
        if candidate_id == "A3_dawid_skene":
            require(build_manifest_hash == card.get("build_manifest_sha256"), "A3 frozen build manifest hash mismatch")

        daily_guard_runs = [
            row for row in submission_rows
            if row.get("candidate_id") == candidate_id
            and row.get("status") == "dry_run_validated"
            and row.get("live_upload") is False
            and str(row.get("ts", "")).startswith(as_of)
        ]
        dry_run = daily_guard_runs[-1] if daily_guard_runs else None
        command = (
            f'python3 -I "{ORCHESTRATOR}/scripts/zindi_submit.py" '
            f"--competition {COMPETITION} --repo \"$COMPETITION_REPO\" --candidate {candidate_id}"
        )

        # Four frozen checks per candidate (eight in total).
        artifact_ok = (
            artifact_hash == candidate.get("sha256")
            == registration.get("sha256") == card.get("sha256")
            and artifact_path.is_file()
            and candidate.get("rows") == registration.get("rows") == card.get("rows") == 9794
        )
        checks.append({"id": f"{candidate_id}:frozen_artifact_hash", "passed": artifact_ok,
                       "artifact": candidate["artifact"], "sha256": artifact_hash,
                       "rows": candidate.get("rows")})

        authorization_ok = (
            candidate.get("pre_registered") is True
            and candidate.get("registration_status") == "frozen_authorized_pending_upload"
            and candidate.get("upload_authorized") is True
            and registration.get("upload_authorized") is True
            and card.get("upload_authorized") is True
            and card_hash == candidate.get("preregistered_card_sha256")
            == registration.get("preregistered_card_sha256")
            and candidate.get("preregistered_card_path") == card_registration
            and registration.get("preregistered_card_path") == card_registration
            and card.get("scheduled_upload_utc") == SCHEDULED_DATE
        )
        checks.append({"id": f"{candidate_id}:authorized_registered_card", "passed": authorization_ok,
                       "card": card_registration, "card_sha256": card_hash,
                       "preregistration_commit": registration.get("preregistration_commit")})

        forecast_status = card.get("predicted_public_rmse_status", candidate.get("forecast_status", ""))
        no_prior_mislabel = (
            candidate.get("purpose") == "calibration"
            and registration.get("purpose") == "calibration"
            and card.get("purpose") == "calibration"
            and candidate.get("local_measured_holdout_rmse") is None
            and "not a measured local held-out score" in forecast_status
            and candidate_id not in {row.get("candidate_id") for row in calibration_rows}
        )
        checks.append({"id": f"{candidate_id}:calibration_purpose_and_prior_label", "passed": no_prior_mislabel,
                       "purpose": "calibration", "forecast_status": forecast_status,
                       "local_measured_holdout_rmse": None, "previous_calibration_record": False})

        budget_ok = (
            card.get("scheduled_upload_utc") == SCHEDULED_DATE
            and budget.get("lane_max") == daily_cap == 2
            and scheduled_ids.count(candidate_id) == 1
            and len(schedule) == 2
            and set(scheduled_ids) == set(CANDIDATES)
            and len(today_uploads) == 0
        )
        guard_ok = (
            dry_run is not None
            and dry_run.get("live_upload") is False
            and dry_run.get("status") == "dry_run_validated"
            and dry_run.get("platform_submission_id") is None
            and dry_run.get("our_score") is None
            and dry_run.get("sha256") == artifact_hash
            and dry_run.get("daily_cap") == 2
            and dry_run.get("submissions_today_before") == 2
        )
        checks.append({"id": f"{candidate_id}:scheduled_daily_cap", "passed": budget_ok and guard_ok,
                       "scheduled_utc": SCHEDULED_DATE, "queued_for_date": scheduled_ids,
                       "planned_slots": len(schedule), "lane_daily_cap": daily_cap,
                       "guard_dry_run": {k: dry_run.get(k) for k in ("status", "live_upload", "submissions_today_before", "daily_cap", "platform_submission_id", "our_score")} if dry_run else None})

        candidates_out[candidate_id] = {
            "artifact": candidate["artifact"], "sha256": artifact_hash,
            "build_manifest": card.get("build_manifest"), "build_manifest_sha256": build_manifest_hash,
            "frozen_card": card_registration, "frozen_card_sha256": card_hash,
            "scheduled_utc": SCHEDULED_DATE, "purpose": "calibration",
            "guard_dry_run_command": command,
            "guard_dry_run_executed": guard_ok,
            "live_flag_present": False,
            "upload_performed": False,
        }

    require(len(checks) == 8, f"expected 8 checks, got {len(checks)}")
    passed = sum(bool(row["passed"]) for row in checks)
    guard_records = {c: candidates_out[c]["guard_dry_run_executed"] for c in CANDIDATES}
    return {
        "schema_version": 1,
        "status": "PASS" if passed == 8 else "BLOCKED",
        "as_of_utc": as_of,
        "scheduled_upload_utc": SCHEDULED_DATE,
        "checks_passed": passed,
        "checks_required": 8,
        "checks": checks,
        "candidates": candidates_out,
        "budget_state": {
            "as_of_utc_date_local_records": as_of,
            "2026-10-26_guard_reported_submissions_today_before": 2,
            "2026-10-26_lane_daily_cap": 2,
            "2026-10-27_completed_uploads_in_frozen_record": 0,
            "2026-10-27_queued_candidates": scheduled_ids,
            "2026-10-27_planned_slots_of_daily_cap": f"{len(schedule)}/{daily_cap}",
            "total_account_capacity_verified": final["upload_budget"].get("remaining_total_account_capacity_verified"),
            "note": "The 2026-10-27 schedule is exactly two authorized candidates, equal to the lane cap. Total account capacity remains unverified in the frozen registration; the live guarded uploader will enforce current UTC-day capacity at upload time.",
        },
        "commands": [candidates_out[c]["guard_dry_run_command"] for c in CANDIDATES],
        "guard_dry_runs": {
            "mode": "default dry-run; no --live flag",
            "checks": guard_records,
            "remote_uploads": 0,
            "note": "Guarded dry-runs validate the exact candidate packages and query current submission counts; they do not POST/upload. The preflight script itself is local-only.",
        },
        "upload_performed": False,
        "candidate_artifacts_modified": False,
        "source_hashes": {
            "candidates_manifest": digest(manifest_path),
            "final_two_registration": digest(final_path),
            "frozen_card": card_hash,
            "calibration_ledger": digest(ledger_path),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="UTC date YYYY-MM-DD (default: current UTC date)")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/final-two-a3-v2-preflight.json")
    parser.add_argument("--check-only", action="store_true", help="validate without writing report")
    args = parser.parse_args()
    as_of = args.as_of or datetime.now(timezone.utc).date().isoformat()
    report = make_report(as_of)
    encoded = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if not args.check_only:
        args.out.write_text(encoded, encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks_passed": report["checks_passed"],
                      "checks_required": report["checks_required"], "out": None if args.check_only else str(args.out)}, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
