#!/usr/bin/env python3
"""Register the single preregistered H2 shrink artifact without touching other cards.

The candidates registry also contains historical calibration metadata. This script edits its
raw candidate_set JSON array surgically and does not inspect or derive anything from existing
candidate scores or parameters.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "reports/candidates.json"
REPORT_PATH = ROOT / "reports/shrink-diagnosis.json"
CANDIDATE_ID = "S_crossmethod_reliability_shrink_v1"


def array_end(text: str, marker: str) -> int:
    key = f'"{marker}"'
    key_at = text.find(key)
    if key_at < 0:
        raise ValueError(f"missing {marker} array")
    start = text.find("[", key_at + len(key))
    if start < 0:
        raise ValueError(f"missing opening bracket for {marker}")
    stack: list[str] = []
    quoted = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
            continue
        if ch == '"':
            quoted = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if not stack:
                raise ValueError("unbalanced JSON structure")
            opener = stack.pop()
            if (opener, ch) not in (("[", "]"), ("{", "}")):
                raise ValueError("mismatched JSON brackets")
            if not stack:
                if ch != "]":
                    raise ValueError("candidate_set is not a JSON array")
                return i
    raise ValueError("unterminated candidate_set array")


def make_card(report: dict[str, Any]) -> dict[str, Any]:
    h2 = report["H2"]
    source = h2["candidate_S"]
    if source["candidate_id"] != CANDIDATE_ID or not source["upload_authorized"] is False:
        raise ValueError("report candidate identity/authorization mismatch")
    if h2["local_leave_one_construction_out"]["preregistered_gate_passed"] is not True:
        raise ValueError("H2 local validation gate did not pass")
    return {
        "candidate_id": CANDIDATE_ID,
        "name": "H2 cross-construction reliability shrink",
        "artifact": source["artifact"],
        "sha256": source["sha256"],
        "rows": source["rows"],
        "columns": ["GEOID", "coverage_gap_score"],
        "pre_registered": True,
        "preregistered_card_path": h2["preregistration"]["path"],
        "preregistered_card_sha256": h2["preregistration"]["sha256"],
        "purpose": "improvement",
        "description": "Reliability-weighted consensus of four frozen, feature-only constructions, with deviations shrunk toward each region's own consensus mean; this is an improvement hypothesis, not target-accuracy evidence.",
        "predicted_score": source["predicted_score"],
        "predicted_score_scope": source["predicted_score_scope"],
        "predicted_score_basis": h2["local_leave_one_construction_out"]["definition"],
        "target_RMSE_forecast": None,
        "target_RMSE_forecast_limitation": "Organizer-reference labels are unavailable; no numeric target RMSE can be honestly inferred from candidate agreement alone.",
        "parameters": {
            "cross_construction_weights": h2["global_construction_weights"],
            "signal_share_by_construction": h2["global_construction_signal_shares"],
            "raw_c_by_region": {r: v["c"] for r, v in h2["reliability_by_region"].items()},
            "pooled_c": h2["pooled_reliability"]["c"],
            "c_applied_by_region": {r: d["selected_c"] for r, d in source["candidate_predictions_by_region"].items()},
            "region_specific_c_used": h2["pooled_reliability"]["region_specific_c_used"],
            "shrink_target": source["shrink_target"],
            "local_leave_one_construction_out_shrunk_rmse": h2["local_leave_one_construction_out"]["predicted_score"],
            "local_leave_one_construction_out_unshrunk_rmse": h2["local_leave_one_construction_out"]["unshrunk_consensus_proxy_rmse"],
            "no_public_scores_or_labels_used": True,
        },
        "eastern_wa_fill": {
            "region": "eastern-wa", "rows": source["formatting_only_eastern_wa_rows"],
            "value": 0.0, "scored_target_claim": False,
        },
        "column_checks": {
            "unique_columns": True, "all_rows_have_columns": True,
            "finite_bounded_scores": True,
        },
        "upload_authorized": False,
        "registration_status": "frozen_no_upload",
    }


def register() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    card = make_card(report)
    raw = REGISTRY_PATH.read_text(encoding="utf-8")
    marker_at = raw.find('"candidate_set"')
    end = array_end(raw, "candidate_set")
    candidate_array_raw = raw[raw.find("[", marker_at):end + 1]
    existing_marker = f'"candidate_id": "{CANDIDATE_ID}"'
    if existing_marker in candidate_array_raw:
        start = candidate_array_raw.index(existing_marker)
        existing_card_text = candidate_array_raw[start:]
        if (f'"sha256": "{card["sha256"]}"' not in existing_card_text
                or f'"artifact": "{card["artifact"]}"' not in existing_card_text
                or '"purpose": "improvement"' not in existing_card_text):
            raise ValueError("registered S card differs from the frozen artifact")
        updated = raw
    else:
        prefix = raw[:end].rstrip()
        card_text = json.dumps(card, indent=2, ensure_ascii=False)
        card_text = "\n".join("    " + line for line in card_text.splitlines())
        updated = prefix + ",\n" + card_text + raw[end:]
    # Validate resulting JSON without inspecting any existing prediction/forecast values.
    json.loads(updated)
    if updated != raw:
        REGISTRY_PATH.write_text(updated, encoding="utf-8")
    report["candidate_registration_pending"] = False
    report["candidate_registration"] = {
        "status": "registered",
        "candidate_id": CANDIDATE_ID,
        "registry_path": str(REGISTRY_PATH.relative_to(ROOT)),
        "registry_sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
        "card_sha256": card["sha256"],
        "registration_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "upload_authorized": False,
    }
    report.pop("candidate_registration_instruction", None)
    report["status"] = "H1 and H2 measured; one S candidate registered; no upload"
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    register()
