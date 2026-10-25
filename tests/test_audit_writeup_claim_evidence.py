"""Tests for the writeup claim-to-evidence audit (row bias-writeup-claim-evidence-audit)."""
from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "audit_writeup_claim_evidence", ROOT / "scripts/audit_writeup_claim_evidence.py")
audit = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(audit)
CONFIG = audit.load_config()
audit.configure(CONFIG)

POOL = audit.EvidencePool()
POOL.load()
INVENTORY = json.loads((ROOT / "reports/writeup-claim-inventory.json").read_text())
CLAIMS = {claim["claim_id"]: claim for claim in INVENTORY["claims"]}


class TokenizerTests(unittest.TestCase):
    def test_thousands_percents_and_negatives_are_extracted(self) -> None:
        tokens = {(value, decimals) for value, decimals, _
                  in audit.numeric_tokens("9,379 tracts fell 0.03488 by 12.37%")}
        self.assertIn((9379.0, 0), tokens)
        self.assertIn((0.03488, 5), tokens)
        self.assertIn((12.37, 2), tokens)

    def test_table_alignment_rows_and_years_are_ignored(self) -> None:
        self.assertEqual(audit.numeric_tokens("|---|---:|---:|"), [])
        self.assertFalse(audit.is_informative(1.0, 0))
        self.assertFalse(audit.is_informative(0.0, 1))
        self.assertTrue(audit.is_informative(0.03488, 5))

    def test_fenced_code_is_stripped_before_tokenizing(self) -> None:
        stripped = audit.strip_fenced_code("value 0.1234\n```bash\npython x.py --out 9.9\n```\n")
        self.assertIn(0.1234, {value for value, _, _ in audit.numeric_tokens(stripped)})
        self.assertNotIn(9.9, {value for value, _, _ in audit.numeric_tokens(stripped)})


class PoolMatchingTests(unittest.TestCase):
    def test_percent_token_matches_a_stored_fraction(self) -> None:
        observed = [-0.06059553198599227, 0.1601997580693138]
        self.assertTrue(POOL.matches(6.06, 2, observed))
        self.assertTrue(POOL.matches(16.02, 2, observed))
        self.assertFalse(POOL.matches(6.07, 2, observed))

    def test_rounding_tolerance_is_the_token_precision(self) -> None:
        self.assertTrue(POOL.matches(9.95, 2, [0.09954]))
        self.assertFalse(POOL.matches(9.95, 3, [0.09954]))


class DerivedAggregateTests(unittest.TestCase):
    def test_percentage_derivation_reproduces_a_writeup_share(self) -> None:
        entry = {"operation": "percentage",
                 "evidence": "reports/confidence-by-stratum.json",
                 "pointer_a": "/regions/maricopa-az/poi_rows_confidence_below_0_5",
                 "pointer_b": "/regions/maricopa-az/poi_rows_with_valid_confidence"}
        self.assertAlmostEqual(audit.compute_aggregate(POOL, entry), 9.95, places=2)

    def test_sum_derivation_reproduces_the_official_tract_total(self) -> None:
        entry = {"operation": "sum", "evidence": "reports/edge-cases.json",
                 "path": "/regions", "key": "official_tracts"}
        self.assertEqual(audit.compute_aggregate(POOL, entry), 9379.0)

    def test_unknown_operation_fails_closed_and_missing_key_leaves_token_uncovered(self) -> None:
        with self.assertRaises(ValueError):
            audit.compute_aggregate(POOL, {"operation": "median",
                                           "evidence": "reports/edge-cases.json"})
        missing = audit.compute_aggregate(POOL, {"operation": "sum", "path": "/nope",
                                                 "key": "x",
                                                 "evidence": "reports/edge-cases.json"})
        self.assertIn(missing, (None, 0.0))
        claim = copy.deepcopy(CLAIMS["edge-cases-002"])
        baseline = audit.verify_claim(copy.deepcopy(CLAIMS["edge-cases-002"]), POOL)
        self.assertEqual(len(baseline["derived_tokens_verified"]),
                         len(claim["derived_tokens"]))
        self.assertEqual({entry["key"] for entry in claim["derived_tokens"]}
                         >= {"official_tracts", "zero_overture_named_roads"}, True)
        claim["derived_tokens"] = [entry for entry in claim["derived_tokens"]
                                   if entry["key"] != "official_tracts"]
        result = audit.verify_claim(claim, POOL)
        self.assertEqual(len(result["derived_tokens_verified"]),
                         len(baseline["derived_tokens_verified"]) - 1)

    def test_percentage_with_zero_denominator_is_not_computed(self) -> None:
        entry = {"operation": "percentage", "evidence": "reports/edge-cases.json",
                 "pointer_a": "/regions/maricopa-az/official_tracts",
                 "pointer_b": "/regions/maricopa-az/zero_land_area"}
        self.assertIsNone(audit.compute_aggregate(POOL, entry))


class LanguageGateTests(unittest.TestCase):
    def test_removed_layer_named_in_prose_without_negation_is_usage(self) -> None:
        self.assertTrue(audit.classify_prohibited("we joined TIGER roads to the tracts")
                        ["usage"])

    def test_quoted_organiser_rule_text_counts_as_disclosure(self) -> None:
        text = ("The official rules also warn that Overture/TIGER named-road length ratios "
                "vary from 0.71 to 1.59")
        result = audit.classify_prohibited(text)
        self.assertTrue(result["disclosure"])
        self.assertEqual(result["usage"], [])

    def test_documented_historical_component_name_is_disclosure(self) -> None:
        result = audit.classify_prohibited(
            "v0 transport/building/CBP components were replaced by provided Overture layers")
        self.assertEqual(result["usage"], [])

    def test_accuracy_assertion_needs_both_language_and_a_number(self) -> None:
        self.assertTrue(audit.accuracy_assertion("our public RMSE is 0.0847"))
        self.assertFalse(audit.accuracy_assertion("the proxy score mean is 0.0847"))

    def test_workspace_evidence_is_limited_to_the_rules_document(self) -> None:
        allowed = set(CONFIG["workspace_documentation_evidence"])
        for claim in CLAIMS.values():
            for entry in claim["evidence"]:
                if entry["path"].startswith("WORKSPACE:"):
                    self.assertIn(entry["path"], allowed)


class SectionRuleTests(unittest.TestCase):
    def test_document_default_rule_is_used_for_unmapped_headings(self) -> None:
        rule = audit.section_rule("docs/writeup/methodology.md", "A heading nobody mapped", 1)
        self.assertEqual(rule.get("occurrence"), "any")

    def test_every_claim_class_has_a_document_default(self) -> None:
        for doc in CONFIG["docs"]:
            self.assertTrue(any(rule["doc"] == doc and rule.get("occurrence") == "any"
                                for rule in CONFIG["sections"]))


class InventoryTests(unittest.TestCase):
    def test_every_claim_has_evidence_pointer_and_both_commands(self) -> None:
        for claim in CLAIMS.values():
            self.assertTrue(claim["evidence"], claim["claim_id"])
            self.assertTrue(all(entry["sha256"] for entry in claim["evidence"]))
            self.assertIn("scripts/verify_writeup_claim.py", claim["verify_command"])
            self.assertIn("scripts/verify_writeup_claim.py", claim["verify_command"])
            self.assertTrue(claim["reproduce_command"])
            if claim["provenance_class"].startswith("measured_"):
                self.assertTrue(claim["reproduce_products"], claim["claim_id"])
                self.assertTrue(all(product["sha256"]
                                    for product in claim["reproduce_products"]))

    def test_audit_never_cites_its_own_artifacts_as_evidence(self) -> None:
        self.assertNotIn("reports/writeup-claim-inventory.json", CONFIG["evidence_allowlist"])
        self.assertNotIn("reports/writeup-claim-evidence-audit.json",
                         CONFIG["evidence_allowlist"])
        for claim in CLAIMS.values():
            for entry in claim["evidence"]:
                self.assertNotIn("writeup-claim", entry["path"])

    def test_hedged_classes_all_carry_a_qualification_in_scope(self) -> None:
        for claim in CLAIMS.values():
            if claim["provenance_class"] not in CONFIG["hedge_required_classes"]:
                continue
            text = "\n".join([claim["text"], *claim["hedge_context"]]).lower()
            self.assertTrue(any(phrase in text for phrase in CONFIG["hedge_phrases"]),
                            claim["claim_id"])

    def test_report_is_deterministic_and_carries_no_wall_clock_key(self) -> None:
        report = json.loads((ROOT / "reports/writeup-claim-evidence-audit.json").read_text())
        stack = [report]
        forbidden = ("timestamp", "generated_at", "utcnow", "elapsed", "duration", "wall_clock")
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, child in node.items():
                    self.assertFalse(any(word in key.lower() for word in forbidden), key)
                    stack.append(child)
            elif isinstance(node, list):
                stack.extend(node)
        self.assertTrue(report["acceptance"]["all_passed"])


class NegativeControlTests(unittest.TestCase):
    """Tampered copies must be rejected, so the gates are not vacuous."""

    def test_derived_token_off_by_one_fails_the_verifier(self) -> None:
        claim = copy.deepcopy(CLAIMS["methodology-042"])
        intact = audit.verify_claim(copy.deepcopy(CLAIMS["methodology-042"]), POOL)
        self.assertEqual(len(intact["derived_tokens_verified"]),
                         len(CLAIMS["methodology-042"]["derived_tokens"]))
        claim["derived_tokens"][0]["value"] = claim["derived_tokens"][0]["value"] + 1.0
        tampered = audit.verify_claim(claim, POOL)
        self.assertEqual(len(tampered["derived_tokens_verified"]),
                         len(intact["derived_tokens_verified"]) - 1)
        self.assertTrue(tampered["tokens_uncovered"])

    def test_named_evidence_with_a_broken_hash_fails(self) -> None:
        claim = copy.deepcopy(CLAIMS["bias-discovery-001"])
        claim["evidence"][0]["sha256"] = "0" * 64
        self.assertFalse(audit.verify_claim(claim, POOL)["evidence_hashes_verified"])

    def test_accuracy_claim_in_a_scope_statement_is_rejected(self) -> None:
        claim = copy.deepcopy(CLAIMS["methodology-001"])
        claim["text"] = "Our submitted public RMSE was 0.0847 on the leaderboard."
        self.assertFalse(audit.verify_claim(claim, POOL)["accuracy_ok"])

    def test_prohibited_source_language_in_a_model_claim_is_rejected(self) -> None:
        claim = copy.deepcopy(CLAIMS["methodology-013"])
        claim["provenance_class"] = "measured_model_only"
        claim["text"] = ("We scored the building gap by opening usgs rows and cbp counts for "
                         "8,000 tracts, mean 0.02856")
        self.assertFalse(audit.verify_claim(claim, POOL)["prohibited_ok"])

    def test_unhedged_proxy_section_is_rejected(self) -> None:
        """A proxy-score claim with no hedge anywhere must be rejected.

        Selected by section and provenance class rather than by claim id, because claim ids are
        positional and shift when a paragraph is added earlier in the document.
        """
        hedges = [phrase.lower() for phrase in CONFIG["hedge_phrases"]]
        candidates = [claim for claim in CLAIMS.values()
                      if claim["doc"].endswith("bias-discovery.md")
                      and claim["section"] == "Quantified proxy evidence"
                      and claim["provenance_class"] == "measured_provided_proxy"
                      and not any(phrase in claim["text"].lower() for phrase in hedges)]
        self.assertTrue(candidates, "no unhedged proxy claim available to test")
        rejected = 0
        for candidate in candidates:
            claim = copy.deepcopy(candidate)
            claim["hedge_context"] = ["The screen found 248 high-field rural tracts."]
            if not audit.verify_claim(claim, POOL)["hedge_ok"]:
                rejected += 1
        self.assertGreater(rejected, 0, "an unhedged proxy claim was accepted")

    def test_registry_products_may_not_change_quietly(self) -> None:
        self.assertIn("reports/candidates.json", CONFIG["reproduce_never_disclosed_products"])
        self.assertIn("reports/lb-score-algebra.json",
                      CONFIG["reproduce_never_disclosed_products"])


if __name__ == "__main__":
    unittest.main()
