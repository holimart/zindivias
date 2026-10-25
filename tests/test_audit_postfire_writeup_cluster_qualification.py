"""Tests for the post-fire county-cluster writeup qualification screen.

The screen is pure text and JSON work: no network, no resampling, no reference layer, no label and no
platform access. These tests check that it really counts (it is not vacuous), that it fails when the
qualification is removed, and that it never writes a committed file.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MODULE = importlib.util.spec_from_file_location(
    "audit_postfire_writeup_cluster_qualification",
    ROOT / "scripts/audit_postfire_writeup_cluster_qualification.py")
audit = importlib.util.module_from_spec(MODULE)
sys.modules["audit_postfire_writeup_cluster_qualification"] = audit
MODULE.loader.exec_module(audit)

CONFIG_PATH = ROOT / "config/postfire-writeup-cluster-qualification-preregistered.json"
REPORT_PATH = ROOT / "reports/postfire-writeup-cluster-qualification.json"
MARKDOWN_PATH = ROOT / "docs/writeup/postfire-cluster-qualification-audit.md"
UV = "/home/martin/hddProjects/langywrap/uv"
DEPENDENCIES = ["--with", "numpy==2.5.4"]
COMMITTED = [doc["path"] for doc in json.loads(CONFIG_PATH.read_text())["documents"]] + [
    "reports/discovery-postfire-county-bootstrap.json", "reports/discovery-postfire.json",
    "config/discovery-postfire-county-bootstrap-preregistered.json",
    "reports/postfire-writeup-cluster-qualification.json",
    "docs/writeup/postfire-cluster-qualification-audit.md"]


def config() -> dict:
    return audit.load_config(CONFIG_PATH)


def report(tamper: str | None = None) -> dict:
    return audit.build_report(config(), tamper=tamper)


class BaselineTests(unittest.TestCase):
    def test_the_committed_report_passes_every_gate(self) -> None:
        result = report()
        self.assertTrue(result["gates"]["all_passed"], result["gates"])
        self.assertEqual(result["summary"]["unqualified_inferential_statements"], 0)

    def test_both_writeups_are_audited_and_neither_is_empty(self) -> None:
        result = report()
        self.assertEqual([entry["id"] for entry in result["documents"]], ["discovery", "final"])
        for entry in result["documents"]:
            with self.subTest(document=entry["id"]):
                self.assertGreater(entry["block_chars"], 4000)
                self.assertGreater(entry["claim_scope_chars"], 1000)

    def test_the_classifier_is_not_vacuous(self) -> None:
        cfg = config()
        minimum = cfg["classification"]["minimum_inferential_statements_per_document"]
        for entry in report()["documents"]:
            with self.subTest(document=entry["id"]):
                self.assertGreaterEqual(entry["inferential_statements"], minimum)
                self.assertGreaterEqual(entry["statements_examined"], 30)

    def test_the_qualifying_sentence_that_distinguishes_the_two_designs_is_recorded(self) -> None:
        for entry in report()["documents"]:
            with self.subTest(document=entry["id"]):
                sentence = entry["design_contrast_sentence"]
                self.assertIsNotNone(sentence)
                self.assertIn("not interchangeable", sentence)
                self.assertIn("understates county-level dependence", sentence)

    def test_first_mention_of_the_difference_carries_both_intervals_and_the_verdict(self) -> None:
        result = report()
        literals = result["frozen_inputs"]["derived_literals"]
        for entry in result["documents"]:
            with self.subTest(document=entry["id"]):
                self.assertTrue(entry["first_mention_carries_cluster_interval_and_verdict"])
                self.assertIn(literals["tract_bootstrap_interval_percentage_points"],
                              entry["first_mention"])
                self.assertIn(literals["county_cluster_interval_percentage_points"],
                              entry["first_mention"])
                self.assertIn("exploratory", entry["first_mention"])

    def test_only_the_two_frozen_intervals_appear_in_the_finding_blocks(self) -> None:
        result = report()
        literals = result["frozen_inputs"]["derived_literals"]
        allowed = {literals["tract_bootstrap_interval_percentage_points"],
                   literals["county_cluster_interval_percentage_points"]}
        for entry in result["documents"]:
            with self.subTest(document=entry["id"]):
                self.assertTrue(entry["interval_literals_found"])
                self.assertEqual(set(entry["interval_literals_found"]) - allowed, set())


class FrozenAgreementTests(unittest.TestCase):
    def test_derived_literals_are_recomputed_from_the_frozen_reports(self) -> None:
        cluster = json.loads((ROOT / "reports/discovery-postfire-county-bootstrap.json")
                             .read_text(encoding="utf-8"))
        literals = report()["frozen_inputs"]["derived_literals"]
        self.assertEqual(
            literals["point_difference_percentage_points"],
            f"{cluster['point_estimate']['difference_percentage_points']:+.2f}")
        low, high = cluster["robustness"]["cluster_95_percentile_interval_percentage_points"]
        self.assertEqual(literals["county_cluster_interval_percentage_points"],
                         f"[{low:+.2f}, {high:+.2f}]")
        t_low, t_high = cluster["published_tract_level_reference"]["frozen_report_interval"]
        self.assertEqual(literals["tract_bootstrap_interval_percentage_points"],
                         f"[{t_low * 100:+.2f}, {t_high * 100:+.2f}]")
        self.assertEqual(
            literals["positive_difference_fraction_percent"],
            f"{cluster['robustness']['positive_difference_fraction_of_valid_percentage_points']:.2f}%")

    def test_every_document_agrees_with_every_frozen_statistic(self) -> None:
        for entry in report()["documents"]:
            with self.subTest(document=entry["id"]):
                for name, stat in entry["frozen_statistics"].items():
                    self.assertTrue(stat["captures_agree"], f"{name} disagrees")
                    self.assertGreaterEqual(stat["occurrences"], stat["min_occurrences"])

    def test_numbers_of_keeps_the_sign_and_ignores_the_percent_sign(self) -> None:
        self.assertEqual(audit.numbers_of("95.99%"), audit.numbers_of("95.99"))
        self.assertNotEqual(audit.numbers_of("[+0.55, +20.89]"),
                            audit.numbers_of("[-0.55, +20.89]"))

    def test_frozen_report_hashes_are_pinned_and_verified(self) -> None:
        result = report()
        for name, entry in result["frozen_inputs"]["integrity"].items():
            with self.subTest(artifact=name):
                self.assertTrue(entry["sha256_matches_pin"])
        self.assertTrue(result["gates"]["frozen_report_hashes_match_pin"])

    def test_a_pinned_hash_mismatch_is_refused_before_any_prose_check(self) -> None:
        cfg = config()
        cfg["frozen_reports"]["county_bootstrap"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError) as caught:
            audit.build_report(cfg)
        self.assertIn("pinned SHA-256", str(caught.exception))


class ClassifierTests(unittest.TestCase):
    def test_an_unqualified_inferential_statement_is_caught(self) -> None:
        cfg = config()
        statements = [{"paragraph": 0, "index": 0,
                       "text": "The high-burn minus unburned difference is +9.55 percentage points, "
                               "with a 95% interval of [+0.55, +20.89] percentage points."}]
        classified = audit.classify(statements, cfg)
        self.assertTrue(classified[0]["is_inferential_statement"])
        self.assertTrue(classified[0]["unqualified"])

    def test_the_same_statement_with_the_clustered_result_is_qualified(self) -> None:
        cfg = config()
        statements = [{"paragraph": 0, "index": 0,
                       "text": "The high-burn minus unburned difference is +9.55 percentage points, "
                               "and its 95% percentile interval is [-1.65, +16.67] once whole "
                               "counties are resampled, so it is exploratory."}]
        classified = audit.classify(statements, cfg)
        self.assertTrue(classified[0]["is_inferential_statement"])
        self.assertFalse(classified[0]["unqualified"])
        self.assertTrue(classified[0]["cluster_qualification"])

    def test_descriptive_prose_about_the_cutoff_is_exempt(self) -> None:
        cfg = config()
        statements = [{"paragraph": 0, "index": 0,
                       "text": "The table below is a descriptive cutoff, not an organiser score."}]
        self.assertFalse(audit.classify(statements, cfg)[0]["is_inferential_statement"])

    def test_reproducibility_machinery_prose_is_outside_the_claim_scope(self) -> None:
        cfg = config()
        doc = cfg["documents"][0]
        text = (ROOT / doc["path"]).read_text(encoding="utf-8")
        block = audit.finding_block(text, doc)
        claims = audit.claim_scope(block, doc)
        self.assertNotIn("the audit fails closed", claims)
        self.assertIn("the audit fails closed", block)

    def test_is_affirmative_separates_a_disclosure_from_a_claim(self) -> None:
        markers = config()["language"]["negation_markers"]
        disclosure = ("it does **not** claim that the organiser used a 2018 microsoft release to "
                      "construct its hidden target")
        self.assertFalse(audit.is_affirmative(disclosure, "the organiser used", markers))
        claim = "the fire caused the building-count gap, which proves the reference layer is stale"
        self.assertTrue(audit.is_affirmative(claim, "caused the", markers))
        distant_negation = "no county was dropped; the fire caused the gap"
        self.assertTrue(audit.is_affirmative(distant_negation, "caused the", markers))

    def test_a_missing_anchor_fails_loudly(self) -> None:
        doc = copy.deepcopy(config()["documents"][0])
        doc["block_start_anchor"] = "this heading does not exist"
        with self.assertRaises(ValueError):
            audit.finding_block("anything", doc)


class TamperTests(unittest.TestCase):
    EXPECTED = {
        "strip_cluster_interval": "frozen_interval_literals_present",
        "strip_exploratory_verdict": "exploratory_verdict_near_finding",
        "unqualify_first_mention": "unqualified_inferential_statements_zero",
        "perturb_cluster_bound": "no_unfrozen_interval_literal",
        "drop_design_contrast": "tract_and_cluster_designs_distinguished",
        "generated_block_diverges": "generated_writeup_matches_source_block",
        "cutoff_retuned": "preregistered_design_not_drifted",
        "seed_or_replicates_changed": "preregistered_design_not_drifted",
        "causal_claim": "no_causal_or_hidden_target_claim",
    }

    def test_every_preregistered_negative_control_fires_its_expected_gate(self) -> None:
        declared = {case["id"]: case["expect_gate"] for case in config()["negative_controls"]}
        self.assertEqual(declared, self.EXPECTED)
        for tamper, expected_gate in self.EXPECTED.items():
            with self.subTest(tamper=tamper):
                result = report(tamper)
                self.assertFalse(result["gates"]["all_passed"])
                failed = [gate for gate, value in result["gates"].items() if not value]
                self.assertIn(expected_gate, failed)
                self.assertFalse(result["gates"][expected_gate])

    def test_an_unknown_tamper_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            report("make_it_pass")

    def test_a_tamper_run_never_rewrites_a_committed_file(self) -> None:
        before = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                  for path in COMMITTED}
        for tamper in self.EXPECTED:
            audit.build_report(config(), tamper=tamper)
        after = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                 for path in COMMITTED}
        self.assertEqual(before, after)

    def test_the_design_drift_gate_reads_the_frozen_design_not_the_prose(self) -> None:
        result = report()
        drift = result["design_drift"]
        self.assertEqual(drift["cutoff_used"], 0.8)
        self.assertEqual(drift["cluster_replicates"], 50000)
        self.assertEqual(drift["cluster_seed"], 20261025)
        self.assertEqual(drift["verdict"], "exploratory")
        self.assertFalse(drift["quantitative_claim_retained"])


class DeterminismAndSafetyTests(unittest.TestCase):
    def test_two_runs_are_identical(self) -> None:
        first = audit.canonical_bytes(report())
        second = audit.canonical_bytes(report())
        self.assertEqual(first, second)

    def test_the_committed_report_is_check_only_clean(self) -> None:
        completed = subprocess.run(
            [UV, "run", "--quiet", *DEPENDENCIES, "python",
             "scripts/audit_postfire_writeup_cluster_qualification.py", "--check-only"],
            cwd=ROOT, capture_output=True, text=True, timeout=900,
            env={**os.environ})
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("unchanged", completed.stdout)

    def test_the_committed_markdown_is_generated_from_the_report(self) -> None:
        rendered = audit.render_markdown(report())
        self.assertEqual(rendered, MARKDOWN_PATH.read_text(encoding="utf-8"))

    def test_no_wall_clock_key_and_no_nan_in_the_committed_products(self) -> None:
        document = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(audit.wall_clock_hits(
            {"documents": document["documents"], "design_drift": document["design_drift"]}), [])
        self.assertNotIn("NaN", json.dumps(document))
        self.assertTrue(document["gates"]["report_is_strict_json"])
        self.assertTrue(document["gates"]["report_has_no_temporal_field"])

    def test_the_audit_opens_only_the_allowlisted_files(self) -> None:
        result = report()
        access = result["access"]
        self.assertEqual(access["unexpected_opened_paths"], [])
        self.assertEqual(access["forbidden_opened_paths"], [])
        self.assertEqual(set(access["opened_paths"]), set(access["allowlist"]))
        self.assertTrue(result["gates"]["only_allowlisted_files_opened"])
        self.assertTrue(result["gates"]["no_forbidden_path_opened"])

    def test_the_value_scan_is_leaf_wise_so_measurements_cannot_self_hit(self) -> None:
        cfg = config()
        self.assertEqual(audit.value_scan({"a": 0.0911234}, cfg), [])
        self.assertTrue(audit.value_scan({"a": {"b": [0.0911]}}, cfg))
        self.assertTrue(audit.value_scan({"a": "0.3288136427728255"}, cfg))

    def test_the_prohibitions_are_recorded_and_no_upload_path_exists(self) -> None:
        cfg = config()
        self.assertEqual(cfg["prohibitions"]["uploads_performed"], 0)
        self.assertTrue(cfg["prohibitions"]["no_reference_layers"])
        self.assertTrue(cfg["prohibitions"]["no_labels"])
        self.assertTrue(cfg["prohibitions"]["no_uploads"])
        self.assertTrue(cfg["prohibitions"]["no_preregistered_design_change_after_results"])
        source = (ROOT / "scripts/audit_postfire_writeup_cluster_qualification.py").read_text()
        for marker in ("requests.", "urllib", "http", "zindi_submit", "post("):
            self.assertNotIn(marker, source)

    def test_check_only_detects_a_tampered_committed_report(self) -> None:
        committed = REPORT_PATH.read_text(encoding="utf-8")
        document = json.loads(committed)
        document["gates"]["unqualified_inferential_statements_zero"] = False
        with tempfile.TemporaryDirectory() as folder:
            mutated = Path(folder) / "report.json"
            mutated.write_text(json.dumps(document), encoding="utf-8")
            completed = subprocess.run(
                [UV, "run", "--quiet", *DEPENDENCIES, "python",
                 "scripts/audit_postfire_writeup_cluster_qualification.py", "--check-only",
                 "--out", str(mutated)],
                cwd=ROOT, capture_output=True, text=True, timeout=900)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("CHANGED", completed.stdout)
        self.assertEqual(REPORT_PATH.read_text(encoding="utf-8"), committed)

    def test_the_cli_tamper_mode_exits_non_zero(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            completed = subprocess.run(
                [UV, "run", "--quiet", *DEPENDENCIES, "python",
                 "scripts/audit_postfire_writeup_cluster_qualification.py",
                 "--tamper", "strip_exploratory_verdict",
                 "--out", f"{folder}/report.json", "--markdown", f"{folder}/audit.md"],
                cwd=ROOT, capture_output=True, text=True, timeout=900)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("FAIL", completed.stdout)


if __name__ == "__main__":
    unittest.main()
