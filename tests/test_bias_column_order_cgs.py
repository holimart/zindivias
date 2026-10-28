from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = {
    "V2_model_cgs": "V2_model",
    "A1_physical_equal_cgs": "A1_physical_equal",
    "A2_spectral_cgs": "A2_spectral",
    "A3_dawid_skene_cgs": "A3_dawid_skene",
    "B_from_A_cgs": "B_from_A",
}
REQUIRED_HEADER = ["GEOID", "coverage_gap_score"]


def read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    return header, rows


@pytest.mark.parametrize(("cgs_id", "source_id"), CANDIDATES.items())
def test_cgs_candidate_has_data_page_header_and_byte_identical_values(cgs_id: str, source_id: str):
    output = ROOT / f"reports/candidates/{cgs_id}.csv"
    source = ROOT / f"reports/candidates/{source_id}.csv"
    output_header, output_rows = read_rows(output)
    source_header, source_rows = read_rows(source)

    assert output_header == REQUIRED_HEADER
    assert source_header == ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]
    assert len(output_rows) == len(source_rows) == 9794
    assert all(len(row) == 2 for row in output_rows)
    assert all(len(row) == 5 for row in source_rows)
    assert [(row[0], row[1]) for row in output_rows] == [(row[0], row[4]) for row in source_rows]

    manifest_path = ROOT / f"reports/candidates/{cgs_id}_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["candidate_id"] == cgs_id
    assert manifest["source_candidate_id"] == source_id
    assert manifest["format_fix_only"] is True
    assert manifest["copied_values_byte_identical"] is True
    assert manifest["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    registry = json.loads((ROOT / "reports/candidates.json").read_text(encoding="utf-8"))
    candidates = {x["candidate_id"]: x for x in registry["candidate_set"]}
    assert candidates[cgs_id]["preregistered_card_path"] == candidates[source_id].get(
        "preregistered_card_path", candidates[source_id].get("pre_registered_card")
    )
    assert candidates[cgs_id]["preregistered_card_sha256"] == candidates[source_id].get(
        "preregistered_card_sha256", candidates[source_id].get("pre_registered_card_sha256")
    )


def test_new_format_candidates_are_registered_and_final_slots_are_reset():
    registry = json.loads((ROOT / "reports/candidates.json").read_text(encoding="utf-8"))
    candidates = {x["candidate_id"]: x for x in registry["candidate_set"]}
    assert set(CANDIDATES) <= set(candidates)
    for cgs_id in CANDIDATES:
        candidate = candidates[cgs_id]
        assert candidate["pre_registered"] is True
        assert candidate["purpose"] == "calibration"
        assert candidate["columns"] == REQUIRED_HEADER
        assert candidate["format_fix_only"] is True

    final_two = json.loads((ROOT / "reports/final-two.json").read_text(encoding="utf-8"))
    assert [(x["slot"], x["candidate_id"]) for x in final_two["final_candidates"]] == [
        ("A", None), ("B", None)
    ]
    assert final_two["score_integrity"]["historical_public_scores_valid_for_selection"] is False
    assert final_two["score_integrity"]["historical_score_records_preserved"] is True
    assert final_two["score_integrity"]["no_old_score_used_for_model_selection_or_value_changes"] is True

    def check_score_flags(node):
        if isinstance(node, dict):
            if node.get("public_rmse") is not None:
                if node.get("candidate_id") in CANDIDATES:
                    assert node.get("score_selection_eligible") is True
                else:
                    assert node.get("score_selection_eligible") is False
            for value in node.values():
                check_score_flags(value)
        elif isinstance(node, list):
            for value in node:
                check_score_flags(value)
    check_score_flags(final_two)

    preflight = json.loads((ROOT / "reports/bias-column-order-cgs-preflight.json").read_text(encoding="utf-8"))
    assert preflight["result"] == "PASS"
    assert preflight["uploads_performed"] == 0
    assert {x["candidate_id"] for x in preflight["candidates"]} == set(CANDIDATES)
    assert all(x["guarded_tool_status"] == "dry_run_validated" and not x["live_upload"]
               for x in preflight["candidates"])
    assert all(x["git_commit_at_dry_run"] == preflight["candidates"][0]["git_commit_at_dry_run"]
               for x in preflight["candidates"])


def test_writeup_documents_the_two_column_orders_and_void_historical_scores():
    text = (ROOT / "docs/writeup/final-writeup.md").read_text(encoding="utf-8")
    assert "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score" in text
    assert "GEOID,coverage_gap_score" in text
    assert "void for candidate selection" in text
