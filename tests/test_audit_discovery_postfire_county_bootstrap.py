"""Tests for the county-cluster bootstrap recheck of the post-fire discovery finding.

Row: bias-discovery-postfire-county-bootstrap. The row is an evidence-robustness screen: the
published high-burn versus unburned difference was bootstrapped by resampling tracts independently,
which understates county-level dependence. These tests pin the preregistered design (whole-county
resampling, all tract records preserved, 50,000 seeded replicates), the honesty controls (no county
dropped, cutoff never retuned, no forbidden source), and the fact that the writeup states whatever
verdict the preregistered criteria produced - here, exploratory.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UV = "/home/martin/hddProjects/langywrap/uv"
SCRIPT = ROOT / "scripts/audit_discovery_postfire_county_bootstrap.py"
CONFIG_PATH = ROOT / "config/discovery-postfire-county-bootstrap-preregistered.json"
REPORT_PATH = ROOT / "reports/discovery-postfire-county-bootstrap.json"
FROZEN_PATH = ROOT / "reports/discovery-postfire.json"
CONFIG = json.loads(CONFIG_PATH.read_text())
REPORT = json.loads(REPORT_PATH.read_text())
FROZEN = json.loads(FROZEN_PATH.read_text())
WORKSPACE = os.environ.get("COMPETITION_WORKSPACE")


def load_module():
    spec = importlib.util.spec_from_file_location("postfire_bootstrap", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_script(args: list[str]) -> subprocess.CompletedProcess:
    command = [UV, "run", "--quiet", "--with", "numpy==2.5.4", "--with", "pyarrow==26.0.0",
               "python", str(SCRIPT), *args]
    return subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=900,
                          check=False, env=dict(os.environ))


def payload_of(stdout: str) -> dict:
    return json.JSONDecoder().raw_decode(stdout)[0]


module = load_module()


class IntegrityTests(unittest.TestCase):
    def test_gates_all_pass_and_nothing_failed(self) -> None:
        self.assertEqual(REPORT["summary"]["failed_gates"], [])
        self.assertTrue(REPORT["summary"]["gates_all_passed"], REPORT["summary"]["failed_gates"])
        self.assertTrue(all(value is True for value in REPORT["gates"].values()))

    def test_preregistration_is_carried_and_hash_pinned(self) -> None:
        pre = REPORT["preregistration"]
        self.assertEqual(pre["config_sha256"], sha256(CONFIG_PATH))
        self.assertEqual(pre["backlog_item"], "bias-discovery-postfire-county-bootstrap")
        self.assertEqual(pre["primary_design"]["replicates"], 50_000)
        self.assertEqual(pre["primary_design"]["seed"], 20261025)
        self.assertTrue(pre["design_substitution_after_results_forbidden"])
        self.assertEqual(CONFIG["frozen_definitions"]["cutoff_values_permitted"], [0.8])

    def test_inputs_are_hash_pinned_and_sample_scores_were_never_read(self) -> None:
        inputs = REPORT["inputs"]
        self.assertEqual(inputs["provided_strata"]["sha256"],
                         CONFIG["inputs"]["provided_strata"]["sha256"])
        self.assertEqual(inputs["provided_overture_feature_cache"]["sha256"],
                         CONFIG["inputs"]["provided_overture_feature_cache"]["sha256"])
        self.assertEqual(inputs["provided_strata"]["read_columns"],
                         list(CONFIG["strata_columns_read"]))
        self.assertEqual(inputs["provided_overture_feature_cache"]["read_columns"],
                         ["GEOID", "overture_buildings"])
        self.assertEqual(inputs["official_sample"]["read_columns"], ["GEOID"])
        self.assertIs(inputs["official_sample"]["score_placeholders_read"], False)
        self.assertEqual(inputs["external_public_data_used"], [])
        self.assertIs(inputs["frozen_report"]["matches_pin"], True)

    def test_derived_feature_cache_regeneration_is_disclosed_not_hidden(self) -> None:
        equivalence = REPORT["derived_input_content_equivalence"]
        self.assertIs(equivalence["butte_below_cutoff_sets_match"], True)
        self.assertIs(equivalence["burn_class_table_matches_frozen_report"], True)
        self.assertIs(equivalence["point_difference_matches_frozen_report"], True)
        self.assertTrue(equivalence["disclosure"])
        self.assertNotEqual(CONFIG["inputs"]["provided_overture_feature_cache"]["sha256"],
                            equivalence["publication_time_sha256"])
        self.assertTrue(REPORT["gates"]["derived_input_regeneration_disclosed"])

    def test_population_and_exclusions_account_for_every_tract_once(self) -> None:
        population = REPORT["analysis_population"]
        exclusions = population["exclusions"]
        self.assertEqual(exclusions["official_rows"], 591)
        self.assertEqual(exclusions["included_in_bins"], 577)
        self.assertEqual(exclusions["over_100"], 14)
        self.assertEqual(exclusions["negative"], 0)
        self.assertEqual(exclusions["missing"], 0)
        self.assertEqual(exclusions["official_rows"],
                         exclusions["included_in_bins"] + exclusions["over_100"]
                         + exclusions["negative"] + exclusions["missing"])
        self.assertEqual([row["tracts"] for row in population["burn_class_table"]],
                         [449, 28, 32, 32, 36])
        self.assertEqual([row["below_cutoff_n"] for row in population["burn_class_table"]],
                         [7, 0, 0, 0, 4])
        frozen = {row["class"]: row["n"] for row in FROZEN["northern_ca_burn_class_table"]}
        self.assertEqual({row["class"].replace("-", "\u2013"): row["tracts"]
                          for row in population["burn_class_table"]}.get("0% (unburned)"),
                         frozen["0% (unburned)"])


class PointEstimateTests(unittest.TestCase):
    def test_point_estimate_reproduces_the_frozen_finding_exactly(self) -> None:
        point = REPORT["point_estimate"]
        self.assertAlmostEqual(point["difference_high_burn_minus_unburned"],
                               FROZEN["high_burn_vs_unburned_below_0_8"]
                               ["difference_high_burn_minus_unburned"], delta=0.0)
        self.assertLessEqual(point["absolute_deviation_from_frozen_report"], 1e-12)
        self.assertLessEqual(point["exact_rational_agreement"], 1e-15)
        expected = str(Fraction(4, 36) - Fraction(7, 449))
        self.assertEqual(point["exact_rational_difference"], expected)
        self.assertAlmostEqual(float(Fraction(expected)),
                               point["difference_high_burn_minus_unburned"], delta=1e-15)

    def test_focal_group_shares_match_the_frozen_table(self) -> None:
        groups = REPORT["analysis_population"]["focal_groups"]
        self.assertEqual(groups["high_burn"]["tracts"], 36)
        self.assertEqual(groups["high_burn"]["below_cutoff_n"], 4)
        self.assertEqual(groups["unburned"]["tracts"], 449)
        self.assertEqual(groups["unburned"]["below_cutoff_n"], 7)
        self.assertAlmostEqual(groups["high_burn"]["share_below_cutoff"], 4 / 36, delta=1e-15)
        self.assertAlmostEqual(groups["unburned"]["share_below_cutoff"], 7 / 449, delta=1e-15)


class ClusterDesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cluster = REPORT["county_cluster_bootstrap"]

    def test_design_is_the_preregistered_county_resample(self) -> None:
        self.assertEqual(self.cluster["replicates"], 50_000)
        self.assertEqual(self.cluster["seed"], 20261025)
        self.assertEqual(self.cluster["cluster_count"], 23)
        self.assertIs(self.cluster["cluster_count_equals_county_count"], True)
        self.assertEqual(self.cluster["analysis_population_covered_by_clusters"], 577)
        self.assertEqual(REPORT["robustness"]["criteria_thresholds"]
                         ["minimum_valid_replicate_fraction"], 0.95)

    def test_cluster_pool_covers_the_whole_analysis_population(self) -> None:
        self.assertEqual(self.cluster["expected_analysis_population"], 577)
        self.assertIs(self.cluster["cluster_population_matches"], True)

    def test_clusters_are_whole_counties_with_every_tract_record(self) -> None:
        sizes = self.cluster["cluster_sizes"]
        self.assertEqual(len(sizes), 23)
        self.assertEqual(sum(row["tracts_all_burn_classes"] for row in sizes), 577)
        self.assertEqual(sum(row["high_burn_tracts"] for row in sizes), 36)
        self.assertEqual(sum(row["unburned_tracts"] for row in sizes), 449)
        for row in sizes:
            self.assertGreaterEqual(row["tracts_all_burn_classes"],
                                    row["high_burn_tracts"] + row["unburned_tracts"])
        for row in self.cluster["tract_preservation_checks"]:
            self.assertIs(row["tracts_all_classes_matches_matrix_product"], True)
            self.assertIs(row["focal_tracts_match"], True)
            self.assertGreater(row["non_focal_tracts_retained"], 0)

    def test_no_county_is_dropped_and_the_pool_is_the_full_county_set(self) -> None:
        self.assertEqual(sorted(row["county"] for row in self.cluster["cluster_sizes"]),
                         sorted(row["county"] for row in
                                REPORT["secondary_leave_one_county_out"]["rows"]))
        self.assertEqual(len(self.cluster["cluster_sizes"]),
                         REPORT["analysis_population"]["counties"])
        self.assertTrue(REPORT["gates"]["no_county_dropped"])

    def test_interval_and_fractions_are_reported_in_fraction_and_percentage_points(self) -> None:
        robust = REPORT["robustness"]
        interval = robust["cluster_95_percentile_interval"]
        percentage = robust["cluster_95_percentile_interval_percentage_points"]
        self.assertEqual(len(interval), 2)
        for fraction, points in zip(interval, percentage):
            self.assertAlmostEqual(fraction * 100.0, points, delta=1e-12)
        self.assertAlmostEqual(percentage[0], -1.65, delta=0.005)
        self.assertAlmostEqual(percentage[1], 16.67, delta=0.005)
        self.assertEqual(self.cluster["confidence_level_percent"], 95.0)
        self.assertEqual(self.cluster["percentile_bounds"], [2.5, 97.5])

    def test_criteria_are_evaluated_against_the_preregistered_thresholds(self) -> None:
        criteria = REPORT["robustness"]["criteria"]
        self.assertEqual(sorted(criteria),
                         ["cluster_ci_lower_bound_positive",
                          "positive_difference_fraction_at_least_preregistered",
                          "valid_replicate_fraction_at_least_preregistered"])
        self.assertIs(criteria["valid_replicate_fraction_at_least_preregistered"], True)
        self.assertEqual(self.cluster["valid_replicate_fraction"], 1.0)
        self.assertEqual(self.cluster["valid_replicates"], 50_000)
        self.assertEqual(self.cluster["invalid_replicates"], 0)

    def test_the_measured_verdict_is_exploratory_and_not_spun(self) -> None:
        """The honest outcome: the interval crosses zero, so the quantitative claim is exploratory."""
        robust = REPORT["robustness"]
        self.assertLess(robust["cluster_95_percentile_interval"][0], 0.0)
        self.assertIs(robust["criteria"]["cluster_ci_lower_bound_positive"], False)
        self.assertIs(robust["criteria"]["positive_difference_fraction_at_least_preregistered"], True)
        self.assertGreaterEqual(robust["positive_difference_fraction_of_valid"], 0.95)
        self.assertIs(robust["quantitative_claim_retained"], False)
        self.assertEqual(robust["verdict"], "exploratory")
        self.assertEqual(REPORT["summary"]["verdict"], "exploratory")

    def test_secondary_designs_cannot_change_the_verdict(self) -> None:
        secondary = REPORT["secondary_within_group_county_cluster"]
        self.assertIs(secondary["may_change_verdict"], False)
        self.assertEqual(secondary["design"], "within_group_county_cluster")
        self.assertEqual(secondary["high_burn"]["counties"], 15)
        self.assertEqual(secondary["unburned"]["counties"], 17)
        loo = REPORT["secondary_leave_one_county_out"]
        self.assertIs(loo["every_county_dropped_exactly_once"], True)
        self.assertEqual(len(loo["rows"]), 23)
        self.assertGreater(loo["minimum_difference"], 0.0)
        self.assertEqual(loo["minimum_county"], "033")
        self.assertEqual(loo["maximum_county"], "067")
        reference = REPORT["published_tract_level_reference"]
        self.assertIs(reference["reproduces_frozen_interval_exactly"], True)
        self.assertEqual(reference["bootstrap_95_percentile_ci"],
                         FROZEN["high_burn_vs_unburned_below_0_8"]["bootstrap_95_percentile_ci"])
        self.assertEqual(reference["seed"], 20261024)


class ProhibitionTests(unittest.TestCase):
    def test_cutoff_was_never_retuned(self) -> None:
        self.assertEqual(REPORT["analysis_population"]["cutoff_used"], 0.8)
        self.assertEqual(REPORT["analysis_population"]["cutoff_values_permitted"], [0.8])
        self.assertTrue(REPORT["gates"]["cutoff_not_tuned"])
        self.assertIs(REPORT["non_influence"]["cutoff_retuned"], False)

    def test_no_forbidden_source_and_no_leaderboard_value_appears(self) -> None:
        scan = REPORT["forbidden_source_scan"]
        self.assertEqual(scan["forbidden_opened_hits"], [])
        self.assertEqual(scan["forbidden_numeric_literal_hits"], [])
        self.assertEqual(scan["sample_files_opened_by_this_script"], [])
        self.assertEqual(scan["delegated_sample_reader"]["declared_columns_read"], ["GEOID"])
        self.assertIs(scan["delegated_sample_reader"]["score_placeholders_read"], False)
        self.assertTrue(REPORT["gates"]["no_forbidden_source_read"])
        for token in CONFIG["forbidden_source_scan"]["prohibited_numeric_literals"]:
            self.assertNotIn(token, json.dumps(REPORT), token)

    def test_no_external_network_or_causal_language(self) -> None:
        source = SCRIPT.read_text()
        for token in ("import requests", "import urllib", "import http", "httpx",
                      "socket.", "subprocess.run([\"curl"):
            self.assertNotIn(token, source, token)
        uri_tokens = [token for token in
                      CONFIG["forbidden_source_scan"]["prohibited_uri_tokens"]
                      if "://" in token]
        opened = module.opened_literals(SCRIPT)
        self.assertEqual([literal for literal in opened
                          if any(token in literal for token in uri_tokens)], [])
        interpretation = REPORT["interpretation"].lower()
        for pattern in CONFIG["language"]["forbidden_patterns"]:
            self.assertNotIn(pattern, interpretation, pattern)
        for hedge in CONFIG["language"]["required_hedges"]:
            self.assertIn(hedge, interpretation, hedge)

    def test_no_model_chain_script_reads_the_snapshot(self) -> None:
        readers = []
        for path in sorted((ROOT / "scripts").glob("*.py")):
            if path.name == SCRIPT.name:
                continue
            if "discovery-postfire-county-bootstrap" in path.read_text(encoding="utf-8"):
                readers.append(path.name)
        self.assertEqual(readers, [])
        self.assertIs(REPORT["non_influence"]["model_changed"], False)
        self.assertIs(REPORT["non_influence"]["upload_performed"], False)

    def test_report_serialises_no_wall_clock_value(self) -> None:
        self.assertTrue(REPORT["gates"]["report_serialises_no_unexpected_wall_clock"])
        self.assertEqual(module.wall_clock_hits({key: value for key, value in REPORT.items()
                                                 if key != "preregistration"}), [])


class WriteupQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        document = (ROOT / "docs/writeup/bias-discovery.md").read_text(encoding="utf-8")
        start = document.find("## Evidence")
        end = document.find("## Section 2", start + 1)
        self.block = document[start:end]

    def test_the_finding_is_qualified_in_the_evidence_block(self) -> None:
        qualification = REPORT["writeup_qualification"]
        self.assertEqual(qualification["verdict"], "exploratory")
        self.assertEqual(qualification["missing_fragments"], [])
        self.assertIn("county-cluster bootstrap", self.block)
        self.assertIn("exploratory", self.block)
        self.assertIn("-1.65", self.block)
        self.assertIn("+16.67", self.block)
        self.assertTrue(REPORT["gates"]["writeup_qualification_matches_verdict"])

    def test_the_qualification_keeps_the_published_result_rather_than_deleting_it(self) -> None:
        self.assertIn("+9.55 percentage points", self.block)
        self.assertIn("[+0.55, +20.89]", self.block)
        self.assertIn("50,000", self.block)
        self.assertIn("valid-replicate fraction 1.0", self.block)

    def test_the_reproduction_command_is_recorded_in_the_writeup(self) -> None:
        document = (ROOT / "docs/writeup/bias-discovery.md").read_text(encoding="utf-8")
        repro_block = document[document.find("## Reproducibility"):]
        self.assertIn("python scripts/audit_discovery_postfire_county_bootstrap.py", repro_block)
        self.assertIn("reports/discovery-postfire-county-bootstrap.json", repro_block)


class DeterminismTests(unittest.TestCase):
    @unittest.skipUnless(WORKSPACE, "COMPETITION_WORKSPACE is not set")
    def test_two_runs_are_byte_identical_and_match_the_committed_report(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            digests = []
            for index in (1, 2):
                completed = run_script(["--workspace", WORKSPACE, "--out", f"{folder}/{index}.json"])
                self.assertEqual(completed.returncode, 0, completed.stdout[-400:])
                digests.append(sha256(Path(folder) / f"{index}.json"))
            self.assertEqual(digests[0], digests[1])
            self.assertEqual(digests[0], sha256(REPORT_PATH))

    @unittest.skipUnless(WORKSPACE, "COMPETITION_WORKSPACE is not set")
    def test_check_only_accepts_the_committed_report(self) -> None:
        completed = run_script(["--workspace", WORKSPACE, "--check-only"])
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("unchanged", completed.stdout)


class NegativeControlTests(unittest.TestCase):
    CASES = {
        "drop_inconvenient_county": "no_county_dropped",
        "tract_resample_disguised_as_cluster": "clusters_are_whole_counties",
        "cutoff_retuned": "cutoff_not_tuned",
        "seed_or_replicates_changed": "replicates_and_seed_as_preregistered",
        "point_estimate_perturbed": "point_estimate_matches_frozen_report",
    }

    def setUp(self) -> None:
        if not WORKSPACE:
            self.skipTest("COMPETITION_WORKSPACE is not set")
        self.committed_before = sha256(REPORT_PATH)

    def tearDown(self) -> None:
        self.assertEqual(sha256(REPORT_PATH), self.committed_before,
                         "a negative control wrote into the repository")

    def test_every_negative_control_fires_the_expected_gate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            for case, gate in self.CASES.items():
                completed = run_script(["--workspace", WORKSPACE, "--tamper", case,
                                        "--out", f"{folder}/{case}.json"])
                self.assertNotEqual(completed.returncode, 0, case)
                payload = payload_of(completed.stdout)
                self.assertIn(gate, payload["failed_gates"],
                              f"{case}: {payload['failed_gates']}")

    def test_unmutated_control_passes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            completed = run_script(["--workspace", WORKSPACE, "--out", f"{folder}/clean.json"])
            self.assertEqual(completed.returncode, 0, completed.stdout[-400:])
            self.assertTrue(payload_of(completed.stdout)["summary"]["gates_all_passed"])

    def test_unknown_tamper_case_is_rejected(self) -> None:
        completed = run_script(["--workspace", WORKSPACE, "--tamper", "make_it_survive"])
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid choice", completed.stderr)

    def test_every_declared_control_case_is_tested(self) -> None:
        declared = [case["case"] for case in CONFIG["negative_controls"]]
        self.assertEqual(sorted(set(declared) - {"unmutated_control"}), sorted(self.CASES))
        for case in CONFIG["negative_controls"]:
            if case["case"] == "unmutated_control":
                self.assertIsNone(case["expect_failed_gate"])
            else:
                self.assertIn(case["case"], self.CASES)
                self.assertEqual(case["expect_failed_gate"], self.CASES[case["case"]])


class WallClockAndHelperTests(unittest.TestCase):
    def test_wall_clock_hits_detects_iso_timestamps(self) -> None:
        self.assertEqual(module.wall_clock_hits({"ok": 1}), [])
        self.assertTrue(module.wall_clock_hits({"nested": {"when": "2026-10-25T05:00:00+00:00"}}))
        self.assertTrue(module.wall_clock_hits({"generated_at": "x"}))

    def test_delegated_reader_is_the_frozen_geoid_only_function(self) -> None:
        if not WORKSPACE:
            self.skipTest("COMPETITION_WORKSPACE is not set")
        reader, digest = module.delegated_sample_reader()
        self.assertEqual(digest, sha256(ROOT / CONFIG["forbidden_source_scan"]
                                        ["delegated_sample_reader"]))
        ids = reader(Path(WORKSPACE) / CONFIG["inputs"]["official_sample"]["path"])
        self.assertEqual(len(ids), 591)
        self.assertEqual(len(set(ids)), 591)
        self.assertEqual(REPORT["inputs"]["official_sample"]["geoid_sequence_sha256"],
                         hashlib.sha256(("\n".join(ids) + "\n").encode("utf-8")).hexdigest())
        # The frozen reader raises rather than returning a score column value.
        with self.assertRaises(ValueError):
            reader(ROOT / "config/discovery-postfire-county-bootstrap-preregistered.json")


if __name__ == "__main__":
    unittest.main()
