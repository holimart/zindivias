import hashlib
import json
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class FinalTwoProtocolTests(unittest.TestCase):
    def test_pending_report_matches_frozen_protocol_and_deadlines(self):
        report_path = REPO / "reports/final-two.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        protocol_path = REPO / report["protocol"]["path"]
        digest = hashlib.sha256(protocol_path.read_bytes()).hexdigest()
        self.assertEqual(report["protocol"]["sha256"], digest)
        self.assertEqual(report["status"], "protocol_frozen_waiting_for_eligible_candidates")
        self.assertEqual([item["slot"] for item in report["final_candidates"]], ["A", "B"])
        for item in report["final_candidates"]:
            self.assertIsNone(item["candidate_id"])
            self.assertIsNone(item["sha256"])
            self.assertIsNone(item["public_rmse"])
        self.assertEqual(report["upload_budget"]["queued_uploads"], [])
        self.assertEqual(report["upload_budget"]["reserved_final_uploads"], 2)
        self.assertEqual(report["upload_budget"]["lane_capacity_estimate_oct25_to_oct30"], 12)
        upload_by = datetime.fromisoformat(report["deadlines_utc"]["final_successful_uploads_by"].replace("Z", "+00:00"))
        select_by = datetime.fromisoformat(report["deadlines_utc"]["operator_ui_selection_by"].replace("Z", "+00:00"))
        self.assertEqual((select_by - upload_by).total_seconds(), 32 * 3600)

    def test_protocol_forbids_probes_and_uses_model_only_shrink(self):
        text = (REPO / "docs/final-two-protocol.md").read_text(encoding="utf-8")
        self.assertIn("No phase-3", text)
        self.assertIn("B_i = 0.5 * A_i + 0.5 * mean(A_j", text)
        self.assertIn("Never select a candidate because of a single public-score difference", text)
        self.assertIn("replaces the historical C0-anchor recommendation", text)
        self.assertIn("predicted-versus-actual error", text)


if __name__ == "__main__":
    unittest.main()
