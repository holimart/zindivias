#!/usr/bin/env python3
"""Register pre-registered format-only CGS artifacts and void historical scores for selection."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_REL = "config/bias-column-order-rescore-preregistered.json"
REGISTRY_REL = "reports/candidates.json"
FINAL_TWO_REL = "reports/final-two.json"
NOTICE = (
    "Historical public scores from five-column submissions are void for candidate selection: "
    "Zindi scores column 2 by position, which was transport_gap in the per-region sample order. "
    "The underlying records and score values are retained only as historical records."
)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def mark_historical_scores_void(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("public_rmse") is not None:
            node["score_selection_eligible"] = False
            node["score_selection_status"] = "void_column_order_mismatch"
            node["score_selection_note"] = NOTICE
        for value in node.values():
            mark_historical_scores_void(value)
    elif isinstance(node, list):
        for value in node:
            mark_historical_scores_void(value)


def register(repo: Path, preregistration_commit: str) -> dict[str, Any]:
    config = json.loads((repo / CONFIG_REL).read_text(encoding="utf-8"))
    config_hash = file_sha(repo / CONFIG_REL)
    build_report = json.loads((repo / "reports/bias-column-order-cgs-build.json").read_text(encoding="utf-8"))
    report_by_id = {x["candidate_id"]: x for x in build_report["candidates"]}
    registry_path = repo / REGISTRY_REL
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    originals = {x["candidate_id"]: x for x in registry["candidate_set"]}
    existing = {x["candidate_id"]: x for x in registry["candidate_set"]}
    new_candidate_entries = []
    for spec in config["sources"]:
        candidate_id = spec["candidate_id"]
        if candidate_id in existing:
            candidate = existing[candidate_id]
            if candidate.get("sha256") != report_by_id[candidate_id]["sha256"]:
                raise ValueError(f"existing registration for {candidate_id} has a different artifact hash")
            continue
        source = originals.get(spec["source_candidate_id"])
        built = report_by_id.get(candidate_id)
        if source is None or built is None:
            raise ValueError(f"missing registered source or built artifact for {candidate_id}")
        if source.get("sha256") != spec["source_sha256"] or built.get("source_sha256") != spec["source_sha256"]:
            raise ValueError(f"frozen source hash mismatch while registering {candidate_id}")
        candidate = copy.deepcopy(source)
        candidate["candidate_id"] = candidate_id
        candidate["name"] = f"{source.get('name', spec['source_candidate_id'])} (CGS column-order format fix)"
        candidate["artifact"] = built["artifact"]
        candidate["sha256"] = built["sha256"]
        candidate["rows"] = built["rows"]
        candidate["columns"] = ["GEOID", "coverage_gap_score"]
        candidate["pre_registered"] = True
        candidate["purpose"] = "calibration"
        candidate["description"] = (
            f"Format-only copy of {spec['source_candidate_id']}: frozen coverage_gap_score text "
            "is unchanged; only the file columns are reduced/reordered to GEOID,coverage_gap_score "
            "as required by the Data-page template. No new prediction or accuracy claim."
        )
        candidate["source_candidate_id"] = spec["source_candidate_id"]
        candidate["source_artifact"] = spec["source_artifact"]
        candidate["source_sha256"] = spec["source_sha256"]
        candidate["format_fix_only"] = True
        candidate["format_fix_note"] = NOTICE
        candidate["preregistered_card_path"] = spec["pre_registered_card_path"]
        candidate["preregistered_card_sha256"] = spec["pre_registered_card_sha256"]
        candidate["preregistration_commit"] = preregistration_commit
        candidate["pre_registered_card"] = spec["pre_registered_card_path"]
        candidate["pre_registered_card_sha256"] = spec["pre_registered_card_sha256"]
        candidate["upload_authorized"] = True
        candidate["upload_not_before_utc"] = "2026-10-29T00:00:00Z"
        candidate["registration_status"] = "preregistered_format_fix_waiting_scheduled_upload"
        candidate["upload_performed"] = False
        candidate["submission_id"] = None
        candidate["public_rmse"] = None
        candidate["score_source"] = None
        candidate["submitted_utc"] = None
        candidate["uploads_used"] = 0
        candidate["max_uploads"] = 1
        for key in ("upload_status", "platform_results"):
            candidate.pop(key, None)
        candidate["local_invariants"] = {
            "report": "reports/bias-column-order-cgs-build.json",
            "manifest": built["manifest"],
            "manifest_sha256": built["manifest_sha256"],
            "exact_template_ids_and_order": True,
            "rows": 9794,
            "header": ["GEOID", "coverage_gap_score"],
            "coverage_gap_score_byte_identical_to_source": True,
            "source_values_recomputed": False,
            "upload_performed": False,
        }
        new_candidate_entries.append(candidate)
    registry["candidate_set"].extend(new_candidate_entries)
    registry["column_order_score_integrity_notice"] = {
        "historical_public_scores_valid_for_selection": False,
        "historical_score_records_preserved": True,
        "reason": NOTICE,
        "corrected_column_order": ["GEOID", "coverage_gap_score"],
        "format_fix_preregistration_config": CONFIG_REL,
        "format_fix_preregistration_config_sha256": config_hash,
        "cgs_candidate_ids": [x["candidate_id"] for x in config["sources"]],
    }
    write_json(registry_path, registry)

    final_path = repo / FINAL_TWO_REL
    final_two = json.loads(final_path.read_text(encoding="utf-8"))
    old_final = copy.deepcopy(final_two.get("final_candidates", []))
    old_interim = copy.deepcopy(final_two.get("interim_public_ranking", {}))
    mark_historical_scores_void(final_two)
    mark_historical_scores_void(old_final)
    mark_historical_scores_void(old_interim)
    if old_final and not final_two.get("invalidated_final_selection") and any(x.get("candidate_id") for x in old_final):
        final_two["invalidated_final_selection"] = {
            "records": old_final,
            "void_for_selection": True,
            "reason": NOTICE,
        }
    if old_interim and not final_two.get("invalidated_interim_public_ranking"):
        final_two["invalidated_interim_public_ranking"] = {
            "records": old_interim,
            "void_for_selection": True,
            "reason": NOTICE,
        }
    final_two["status"] = "column_order_cgs_preregistered_pending_scheduled_uploads"
    final_two["final_candidates"] = [
        {"slot": "A", "candidate_id": None, "artifact": None, "sha256": None,
         "submission_id": None, "public_rmse": None,
         "selection_status": "awaiting_column_order_corrected_scores"},
        {"slot": "B", "candidate_id": None, "artifact": None, "sha256": None,
         "submission_id": None, "public_rmse": None,
         "selection_status": "awaiting_column_order_corrected_A"},
    ]
    old_queue = final_two.get("upload_budget", {}).get("queued_uploads", [])
    if old_queue and not final_two.get("superseded_upload_queue"):
        final_two["superseded_upload_queue"] = {
            "records": old_queue,
            "status": "void_column_order_rescore",
            "reason": NOTICE,
        }
    final_two.setdefault("upload_budget", {})["queued_uploads"] = []
    final_two["selection_policy"]["candidate_A"] = (
        "Lowest eligible public RMSE among the frozen model cohort using only the new _cgs artifacts; "
        "all earlier public scores are void for selection because column 2 was transport_gap."
    )
    final_two["selection_policy"]["candidate_B"] = (
        "Rebuild the deterministic 50% shrink companion from the _cgs A-rule winner; no target means, "
        "historical scores, or cell adjustments."
    )
    final_two["interim_public_ranking"] = {
        "scored_subset": old_interim.get("scored_subset", []),
        "successful_candidate_scores": old_interim.get("successful_candidate_scores", []),
        "lower_rmse_candidate": None,
        "provisional_candidate_A": None,
        "provisional_A_platform_submission_id": None,
        "comparisons_to_provisional_A": [],
        "A_uniquely_below_all_cohort_candidates_outside_pairwise_guard": None,
        "final_two_selected": False,
        "selection_basis": "No earlier public score is valid for selection; await correctly ordered _cgs scores.",
    }
    final_two["score_integrity"] = {
        "finding": "Zindi scores the second CSV column by position; Data-page SampleSubmission.csv puts coverage_gap_score second, while previous five-column files put transport_gap second.",
        "historical_public_scores_valid_for_selection": False,
        "historical_score_records_preserved": True,
        "historical_score_records_marked_or_covered_by_notice_in": [
            "candidate_registrations", "upload_budget.completed_uploads", "interim_public_ranking",
            "invalidated_interim_public_ranking", "invalidated_final_selection",
            "reports/submissions.jsonl", "reports/lb-calibration.jsonl",
        ],
        "note": NOTICE,
        "corrected_column_order": ["GEOID", "coverage_gap_score"],
        "cgs_candidates": [x["candidate_id"] for x in config["sources"]],
        "no_old_score_used_for_model_selection_or_value_changes": True,
    }
    final_two["format_fix_upload_plan"] = {
        "uploads_permitted_before_utc": "2026-10-29T00:00:00Z",
        "uploads_performed_this_turn": 0,
        "daily_cap": 2,
        "2026-10-29_before_00Z": [],
        "2026-10-29_from_00Z": ["V2_model_cgs", "A1_physical_equal_cgs"],
        "2026-10-30_before_12Z": [
            "the better of A3_dawid_skene_cgs/A2_spectral_cgs under the A-rule after valid CGS scores",
            "B rebuilt from the A-rule winner",
        ],
        "note": "Steward schedule; no live upload in this turn. If the A-rule winner is not V2_model, rebuild and register its B companion before the second-day plan."
    }
    final_two["column_order_rescore_preregistration"] = {
        "config": CONFIG_REL,
        "config_sha256": config_hash,
        "preregistration_commit": preregistration_commit,
        "registered_candidates": [
            {"candidate_id": x["candidate_id"], "source_candidate_id": x["source_candidate_id"],
             "artifact": report_by_id[x["candidate_id"]]["artifact"],
             "sha256": report_by_id[x["candidate_id"]]["sha256"],
             "purpose": "calibration", "card_path": x["pre_registered_card_path"],
             "card_sha256": x["pre_registered_card_sha256"], "format_fix_only": True,
             "upload_performed": False}
            for x in config["sources"]
        ],
    }
    current_regs = {x.get("candidate_id") for x in final_two.get("candidate_registrations", [])}
    for spec in config["sources"]:
        if spec["candidate_id"] in current_regs:
            continue
        built = report_by_id[spec["candidate_id"]]
        final_two.setdefault("candidate_registrations", []).append({
            "candidate_id": spec["candidate_id"],
            "registered_utc": "2026-10-28",
            "preregistration_commit": preregistration_commit,
            "preregistered_card_path": spec["pre_registered_card_path"],
            "preregistered_card_sha256": spec["pre_registered_card_sha256"],
            "artifact": built["artifact"],
            "sha256": built["sha256"],
            "rows": built["rows"],
            "columns": ["GEOID", "coverage_gap_score"],
            "format_fix_only": True,
            "format_fix_of": spec["source_candidate_id"],
            "format_fix_note": NOTICE,
            "purpose": "calibration",
            "prediction_written_before_any_upload": True,
            "prediction_basis": "No prediction was recalculated; this artifact copies the frozen source coverage_gap_score text unchanged.",
            "local_invariants": {
                "report": "reports/bias-column-order-cgs-build.json",
                "all_gates_passed": True,
                "exact_template_ids_and_order": True,
                "coverage_gap_score_byte_identical_to_source": True,
            },
            "upload_authorized": True,
            "upload_not_before_utc": "2026-10-29T00:00:00Z",
            "upload_performed": False,
            "submission_id": None,
            "public_rmse": None,
            "eligibility": "format-corrected calibration candidate; awaiting scheduled upload",
        })
    write_json(final_path, final_two)
    return {
        "registered_candidate_count": len(new_candidate_entries),
        "final_two_candidates": [x["candidate_id"] for x in final_two["final_candidates"]],
        "historical_scores_void_for_selection": final_two["score_integrity"]["historical_public_scores_valid_for_selection"] is False,
        "uploads_performed": 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--preregistration-commit", required=True)
    args = ap.parse_args()
    print(json.dumps(register(args.repo, args.preregistration_commit), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
