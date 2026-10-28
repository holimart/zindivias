import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_s2_writeup_claim_evidence", ROOT / "scripts/audit_s2_writeup_claim_evidence.py")
audit = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(audit)
COMPARISON = json.loads((ROOT / audit.COMPARISON).read_text())
SHRINK_TEXT = (ROOT / audit.SHRINK_DOC).read_text()


class S2WriteupClaimEvidenceTests(unittest.TestCase):
    def test_comparison_table_all_twelve_rows_match_frozen_report(self):
        claims, errors = audit.compare_table_rows(SHRINK_TEXT, COMPARISON)
        self.assertEqual(len(claims), 12)
        self.assertEqual(errors, [])
        self.assertTrue(all(claim["numbers_verified"] for claim in claims))
        self.assertTrue(all(claim["evidence"]["sha256"] == audit.EXPECTED_HASHES[audit.COMPARISON]
                            for claim in claims))

    def test_mutated_comparison_number_is_detected(self):
        changed = SHRINK_TEXT.replace("0.095875", "0.999999", 1)
        claims, errors = audit.compare_table_rows(changed, COMPARISON)
        self.assertTrue(errors)
        matched = [c for c in claims if c["claim_id"] ==
                   "comparison-maricopa-az-S2_h1b_assumption_bound_v1"][0]
        self.assertFalse(matched["numbers_verified"])

    def test_frozen_audit_has_zero_unsupported_quantitative_claims(self):
        report = audit.build_audit()
        acceptance = report["acceptance"]
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(acceptance["quantitative_claim_count"], 26)
        self.assertEqual(acceptance["quantitative_claims_with_direct_report_path_hash_and_pointer"], 26)
        self.assertEqual(acceptance["unsupported_quantitative_or_accuracy_claim_count"], 0)
        self.assertEqual(acceptance["unsupported_claims"], [])
        self.assertTrue(acceptance["explicit_sensitivity_not_accuracy_statement"])

    def test_every_claim_record_carries_frozen_source_and_caveat(self):
        report = audit.build_audit()
        for claim in report["claim_matrix"]:
            self.assertTrue(claim["evidence"]["path"], claim["claim_id"])
            self.assertEqual(len(claim["evidence"]["sha256"]), 64)
            self.assertTrue(claim["evidence"]["json_pointer"], claim["claim_id"])
            self.assertTrue(claim["quoted_caveat"], claim["claim_id"])

    def test_family_s_s2_is_distinguished_from_h1b_composite_s2(self):
        report = audit.build_audit()
        family = [c for c in report["claim_matrix"]
                  if c["claim_id"] == "final-writeup-family-s-s2-channel"][0]
        self.assertTrue(family["numbers_verified"])
        self.assertIn("not the separate", family["claim_quote"])
        self.assertTrue(report["acceptance"]["family_s_s2_label_and_counts_verified"])

    def test_sensitivity_report_thresholds_are_verified(self):
        report = audit.build_audit()
        self.assertTrue(report["acceptance"]["all_four_facility_mask_region_claims_verified"])
        self.assertTrue(report["acceptance"]["all_eight_building_offset_region_claims_verified"])
        self.assertTrue(report["safety"]["target_accuracy_inferred"] is False)


if __name__ == "__main__":
    unittest.main()
