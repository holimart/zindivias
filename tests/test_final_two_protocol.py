import hashlib
import json
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CGS_IDS = {
    "V2_model_cgs", "A1_physical_equal_cgs", "A2_spectral_cgs",
    "A3_dawid_skene_cgs", "B_from_A_cgs",
}


class FinalTwoProtocolTests(unittest.TestCase):
    def test_column_order_rescore_invalidates_old_selection_and_preserves_records(self):
        report_path = REPO / "reports/final-two.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        protocol_path = REPO / report["protocol"]["path"]
        digest = hashlib.sha256(protocol_path.read_bytes()).hexdigest()
        self.assertEqual(report["protocol"]["sha256"], digest)
        self.assertEqual(report["status"], "corrected_order_cgs_cohort_complete_final_A_B_uploaded")
        self.assertEqual([(x["slot"], x["candidate_id"]) for x in report["final_candidates"]],
                         [("A", "V2_model_cgs"), ("B", "B_from_A_cgs")])
        self.assertTrue(all(x["sha256"] and x["submission_id"] and x["public_rmse"] is not None
                            for x in report["final_candidates"]))
        selection = report["corrected_cgs_selection"]
        self.assertEqual(selection["winner"], "V2_model_cgs")
        self.assertTrue(selection["unique_outside_guard"])
        self.assertFalse(selection["tie_break_used"])
        self.assertEqual({x["candidate_id"] for x in selection["scores"]},
                         {"V2_model_cgs", "A1_physical_equal_cgs", "A2_spectral_cgs", "A3_dawid_skene_cgs"})
        self.assertEqual(report["upload_budget"]["queued_uploads"], [])
        self.assertEqual(report["upload_budget"]["reserved_final_uploads"], 2)
        self.assertIn("invalidated_final_selection", report)
        self.assertIn("invalidated_interim_public_ranking", report)
        self.assertTrue(report["score_integrity"]["historical_score_records_preserved"])
        self.assertFalse(report["score_integrity"]["historical_public_scores_valid_for_selection"])
        self.assertTrue(report["score_integrity"]["no_old_score_used_for_model_selection_or_value_changes"])
        self.assertTrue(report["score_integrity"]["corrected_format_scores_selection_eligible"])

        registrations = report["candidate_registrations"]
        by_id = {x["candidate_id"]: x for x in registrations}
        self.assertTrue(CGS_IDS <= set(by_id))
        for candidate_id in CGS_IDS:
            item = by_id[candidate_id]
            self.assertEqual(item["columns"], ["GEOID", "coverage_gap_score"])
            self.assertEqual(item["rows"], 9794)
            self.assertTrue(item["format_fix_only"])
            self.assertEqual(item["purpose"], "calibration")
            self.assertTrue(item["upload_performed"])
            self.assertEqual(item["upload_status"], "successful")
            self.assertIsInstance(item["public_rmse"], (int, float))
            if candidate_id == "B_from_A_cgs":
                self.assertFalse(item["score_selection_eligible"])
                self.assertIn("protocol_defined_B", item["score_selection_status"])
            else:
                self.assertTrue(item["score_selection_eligible"])
        for item in registrations:
            if item.get("public_rmse") is not None and item.get("candidate_id") not in CGS_IDS:
                self.assertIs(item.get("score_selection_eligible"), False)
        self.assertEqual(report["format_fix_upload_plan"]["uploads_performed_this_turn"], 3)
        self.assertEqual(report["format_fix_upload_plan"]["daily_cap"], 5)
        self.assertEqual(report["format_fix_upload_plan"]["2026-10-28_authorized_sequence"],
                         ["V2_model_cgs", "A1_physical_equal_cgs", "A2_spectral_cgs"])
        self.assertEqual(report["format_fix_upload_plan"]["authorization_addendum"],
                         "config/bias-cgs-upload-authorization-2026-10-28.json")
        self.assertEqual(report["corrected_cgs_partial_results"]["scored_count"], 4)
        self.assertTrue(report["corrected_cgs_partial_results"]["a_rule_applied"])
        self.assertEqual(report["corrected_cgs_partial_results"]["selected_A"], "V2_model_cgs")
        self.assertEqual(len(report["format_fix_upload_plan"]["2026-10-29_live_results"]), 2)

    def test_protocol_forbids_probes_and_uses_model_only_shrink(self):
        text = (REPO / "docs/final-two-protocol.md").read_text(encoding="utf-8")
        self.assertIn("No phase-3", text)
        self.assertIn("B_i = 0.5 * A_i + 0.5 * mean(A_j", text)
        self.assertIn("Never select a candidate because of a single public-score difference", text)
        self.assertIn("replaces the historical C0-anchor recommendation", text)
        self.assertIn("predicted-versus-actual error", text)


if __name__ == "__main__":
    unittest.main()
