"""Tests for the external-lab plan, its study-exclusion policy and the readiness gate.

The lab is not built: these tests assert the design is frozen against the spec, that the exclusion
policy really rejects study and near-study candidates, that no reference-layer byte exists, that no model
or submission path can read a lab file, and that the report keeps the build unauthorised.
"""
from __future__ import annotations

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

from bias60 import lab_plan  # noqa: E402

MODULE = importlib.util.spec_from_file_location(
    "report_lab_readiness", ROOT / "scripts/report_lab_readiness.py")
readiness = importlib.util.module_from_spec(MODULE)
sys.modules["report_lab_readiness"] = readiness
MODULE.loader.exec_module(readiness)

CONFIG_PATH = ROOT / "config/bias60-lab-preregistered.json"
REPORT_PATH = ROOT / "reports/lab-readiness.json"
MARKDOWN_PATH = ROOT / "docs/decisions/bias60-lab-readiness.md"
WORKSPACE = Path(os.environ.get("COMPETITION_WORKSPACE", "") or ".").resolve()
UV = "/home/martin/hddProjects/langywrap/uv"
DEPENDENCIES = ["--with", "numpy==2.5.4", "--with", "pyarrow==26.0.0"]
COMMITTED = [CONFIG_PATH, ROOT / "src/bias60/lab_plan.py", ROOT / "scripts/report_lab_readiness.py",
             REPORT_PATH, MARKDOWN_PATH]
SPEC_PATH = Path("/home/martin/hddProjects/competitionsall/docs/research/bias-60-ideas-2026-10.md")


def config() -> dict:
    return lab_plan.load_plan(CONFIG_PATH)


def report(tamper: str | None = None, *, lab_root: Path | None = None,
           extra_scan_scripts: list[Path] | None = None) -> dict:
    # mirror main(): install the access recorder so every provided-data read is disclosed
    lab_plan.set_recorder(readiness.record)
    rules_path = WORKSPACE / "RULES.md"
    readiness.record(CONFIG_PATH)
    if rules_path.is_file():
        readiness.record(rules_path)
    return readiness.build_report(
        config(), workspace=WORKSPACE,
        lab_root=lab_root or (WORKSPACE / "lab"),
        rules_text=rules_path.read_text(encoding="utf-8") if rules_path.is_file() else None,
        extra_scan_scripts=extra_scan_scripts or [], tamper=tamper)


def require_workspace(case: unittest.TestCase) -> None:
    if not (WORKSPACE / "defined-flags-inputs").is_dir():
        case.skipTest("COMPETITION_WORKSPACE is not available")


class SpecPlanTests(unittest.TestCase):
    def test_the_spec_hash_pin_matches_the_frozen_memo(self) -> None:
        self.assertEqual(lab_plan.sha256_file(SPEC_PATH), config()["spec"]["sha256"])

    def test_the_pin_is_the_same_the_harness_uses(self) -> None:
        harness = json.loads((ROOT / "config/bias60-harness-preregistered.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(harness["spec"]["sha256"], config()["spec"]["sha256"])

    def test_section4_parses_every_subsection_the_plan_cites(self) -> None:
        body = lab_plan.section4(SPEC_PATH)
        self.assertEqual(len(body), 6)
        for prefix in ("4.1", "4.2", "4.3", "4.4", "4.5", "4.6"):
            self.assertTrue(any(heading.startswith(prefix) for heading in body), prefix)

    def test_group_counts_and_cv_units_agree_with_the_spec(self) -> None:
        cfg = config()
        body = lab_plan.section4(SPEC_PATH)
        groups = next(lab_plan.parse_groups(text) for heading, text in body.items()
                      if heading.startswith("4.2"))
        self.assertEqual([entry["cv_unit"] for entry in groups],
                         [entry["id"] for entry in cfg["groups"]])
        self.assertEqual([entry["approx_tracts"] for entry in groups],
                         [entry["approx_tracts"] for entry in cfg["groups"]])
        self.assertEqual(len(groups), 9)
        self.assertEqual(cfg["core_groups"], [entry["id"] for entry in cfg["groups"]][:6])

    def test_the_recipe_and_volume_table_are_read_from_the_spec(self) -> None:
        body = lab_plan.section4(SPEC_PATH)
        recipe = lab_plan.parse_recipe(next(text for heading, text in body.items()
                                            if heading.startswith("4.4")))
        self.assertEqual(recipe["projection"], "EPSG:5070")
        self.assertTrue(all(entry["declared"] and entry["uses_clip_mapping"]
                            for entry in recipe["components"].values()))
        self.assertTrue(recipe["road_ratio_band_declared"])
        self.assertTrue(recipe["hospital_check_declared"])
        volume = lab_plan.parse_volume_table(next(text for heading, text in body.items()
                                                 if heading.startswith("4.5")))
        self.assertGreaterEqual(len(volume), 7)
        self.assertIn("Total", volume[-1]["item"])

    def test_the_readiness_report_agrees_with_a_fresh_spec_parse(self) -> None:
        require_workspace(self)
        findings = report()["spec_cross_check"]
        self.assertTrue(findings["group_cv_units_match"])
        self.assertTrue(findings["approx_tract_counts_match"])
        self.assertEqual(findings["source_families_missing_from_register"], [])
        self.assertTrue(findings["structure_codes_covered"])


@unittest.skipUnless((WORKSPACE / "defined-flags-inputs").is_dir(), "workspace required")
class ExclusionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = config()
        self.points = readiness.synthetic_study_points()
        self.policy = lab_plan.policy_from_config(self.cfg, WORKSPACE, study_points=self.points)

    def test_every_official_and_template_geoid_is_excluded(self) -> None:
        official = lab_plan.official_scored_ids(WORKSPACE)
        template = lab_plan.template_geoids(WORKSPACE)
        self.assertEqual(len(official), 9379)
        self.assertEqual(len(template), 9379 + 415)
        self.assertTrue(set(official) <= self.policy.excluded_geoids)
        self.assertTrue(set(template) <= self.policy.excluded_geoids)
        self.assertEqual(self.policy.reject_reason(official[0], None, None),
                         "official_scored_or_template_geoid")

    def test_study_counties_are_enumerated_from_provided_data(self) -> None:
        counties = lab_plan.study_counties(WORKSPACE)
        self.assertEqual(sorted(counts := list(counties)), sorted(lab_plan.REGION_DIRECTORIES))
        for region, entry in counties.items():
            with self.subTest(region=region):
                self.assertGreater(entry["rows"], 0)
                self.assertTrue(entry["states"])
                self.assertTrue(entry["county_fips"])
        self.assertGreater(len(self.policy.study_county_fips), 100)

    def test_the_buffer_rejects_a_neighbour_and_accepts_a_distant_tract(self) -> None:
        x, y = self.points[0]
        self.assertEqual(self.policy.reject_reason("99000000001", "00000", (x + 1000.0, y)),
                         "within_neighbour_buffer_of_study_tract")
        self.assertEqual(self.policy.reject_reason("99000000002", "00000", (x + 5000.0, y)),
                         "within_neighbour_buffer_of_study_tract")
        self.assertIsNone(self.policy.reject_reason("99000000003", "00000", (x + 500000.0, y)))

    def test_the_county_rule_works_when_a_build_enables_it(self) -> None:
        study_county = sorted(self.policy.study_county_fips)[0]
        self.assertIsNone(self.policy.reject_reason("95000000000", study_county, None),
                          "county exclusion is off by default because the packages touch 298 counties")
        enabled = lab_plan.ExclusionPolicy(
            official_geoids=self.policy.official_geoids, template_geoids=self.policy.template_geoids,
            study_county_fips=self.policy.study_county_fips, study_states=self.policy.study_states,
            buffer_km=self.policy.buffer_km, study_points=list(self.policy.study_points),
            county_exclusion_enabled=True)
        self.assertEqual(enabled.reject_reason("95000000000", study_county, None), "study_county")

    def test_partition_separates_accepted_from_every_rejection_reason(self) -> None:
        x, y = self.points[0]
        official = sorted(self.policy.official_geoids)[0]
        study_county = sorted(self.policy.study_county_fips)[0]
        buckets = self.policy.partition([
            {"geoid": official, "county_fips": study_county, "centroid": (x, y)},
            {"geoid": "94000000001", "county_fips": "00000", "centroid": (x + 800.0, y)},
            {"geoid": "94000000002", "county_fips": "00000", "centroid": (x + 900000.0, y)},
        ])
        self.assertEqual(buckets["accepted"], ["94000000002"])
        self.assertIn(official, buckets["official_scored_or_template_geoid"])
        self.assertIn("94000000001", buckets["within_neighbour_buffer_of_study_tract"])

    def test_a_zero_buffer_would_admit_an_adjacent_tract_and_is_gated(self) -> None:
        cfg = config()
        cfg["exclusion_policy"]["centroid_buffer_km"] = 0.0
        policy = lab_plan.policy_from_config(cfg, WORKSPACE, study_points=self.points)
        x, y = self.points[0]
        self.assertIsNone(policy.reject_reason("93000000001", "00000", (x + 100.0, y)))
        result = report("buffer_removed")
        self.assertFalse(result["gates"][
            "neighbour_buffer_policy_rejects_study_and_adjacent_tracts"])


@unittest.skipUnless((WORKSPACE / "defined-flags-inputs").is_dir(), "workspace required")
class ReadinessGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = report()

    def test_every_readiness_gate_passes(self) -> None:
        self.assertTrue(self.result["gates"]["all_passed"], self.result["gates"])
        self.assertEqual(self.result["summary"]["lab_files_on_disk"], 0)

    def test_the_operator_decision_that_authorises_the_lab_is_quoted_from_the_spec(self) -> None:
        authorization = self.result["authorization"]
        self.assertEqual(authorization["status"], "authorized_by_operator_decision")
        self.assertIsNone(authorization["blocking_row"])
        self.assertEqual(authorization["operator_question_status"], "withdrawn_by_operator_decision")
        self.assertIn("901d5e3", authorization["decided_by"])
        self.assertEqual(authorization["decided_at_utc"], "2026-10-25T09:35Z")
        self.assertTrue(self.result["gates"]["operator_decision_present_verbatim_in_spec"])
        self.assertIn("No question goes to the organisers", authorization["decision_verbatim"])
        self.assertIn("Track B is allowed", authorization["decision_verbatim"])
        self.assertEqual(self.result["preregistration"]["spec_amendments"]["amended_spec_sha256"],
                         lab_plan.sha256_file(SPEC_PATH))

    def test_the_four_operator_conditions_each_name_an_enforcement_mechanism(self) -> None:
        conditions = self.result["authorization"]["operator_conditions"]
        self.assertEqual(len(conditions), 4)
        for entry in conditions:
            with self.subTest(condition=entry["condition"][:40]):
                self.assertTrue(entry["enforced"])
                self.assertGreater(len(entry["enforced_by"]), 20)
        self.assertTrue(self.result["gates"]["operator_conditions_enforced"])

    def test_only_the_weight_vector_may_cross_into_a_submission_artifact(self) -> None:
        controls = self.result["authorization"]["track_b_controls"]
        self.assertTrue(controls["only_the_weight_vector_may_cross"])
        self.assertTrue(controls["weight_vector_is_the_only_interface"])
        self.assertEqual(controls["candidate_manifest_must_quote"], "rules_gate.decision_verbatim")
        self.assertEqual(self.result["authorization"]["track_b_smuggles_lab_values"], [])
        for never in ("per-tract lab labels", "lab GEOIDs", "reference-layer measurements"):
            self.assertIn(never, controls["never_cross"])
        self.assertTrue(self.result["gates"]["track_b_crossing_surface_is_bounded"])

    def test_the_lab_must_be_disclosed_in_the_writeup(self) -> None:
        disclosure = self.result["authorization"]["writeup_disclosure"]
        self.assertTrue(disclosure["required"])
        self.assertIn("docs/writeup/methodology.md", disclosure["documents"])
        self.assertTrue(any("operator decision" in line for line in disclosure["must_state"]))
        self.assertTrue(self.result["gates"]["writeup_disclosure_required"])

    def test_the_track_a_ordering_survives_the_authorisation(self) -> None:
        blocker = self.result["authorization"]["second_blocker"]
        self.assertEqual(blocker["row"], "bias60-track-a-combiner")
        self.assertIn("pre-registered before any lab number", blocker["requirement"])
        self.assertIn("still binding on sequencing", blocker["status"])
        self.assertTrue(self.result["gates"]["external_use_recorded_as_authorized"])

    def test_the_build_itself_has_not_been_executed_this_turn(self) -> None:
        self.assertTrue(self.result["gates"]["build_not_executed_this_turn"])
        self.assertEqual(self.result["preregistration"]["prohibitions"]
                         ["reference_layer_bytes_downloaded_in_this_row"], 0)
        self.assertEqual(self.result["preregistration"]["prohibitions"]["uploads_performed"], 0)
        self.assertTrue(self.result["preregistration"]["prohibitions"]["no_download_this_turn"])
        self.assertEqual(self.result["summary"]["lab_files_on_disk"], 0)

    def test_the_rule_citations_appear_verbatim_in_the_saved_official_text(self) -> None:
        self.assertTrue(self.result["gates"]["rules_citations_are_verbatim"])
        for entry in self.result["authorization"]["citations"]:
            with self.subTest(citation=entry["citation"][:40]):
                self.assertTrue(entry["present_in_saved_rules_text"])
        joined = " ".join(self.result["authorization"]["citations"][i]["citation"]
                          for i in range(4))
        self.assertIn("equivalent copies obtained elsewhere", joined)
        self.assertIn("Best Bias Discovery prize", joined)

    def test_the_lab_cannot_reach_a_submission_artifact(self) -> None:
        reaches = self.result["authorization"]["lab_data_may_not_reach"]
        for path in ("reports/candidates.json", "reports/final-two.json", "scripts/submit_zindi.py"):
            self.assertIn(path, reaches)

    def test_the_source_register_is_complete_and_unretrieved(self) -> None:
        register = self.result["source_register"]
        self.assertEqual(register["problems"], [])
        self.assertGreaterEqual(register["entries"], 10)
        roles = {entry["role"] for entry in self.result["sources"]}
        for required in ("lab_tract_polygons", "observed_data", "reference_roads",
                         "reference_buildings", "reference_facilities", "reference_establishments"):
            self.assertIn(required, roles)
        for entry in self.result["sources"]:
            with self.subTest(source=entry["id"]):
                self.assertIsNone(entry["retrieval_date_utc"])
                self.assertIsNone(entry["sha256"])
                self.assertFalse(entry["downloaded"])

    def test_access_is_fully_disclosed_and_holds_no_reference_layer(self) -> None:
        access = self.result["access"]
        opened = access["opened_paths"]
        self.assertIn("WORKSPACE:RULES.md", opened)
        self.assertIn("WORKSPACE:SampleSubmission.csv", opened)
        self.assertEqual(len([entry for entry in opened
                              if entry.endswith("strata-tract-table.parquet")]), 4)
        self.assertIn("ORCHESTRATOR:docs/research/bias-60-ideas-2026-10.md", opened)
        joined = " ".join(opened).lower()
        for token in self.result["firewall"]["tokens"]:
            with self.subTest(token=token):
                self.assertNotIn(token, joined)
        self.assertEqual(access["provided_data_reads"]["strata_columns"],
                         ["GEOID", "STATEFP", "COUNTYFP"])
        self.assertEqual(access["provided_data_reads"]["official_geoid_reader"],
                         "scripts/audit_v2_component_masks.py::official_scored_ids")

    def test_placement_and_the_operator_question_match_the_spec(self) -> None:
        placement = self.result["placement"]
        spec = placement["as_parsed_from_spec"]
        self.assertTrue(spec["writeup_declared_always_allowed"])
        self.assertEqual(spec["scored_submission_default"], "Track A")
        self.assertTrue(spec["track_b_requires_ruling"])
        self.assertEqual(spec["policy_document"], "docs/submission-policy.md")
        self.assertEqual(placement["spec_combined_question"], spec["combined_question"])
        question = " ".join(self.result["authorization"]["operator_question"]
                            .replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
                            .split())
        self.assertIn(spec["combined_question"], question)
        self.assertIn("equivalent copies obtained elsewhere", question)
        self.assertTrue(self.result["gates"]["operator_question_covers_the_spec_question"])

    def test_the_availability_probe_is_head_only_and_not_performed(self) -> None:
        probe = self.result["source_register"]["probe"]
        self.assertTrue(probe["enabled"], "retrieval is authorised, so a HEAD probe is legitimate")
        self.assertFalse(probe["performed"], "but this turn contacted nothing")
        self.assertFalse(probe["content_bytes_may_be_read"])
        self.assertFalse(probe["credentials_may_be_used"])
        self.assertEqual(probe["allowed_methods"], ["HEAD"])
        self.assertTrue(self.result["gates"]["availability_probe_stays_head_only"])

    def test_the_rule_text_is_still_quoted_verbatim_against_the_decision(self) -> None:
        self.assertTrue(self.result["gates"]["rules_citations_are_verbatim"])
        self.assertIn("operator decision", self.result["authorization"]["tension_recorded"].lower())
        self.assertGreaterEqual(len(self.result["authorization"]["still_prohibited"]), 4)

    def test_no_model_or_submission_script_references_the_lab(self) -> None:
        self.assertEqual(self.result["firewall"]["hits"], [])
        listed = config()["firewall"]["model_and_submission_scripts_must_not_reference_lab"]
        self.assertEqual(self.result["firewall"]["scripts_scanned"], len(listed))
        chain = json.loads((ROOT / "config/v2-strata-descriptive-preregistered.json")
                           .read_text(encoding="utf-8"))["model_input_separation"]["model_chain_scripts"]
        for script in chain + ["scripts/submit_zindi.py"]:
            self.assertIn(script, listed, script)
        tokens = self.result["firewall"]["tokens"]
        for relative in listed:
            text = (ROOT / relative).read_text(encoding="utf-8").lower()
            with self.subTest(script=relative):
                self.assertEqual([token for token in tokens if token in text], [])

    def test_a_model_chain_script_touching_the_lab_is_a_gate_failure(self) -> None:
        script = Path(tempfile.mkdtemp(prefix="lab-scan-")) / "fake_model_chain.py"
        script.write_text("from pathlib import Path\n"
                          "Path('/tmp/lab/buildings/ca_building.geojson').read_bytes()\n",
                          encoding="utf-8")
        result = report(extra_scan_scripts=[script])
        self.assertFalse(result["gates"]["model_and_submission_paths_cannot_read_lab"])
        self.assertTrue(result["firewall"]["hits"])

    def test_the_lab_root_is_outside_the_repo_and_empty(self) -> None:
        lab = self.result["lab_root"]
        self.assertFalse(lab["inside_repo"])
        self.assertEqual(lab["file_count"], 0)
        self.assertEqual(lab["tracked_in_git"], [])

    def test_county_filtering_is_required_exactly_where_a_state_holds_study_tracts(self) -> None:
        consistency = self.result["county_filter_consistency"]
        self.assertEqual(consistency["mismatches"], [])
        self.assertEqual(consistency["required_groups"], ["TX-W", "AZ-R", "OK-R", "NM", "CA-F"])
        self.assertEqual(consistency["unfiltered_groups_without_study_tracts"],
                         ["NV", "AR-KS", "LA", "CO-UT"])

    def test_definedness_accounting_is_declared_before_any_number_exists(self) -> None:
        accounting = self.result["definedness_accounting"]
        self.assertTrue(accounting["undefined_is_not_zero"])
        self.assertGreaterEqual(len(accounting["per_group_per_component_required"]), 5)
        self.assertGreaterEqual(self.result["acceptance"]["minimum_labelled_groups"], 5)
        self.assertEqual(self.result["recipe"]["self_check_status"], "not_run_pending_ruling")


@unittest.skipUnless((WORKSPACE / "defined-flags-inputs").is_dir(), "workspace required")
class TamperTests(unittest.TestCase):
    EXPECTED = {
        "decision_fabricated": "operator_decision_present_verbatim_in_spec",
        "conditions_dropped": "operator_conditions_enforced",
        "weights_smuggle_labels": "track_b_crossing_surface_is_bounded",
        "probe_reads_content": "availability_probe_stays_head_only",
        "lab_data_present": "no_prohibited_content_on_disk",
        "county_filter_flag_removed": "county_filtered_retrieval_required_recorded",
        "buffer_removed": "neighbour_buffer_policy_rejects_study_and_adjacent_tracts",
        "exclusion_emptied": "study_geoids_fully_excluded",
        "source_field_dropped": "source_register_complete",
        "lab_root_in_repo": "lab_root_outside_repo",
        "model_reads_lab": "model_and_submission_paths_cannot_read_lab",
        "question_drops_the_spec_question": "operator_question_covers_the_spec_question",
    }

    def _run(self, tamper: str) -> dict:
        lab_root = None
        extra: list[Path] = []
        if tamper == "lab_data_present":
            folder = Path(tempfile.mkdtemp(prefix="lab-tamper-"))
            (folder / "tiger").mkdir()
            (folder / "tiger" / "tl_2024_48_prisecroads.zip").write_text("not reference data",
                                                                         encoding="utf-8")
            lab_root = folder
        if tamper == "model_reads_lab":
            script = Path(tempfile.mkdtemp(prefix="lab-scan-")) / "fake_model_chain.py"
            script.write_text("import pandas\n"
                              "pandas.read_parquet('/tmp/lab/tiger/tl_2024_48_prisecroads.zip')\n",
                              encoding="utf-8")
            extra = [script]
        return report(tamper, lab_root=lab_root, extra_scan_scripts=extra)

    def test_the_preregistered_cases_match_the_expected_gates(self) -> None:
        declared = {case["id"]: case["expect_gate"] for case in config()["negative_controls"]}
        self.assertEqual(declared, self.EXPECTED)

    def test_every_negative_control_fires_its_expected_gate(self) -> None:
        for tamper, expected_gate in self.EXPECTED.items():
            with self.subTest(tamper=tamper):
                result = self._run(tamper)
                self.assertFalse(result["gates"]["all_passed"])
                self.assertIn(expected_gate,
                              [gate for gate, value in result["gates"].items() if not value])
                self.assertFalse(result["gates"][expected_gate])

    def test_an_unknown_tamper_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            report("build_the_lab_anyway")

    def test_tamper_runs_never_rewrite_a_committed_file(self) -> None:
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in COMMITTED}
        for tamper in self.EXPECTED:
            self._run(tamper)
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in COMMITTED}
        self.assertEqual(before, after)


@unittest.skipUnless((WORKSPACE / "defined-flags-inputs").is_dir(), "workspace required")
class DeterminismAndSafetyTests(unittest.TestCase):
    def test_two_runs_are_identical(self) -> None:
        self.assertEqual(readiness.canonical_bytes(report()),
                         readiness.canonical_bytes(report()))

    def test_the_committed_report_and_markdown_match_a_fresh_build(self) -> None:
        self.assertEqual(readiness.render_markdown(report()),
                         MARKDOWN_PATH.read_text(encoding="utf-8"))
        completed = subprocess.run(
            [UV, "run", "--quiet", *DEPENDENCIES, "python", "scripts/report_lab_readiness.py",
             "--check-only"], cwd=ROOT, capture_output=True, text=True, timeout=1200)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("unchanged", completed.stdout)

    def test_no_wall_clock_and_no_nan_in_the_committed_products(self) -> None:
        document = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        self.assertTrue(document["gates"]["report_is_strict_json"])
        self.assertTrue(document["gates"]["report_has_no_temporal_field"])
        self.assertEqual(readiness.wall_clock_hits({
            "authorization": document["authorization"], "groups": document["groups"],
            "spec_cross_check": document["spec_cross_check"], "exclusion": document["exclusion"],
            "lab_root": document["lab_root"]}), [])

    def test_the_readiness_code_itself_downloads_and_uploads_nothing(self) -> None:
        for path in (ROOT / "scripts/report_lab_readiness.py", ROOT / "src/bias60/lab_plan.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(script=path.name):
                for marker in ("requests.", "urllib", "httpx", "urlopen", "boto3", "S3FileSystem",
                               "socket.", "put(", "upload(", "submit_zindi(", "zindi_submit(",
                               "open_browser", "platform.zindi"):
                    self.assertNotIn(marker, source, marker)

    def test_the_repo_holds_no_lab_data(self) -> None:
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True,
                                 text=True).stdout.split()
        self.assertEqual([entry for entry in tracked
                          if entry.startswith("lab/") or "/lab/" in entry], [])
        self.assertFalse((ROOT / "lab").exists())

    def test_the_workspace_lab_directory_is_absent(self) -> None:
        self.assertFalse((WORKSPACE / "lab").exists())


class SpatialIndexTests(unittest.TestCase):
    """The buffer check gained a uniform grid for speed; it must not change a single verdict."""

    def brute_force_reason(self, policy, geoid, county, centroid):
        if geoid in policy.excluded_geoids:
            return "official_scored_or_template_geoid"
        if policy.county_exclusion_enabled and county and county in policy.study_county_fips:
            return "study_county"
        if county and county[:2] in policy.study_states and policy.buffer_km <= 0.0:
            return "study_state_with_no_buffer"
        if centroid is not None and policy.buffer_km > 0.0 and policy.study_points:
            for x, y in policy.study_points:
                if (x - centroid[0]) ** 2 + (y - centroid[1]) ** 2 <= policy.buffer_km ** 2 * 1e6:
                    return "within_neighbour_buffer_of_study_tract"
        return None

    def test_the_grid_returns_exactly_what_the_brute_force_scan_returns(self) -> None:
        import random

        random.seed(20261025)
        study = [(random.uniform(-1.5e6, -1.0e6), random.uniform(1.2e6, 1.5e6)) for _ in range(400)]
        policy = lab_plan.ExclusionPolicy(official_geoids=frozenset({"04013000100"}),
                                         template_geoids=frozenset({"53001000200"}),
                                         study_county_fips=frozenset({"04013"}),
                                         study_states=frozenset({"04", "53"}),
                                         buffer_km=8.0, study_points=study,
                                         county_exclusion_enabled=True)
        disagreements = 0
        for index in range(2000):
            centroid = (random.uniform(-1.6e6, -0.9e6), random.uniform(1.1e6, 1.6e6))
            county = random.choice(["04013", "04017", "32003", "08031"])
            geoid = random.choice(["04013000100", "53001000200", f"99000{index:06d}"])
            expected = self.brute_force_reason(policy, geoid, county, centroid)
            actual = policy.reject_reason(geoid, county, centroid)
            disagreements += int(expected != actual)
        self.assertEqual(disagreements, 0)
        self.assertIsNotNone(policy._grid, "the index should have been built on first use")

    def test_a_point_just_outside_the_buffer_is_kept_and_just_inside_is_dropped(self) -> None:
        policy = lab_plan.ExclusionPolicy(official_geoids=frozenset(), template_geoids=frozenset(),
                                         study_county_fips=frozenset(), study_states=frozenset(),
                                         buffer_km=8.0, study_points=[(0.0, 0.0)])
        self.assertEqual(policy.reject_reason("99000000001", None, (7999.0, 0.0)),
                         "within_neighbour_buffer_of_study_tract")
        self.assertIsNone(policy.reject_reason("99000000002", None, (8001.0, 0.0)))
        # a study point one cell away must still be found: a hole in the nine-cell window would
        # silently accept a tract that sits seven kilometres from a study neighbour
        neighbour = lab_plan.ExclusionPolicy(official_geoids=frozenset(),
                                             template_geoids=frozenset(),
                                             study_county_fips=frozenset(),
                                             study_states=frozenset(), buffer_km=8.0,
                                             study_points=[(7900.0, 0.0)])
        self.assertEqual(neighbour.reject_reason("99000000003", None, (15800.0, 0.0)),
                         "within_neighbour_buffer_of_study_tract")
        self.assertIsNone(neighbour.reject_reason("99000000004", None, (16200.0, 0.0)))

    def test_the_prerequisite_probe_is_not_part_of_the_model_chain(self) -> None:
        chain = ["scripts/build_estimator_v2_candidates.py", "scripts/run_bias60_ideas.py",
                 "scripts/build_ideas_registry.py", "scripts/evaluate_bias60_on_lab.py",
                 "scripts/probe_lab_prerequisites.py"]
        for relative in chain[:-1]:
            source = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(script=relative):
                self.assertNotIn("import probe_lab_prerequisites", source)
                self.assertNotIn("from bias60 import probe", source)
                self.assertNotIn("urlopen", source,
                                 "a model-chain script must not reach the network")
        probe_source = (ROOT / "scripts/probe_lab_prerequisites.py").read_text(encoding="utf-8")
        self.assertIn('method="HEAD"', probe_source)


if __name__ == "__main__":
    unittest.main()
