"""Tests for the lab evaluation: the contract, the measurement maths, and the rails around both.

The committed report is generated with no lab present, so these tests carry a synthetic fixture
(``tests/lab_fixture.py``) that supplies a lab with a known weight vector. Two properties matter most:
with a lab present the machinery must recover the truth it was handed, and with no lab present it must
publish no number at all.
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
sys.path.insert(0, str(ROOT / "tests"))

import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

import lab_fixture  # noqa: E402
from bias60 import lab_eval, lab_plan  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "evaluate_bias60_on_lab", ROOT / "scripts/evaluate_bias60_on_lab.py")
evaluate = importlib.util.module_from_spec(SPEC)
sys.modules["evaluate_bias60_on_lab"] = evaluate
SPEC.loader.exec_module(evaluate)

CONFIG_PATH = ROOT / "config/lab-evaluation-preregistered.json"
REPORT_PATH = ROOT / "reports/lab-evaluation.json"
MARKDOWN_PATH = ROOT / "docs/writeup/lab-evaluation.md"
WORKSPACE = Path(os.environ.get("COMPETITION_WORKSPACE", "") or ".").resolve()
UV = "/home/martin/hddProjects/langywrap/uv"
DEPENDENCIES = ["--with", "numpy==2.5.4", "--with", "pyarrow==26.0.0"]
COMMITTED = [CONFIG_PATH, ROOT / "src/bias60/lab_eval.py", ROOT / "scripts/evaluate_bias60_on_lab.py",
             REPORT_PATH, MARKDOWN_PATH]
SPEC_PATH = Path("/home/martin/hddProjects/competitionsall/docs/research/bias-60-ideas-2026-10.md")


def config(*, fast: bool = False) -> dict:
    document = evaluate.load_config(CONFIG_PATH)
    if fast:
        # the frozen 200 draws are asserted in FrozenDesignTests; the rest of the suite only needs
        # stability selection to be exercised, and 25 draws over whole groups does that
        document["weights"]["stability_draws"] = 25
    return document


def build(tamper: str | None = None, *, lab_root: Path | None = None,
          fast: bool = True) -> dict:
    # a fresh access log per build, exactly as a single CLI invocation has
    evaluate.OPENED.clear()
    lab_plan.set_recorder(evaluate.record)
    return evaluate.build_report(config(fast=fast), workspace=WORKSPACE,
                                lab_root=lab_root or (WORKSPACE / "lab"), tamper=tamper)


def fixture_root(**kwargs) -> Path:
    root = Path(tempfile.mkdtemp(prefix="lab-eval-"))
    lab_fixture.write_lab(root, **kwargs)
    return root


def measured_report(**kwargs) -> tuple[dict, Path, dict]:
    root = Path(tempfile.mkdtemp(prefix="lab-eval-"))
    truth = lab_fixture.write_lab(root, **kwargs)
    return build(lab_root=root), root, truth


require_workspace = unittest.skipUnless((WORKSPACE / "defined-flags-inputs").is_dir(),
                                        "COMPETITION_WORKSPACE is not available")


class FrozenDesignTests(unittest.TestCase):
    def test_the_spec_hash_pin_matches_the_frozen_memo(self) -> None:
        self.assertEqual(lab_plan.sha256_file(SPEC_PATH), config()["spec"]["sha256"])

    def test_the_pin_is_shared_with_the_other_bias60_plans(self) -> None:
        pin = config()["spec"]["sha256"]
        for relative in ("config/bias60-harness-preregistered.json",
                         "config/bias60-lab-preregistered.json"):
            with self.subTest(config=relative):
                self.assertEqual(json.loads((ROOT / relative).read_text(encoding="utf-8"))
                                 ["spec"]["sha256"], pin)

    def test_the_declared_contract_equals_the_code_contract(self) -> None:
        contract = config()["artifact_contract"]
        self.assertEqual(contract["files"], list(lab_eval.ARTIFACTS))
        self.assertEqual(contract["tracts_columns"], list(lab_eval.TRACT_COLUMNS))
        self.assertEqual(contract["estimators_columns"], list(lab_eval.ESTIMATOR_COLUMNS))
        self.assertEqual(contract["labels_columns"], list(lab_eval.LABEL_COLUMNS))
        self.assertEqual(contract["manifest_fields"], list(lab_eval.MANIFEST_REQUIRED))

    def test_channel_names_name_their_target(self) -> None:
        self.assertEqual(lab_eval.label_component_for("composite"), "composite")
        self.assertEqual(lab_eval.label_component_for("target:R"), "R")
        for bad in ("road", "", "target:", "composite:extra"):
            with self.subTest(channel=bad):
                with self.assertRaises(ValueError):
                    lab_eval.label_component_for(bad)

    def test_the_frozen_design_matches_spec_32_and_33(self) -> None:
        cfg = config()
        self.assertEqual(cfg["acceptance"]["minimum_held_out_groups"], 5)
        self.assertEqual(cfg["acceptance"]["minimum_track_b_win_rate"], 0.75)
        self.assertEqual(cfg["weights"]["stability_draws"], 200)
        self.assertEqual(cfg["weights"]["stability_threshold"], 0.6)
        self.assertTrue(cfg["weights"]["positivity"])
        self.assertTrue(cfg["weights"]["inner_folds_are_county_blocked"])
        self.assertEqual(cfg["recipe"]["road_band"], [0.71, 1.59])
        self.assertEqual(cfg["recipe"]["hospital_target"], 12.0)
        self.assertEqual(cfg["track_a_preregistration"]["row"], "bias60-track-a-combiner")
        self.assertEqual(cfg["track_a_preregistration"]["weight_vector_release"], "withheld")


@require_workspace
class NotRunStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = build()

    def test_absent_lab_is_reported_as_not_run_and_still_passes_its_contract(self) -> None:
        self.assertEqual(self.report["summary"]["measurement_status"], "not_run_missing_lab")
        self.assertFalse(self.report["dependency"]["lab_present"])
        self.assertTrue(self.report["gates"]["all_passed"], self.report["gates"])
        self.assertTrue(self.report["gates"]["contract_all_passed"])

    def test_no_number_is_published_when_the_lab_is_absent(self) -> None:
        measurement = self.report["measurement"]
        self.assertEqual(evaluate._floats(measurement["channels"]), [])
        self.assertEqual(self.report["summary"]["weight_vectors_published"], 0)
        self.assertIsNone(self.report["acceptance"]["met"])
        for name, value in self.report["measurement_gates"].items():
            with self.subTest(gate=name):
                self.assertIsNone(value)
        for name, value in self.report["contract_gates"].items():
            if name in {"manifest_provenance_complete", "recipe_self_checks_passed",
                        "study_geoid_exclusion_holds", "nearest_distance_buffer_respected",
                        "rows_consistent", "undefined_never_scored_as_zero",
                        "groups_held_out_adequate"}:
                with self.subTest(gate=name):
                    self.assertIsNone(value)

    def test_the_committed_report_publishes_no_measurement_either(self) -> None:
        document = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(document["dependency"]["measurement_status"], "not_run_missing_lab")
        self.assertEqual(evaluate._floats(document["measurement"]["channels"]), [])
        self.assertTrue(document["gates"]["all_passed"])
        self.assertEqual(document["summary"]["weight_vectors_published"], 0)

    def test_a_half_built_lab_is_a_contract_error_not_a_zero(self) -> None:
        root = fixture_root()
        (root / "labels.parquet").unlink()
        with self.assertRaises(ValueError):
            lab_eval.load_lab(root)
        (root / "manifest.json").unlink()
        with self.assertRaises(ValueError):
            lab_eval.load_lab(root)

    def test_an_unexpected_column_is_rejected_rather_than_ignored(self) -> None:
        root = fixture_root()
        table = pq.read_table(root / "tracts.parquet")
        table = table.append_column("target", pa.array([0.1] * table.num_rows))
        pq.write_table(table, root / "tracts.parquet")
        with self.assertRaises(ValueError):
            lab_eval.load_lab(root)

    def test_the_row_reads_no_reference_layer_and_no_study_side_file(self) -> None:
        opened = " ".join(self.report["access"]["opened_paths"]).lower()
        for token in lab_eval.REFERENCE_LAYER_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, opened)
        self.assertNotIn("ideas/", opened)
        self.assertEqual(self.report["access"]["reference_layer_reads"], 0)
        self.assertEqual(self.report["study_side"]["study_predictions_emitted"], 0)
        self.assertEqual(self.report["study_side"]["study_idea_parquets_opened"], 0)

    def test_no_fabricated_measurement_is_possible_without_the_gate_firing(self) -> None:
        result = build("measurement_fabricated_without_lab")
        self.assertFalse(result["gates"]["no_fabricated_measurement"])
        self.assertFalse(result["gates"]["all_passed"])

    def test_the_evaluator_code_itself_never_downloads_or_uploads(self) -> None:
        for path in (ROOT / "scripts/evaluate_bias60_on_lab.py", ROOT / "src/bias60/lab_eval.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(script=path.name):
                for marker in ("requests.", "urllib", "httpx", "urlopen", "boto3", "S3FileSystem",
                               "socket.", "upload(", "submit_zindi(", "zindi_submit("):
                    self.assertNotIn(marker, source, marker)


@require_workspace
class MeasuredStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report, cls.root, cls.truth = measured_report(per_group=30)
        cls.registry = cls.truth["registry"]
        cls.measurement = cls.report["measurement"]
        cls.composite = cls.measurement["channels"]["composite"]

    def test_the_lab_is_measured_and_every_measurement_gate_passes(self) -> None:  # noqa: E301
        self.assertEqual(self.report["summary"]["measurement_status"], "measured")
        self.assertTrue(self.report["gates"]["all_passed"], self.report["gates"])
        for name, value in self.report["measurement_gates"].items():
            with self.subTest(gate=name):
                self.assertTrue(value)
        self.assertTrue(self.report["acceptance"]["met"])

    def test_the_lab_fitted_weights_recover_the_true_vector(self) -> None:
        truth = lab_fixture._weights(self.registry["implemented"])
        ordered_truth = [truth[name] for name in self.composite["ideas"]]
        self.assertEqual(len(self.composite["weight_vector"]), len(ordered_truth))
        for name, fitted, expected in zip(self.composite["ideas"], self.composite["weight_vector"],
                                          ordered_truth):
            with self.subTest(idea=name):
                self.assertLess(abs(fitted - expected), 0.03, (fitted, expected))
        self.assertTrue(all(value >= 0.0 for value in self.composite["weight_vector"]),
                        "spec 3.2 requires non-negative weights")

    def test_track_b_beats_equal_weights_and_the_win_rate_rule_is_applied(self) -> None:
        self.assertLess(self.composite["rmse_track_b"], self.composite["rmse_equal_weights"])
        self.assertGreaterEqual(self.composite["win_rate_vs_equal"]["win_rate"], 0.75)
        self.assertTrue(self.composite["track_b_accepted_by_rule"])
        self.assertEqual(self.composite["win_rate_vs_equal"]["groups_compared"],
                         len(self.composite["groups"]))
        self.assertGreaterEqual(len(self.composite["groups"]),
                               config()["acceptance"]["minimum_held_out_groups"])
        worst = max(entry["rmse"] for entry in self.composite["per_group_track_b"])
        self.assertAlmostEqual(worst, self.composite["worst_group_rmse"])

    def test_every_implemented_idea_is_scored_with_incremental_value(self) -> None:
        self.assertEqual(sorted(self.composite["ideas"]), sorted(self.registry["implemented"]))
        rows = {row["idea"]: row for row in self.composite["incremental_value"]}
        self.assertEqual(sorted(rows), sorted(self.registry["implemented"]))
        for idea, row in rows.items():
            with self.subTest(idea=idea):
                self.assertIsNotNone(row["correlation_with_label"])
                self.assertIsNotNone(row["partial_correlation_with_label"])
                self.assertIsNotNone(row["drop_column_delta_rmse"])
                self.assertGreater(row["correlation_with_label"], 0.5)
        deltas = [row["drop_column_delta_rmse"] for row in rows.values()]
        self.assertTrue(all(delta >= 0.0 for delta in deltas),
                        "dropping a useful estimator must not lower grouped-CV RMSE")
        self.assertGreater(sum(deltas), 0.0, "at least one estimator must carry real signal")
        heaviest = max(self.truth["true_weights"],
                       key=lambda name: self.truth["true_weights"][name])
        self.assertEqual(max(rows, key=lambda idea: rows[idea]["drop_column_delta_rmse"] or 0.0),
                         heaviest, "the largest truth weight should cost the most when dropped")

    def test_residual_correlation_mtmm_and_ambiguity_are_reported(self) -> None:
        residual = self.composite["residual_correlation"]
        ideas = len(self.composite["ideas"])
        self.assertEqual(residual["pairs"], ideas * (ideas - 1) // 2)
        self.assertIsNotNone(residual["off_diagonal_mass"])
        self.assertEqual(len(residual["matrix"]), ideas)
        mtmm = self.composite["mtmm"]
        self.assertFalse(mtmm["single_member"])
        self.assertIsNotNone(mtmm["between_component_mean_abs_rho"])
        self.assertIsNotNone(self.composite["krogh_vedelsby_ambiguity"])
        self.assertGreaterEqual(self.composite["krogh_vedelsby_ambiguity"], 0.0)

    def test_component_channels_score_only_their_own_component(self) -> None:
        for channel, entry in self.measurement["channels"].items():
            if channel == "composite":
                continue
            component = channel.split("target:", 1)[1]
            with self.subTest(channel=channel):
                self.assertEqual(entry["ideas"], sorted(
                    idea for idea, value in self.registry["components"].items()
                    if value == component))
                self.assertTrue(entry["mtmm"]["single_member"])
                # a single-member channel cannot beat equal weights, and must say so
                self.assertTrue(entry["reported_as_not_beating_equal_weights"]
                                or entry["track_b_accepted_by_rule"])

    def test_stability_selection_runs_over_whole_groups(self) -> None:
        stability = self.composite["stability_selection"]
        self.assertGreater(stability["bootstrap_draws_used"], 0)
        self.assertEqual(stability["threshold"], 0.6)
        self.assertEqual(len(stability["nonzero_share"]), len(self.composite["ideas"]))
        self.assertTrue(stability["selected_indices"], "a real contributor must survive selection")

    def test_track_a_variants_are_scored_or_documented_as_unrunnable(self) -> None:
        variants = self.composite["track_a_variants"]
        self.assertEqual(variants["A1"]["status"], "scored")
        self.assertTrue(np.isfinite(variants["A1"]["rmse"]))
        for variant, ideas in (("A3", ["X1", "Sc2", "Mx5", "P3", "S3", "D2", "X5"]),
                               ("A4", ["T5", "Tm4", "M1", "M5"])):
            with self.subTest(variant=variant):
                self.assertEqual(variants[variant]["status"], "not_runnable")
                self.assertEqual(variants[variant]["missing_ideas"],
                                 sorted(set(ideas) - set(self.registry["implemented"])))
                self.assertTrue(variants[variant]["reason"])

    def test_undefined_rows_are_never_scored_as_zero(self) -> None:
        tracts = lab_eval.load_lab(self.root)["tracts"]["lab_tract_id"]
        # the fixture marks the first idea undefined on about 5% of tracts, so the composite channel
        # must run on fewer rows than the lab contains
        self.assertLess(self.composite["rows"], len(tracts))
        self.assertGreater(self.composite["rows"], 0)

    def test_the_crossing_surface_carries_only_weights_and_constants(self) -> None:
        crossing = self.report["crossing_surface"]
        self.assertEqual(crossing["allowed_fields"], config()["crossing_surface"]["allowed_fields"])
        self.assertEqual(crossing["forbidden_fields_observed"], [])
        self.assertEqual(crossing["lab_commit"], "fixture-commit-0000000")
        self.assertEqual(len(crossing["weight_vectors"]),
                         len(self.measurement["channels"]))
        for vector in crossing["weight_vectors"]:
            with self.subTest(channel=vector["channel"]):
                self.assertEqual(len(vector["weights_sha256"]), 64)
                self.assertIsNotNone(vector["lambda"])
        encoded = json.dumps(crossing).lower()
        for forbidden in config()["crossing_surface"]["forbidden_fields"]:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden.lower(), encoded)

    def test_shift_correction_is_declared_not_wired_until_a_study_allowlist_exists(self) -> None:
        shift = self.measurement["shift_correction"]
        self.assertEqual(shift["status"], "not_wired")
        self.assertTrue(shift["implemented"])
        self.assertEqual(shift["requires"], "study_side_label_free_feature_allowlist")
        self.assertTrue(self.report["gates"]["shift_correction_status_declared"])

    def test_two_runs_on_the_same_lab_are_identical(self) -> None:
        again = build(lab_root=self.root)
        self.assertEqual(evaluate.canonical_bytes(again),
                         evaluate.canonical_bytes(self.report))


class NumericCoreTests(unittest.TestCase):
    def test_nnls_recovers_a_positive_vector_and_respects_the_cone(self) -> None:
        rng = np.random.default_rng(11)
        truth = np.array([0.6, 0.0, 0.4])
        evidence = rng.uniform(0.0, 1.0, size=(400, 3))
        target = evidence @ truth
        w0 = np.full(3, 1.0 / 3.0)
        fitted = lab_eval.nnls_ridge(evidence, target, w0, lam=1e-6)
        self.assertTrue(np.all(fitted >= 0.0))
        np.testing.assert_allclose(fitted, truth, atol=1e-6)

    def test_ridge_shrinks_toward_the_equal_weight_vector_as_lambda_grows(self) -> None:
        rng = np.random.default_rng(12)
        evidence = rng.uniform(0.0, 1.0, size=(200, 4))
        target = evidence @ np.array([0.7, 0.2, 0.1, 0.0]) + rng.normal(0, 0.01, 200)
        w0 = np.full(4, 0.25)
        low = lab_eval.nnls_ridge(evidence, target, w0, lam=1e-6)
        high = lab_eval.nnls_ridge(evidence, target, w0, lam=1e3)
        self.assertLess(np.linalg.norm(high - w0), np.linalg.norm(low - w0))

    def test_group_folds_hold_out_whole_groups(self) -> None:
        groups = ["a", "a", "b", "c", "c", "c"]
        folds = lab_eval.group_folds(groups)
        self.assertEqual([name for name, _train, _test in folds], ["a", "b", "c"])
        for _name, train, test in folds:
            self.assertEqual(sorted(train + test), list(range(len(groups))))
            self.assertFalse(set(train) & set(test))

    def test_county_blocked_folds_keep_counties_together(self) -> None:
        counties = ["c1", "c1", "c1", "c2", "c2", "c3"]
        groups = ["a", "a", "a", "b", "b", "b"]
        folds = lab_eval.county_blocked_folds(counties, groups, 3)
        for _train, test in folds:
            held = {counties[index] for index in test}
            for county in held:
                self.assertTrue(all((counties[index] in held) == (index in test)
                                    for index in range(len(counties))))

    def test_partial_correlation_finds_the_informative_column(self) -> None:
        rng = np.random.default_rng(13)
        latent = rng.uniform(size=300)
        informative = latent + rng.normal(0, 0.01, 300)
        noise = rng.uniform(size=300)
        matrix = np.column_stack([informative, noise, noise * 0.5 + rng.normal(0, 0.01, 300)])
        target = latent + rng.normal(0, 0.01, 300)
        strong = lab_eval.partial_correlation(matrix, target, 0)
        weak = lab_eval.partial_correlation(matrix, target, 1)
        self.assertGreater(strong, 0.9)
        self.assertLess(abs(weak), 0.3)

    def test_shift_weights_reweight_toward_the_harder_region(self) -> None:
        rng = np.random.default_rng(14)
        lab_features = rng.normal(0.0, 1.0, size=(300, 2))
        study_features = rng.normal(1.5, 1.0, size=(150, 2))
        result = lab_eval.shift_weights(lab_features, study_features, (1.0, 99.0), seed=3)
        self.assertGreater(result["weight_mean"], 0.0)
        self.assertLessEqual(result["clip_low"], result["clip_high"])
        self.assertGreater(result["effective_sample_size"], 1.0)
        self.assertEqual(len(result["weights_sha256"]), 64)

    def test_area_of_applicability_flags_study_tracts_outside_the_lab(self) -> None:
        rng = np.random.default_rng(15)
        lab_features = rng.normal(0.0, 1.0, size=(400, 2))
        inside = rng.normal(0.0, 0.5, size=(100, 2))
        result = lab_eval.area_of_applicability(lab_features, inside)
        self.assertLess(result["study_outside_share"], 0.2)
        far = rng.normal(6.0, 1.0, size=(100, 2))
        self.assertGreater(lab_eval.area_of_applicability(lab_features, far)["study_outside_share"],
                           0.9)

    def test_mtmm_separates_within_from_between_component(self) -> None:
        rng = np.random.default_rng(16)
        trait_1 = rng.uniform(size=200)
        trait_2 = rng.uniform(size=200)
        matrix = np.column_stack([trait_1 + rng.normal(0, 0.02, 200),
                                  trait_1 + rng.normal(0, 0.02, 200),
                                  trait_2 + rng.normal(0, 0.02, 200)])
        names = ["A1", "A2", "B1"]
        result = lab_eval.mtmm(matrix, names, {"A1": "A", "A2": "A", "B1": "B"})
        self.assertGreater(result["within_component_mean_abs_rho"], 0.9)
        self.assertLess(result["between_component_mean_abs_rho"], 0.3)
        self.assertGreater(result["convergent_minus_discriminant"], 0.5)


@require_workspace
class NegativeControlTests(unittest.TestCase):
    DATA_TAMPERS = {"study_geoid_in_lab", "nearest_distance_understated", "self_check_fabricated",
                    "missing_source_hash", "too_few_groups", "undefined_rows_zero_filled",
                    "labels_outside_unit"}

    def expected_map(self) -> dict:
        return {case["id"]: case["expect_gate"] for case in config()["negative_controls"]}

    def test_the_preregistered_cases_match_the_expected_gates(self) -> None:
        self.assertEqual(self.expected_map(), {
            "study_geoid_in_lab": "study_geoid_exclusion_holds",
            "nearest_distance_understated": "nearest_distance_buffer_respected",
            "self_check_fabricated": "recipe_self_checks_passed",
            "undefined_rows_zero_filled": "undefined_never_scored_as_zero",
            "missing_source_hash": "manifest_provenance_complete",
            "too_few_groups": "groups_held_out_adequate",
            "labels_outside_unit": "rows_consistent",
            "weights_smuggle_lab_values": "crossing_surface_is_bounded",
            "measurement_fabricated_without_lab": "no_fabricated_measurement",
            "study_side_prediction_emitted": "weights_not_applied_to_study_tracts",
            "release_before_track_a_preregistration": "track_a_release_gate_recorded",
            "spec_pin_drift": "spec_hash_matches_pin",
            "shift_claimed_without_allowlist": "shift_correction_status_declared",
            "prerequisite_probe_absent": "prerequisite_probe_recorded",
            "prerequisite_findings_hidden": "prerequisite_probe_findings_disclosed",
        })

    def test_every_negative_control_fires_its_expected_gate(self) -> None:
        official = sorted(lab_plan.official_scored_ids(WORKSPACE))[0]
        for tamper, gate in self.expected_map().items():
            with self.subTest(tamper=tamper):
                lab_root = None
                if tamper in self.DATA_TAMPERS:
                    lab_root = fixture_root(
                        per_group=16,
                        inject_official_geoid=official if tamper == "study_geoid_in_lab" else None)
                result = build(tamper, lab_root=lab_root)
                failed = {name for name, value in {**result["contract_gates"],
                                                   **result["measurement_gates"],
                                                   **result["gates"]}.items() if value is False}
                self.assertIn(gate, failed)
                self.assertFalse(result["gates"]["all_passed"])

    def test_an_unknown_tamper_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build("run_the_lab_anyway", lab_root=fixture_root())

    def test_tamper_runs_never_rewrite_a_committed_file(self) -> None:
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in COMMITTED}
        for tamper in self.expected_map():
            lab_root = fixture_root(per_group=12) if tamper in self.DATA_TAMPERS else None
            build(tamper, lab_root=lab_root)
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in COMMITTED}
        self.assertEqual(before, after)


@require_workspace
class DeterminismTests(unittest.TestCase):
    def test_the_committed_report_and_markdown_match_a_fresh_build(self) -> None:
        # fast=False: this test must reproduce the committed artifact, so it uses the frozen
        # 200-draw stability setting rather than the trimmed one the other tests run on
        report = build(fast=False)
        self.assertEqual(evaluate.render_markdown(report),
                         MARKDOWN_PATH.read_text(encoding="utf-8"))
        self.assertEqual(evaluate.canonical_bytes(report), REPORT_PATH.read_bytes())

    def test_check_only_rejects_a_mutated_committed_report(self) -> None:
        completed = subprocess.run(
            [UV, "run", "--quiet", *DEPENDENCIES, "python", "scripts/evaluate_bias60_on_lab.py",
             "--check-only"], cwd=ROOT, capture_output=True, text=True, timeout=1200)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("unchanged", completed.stdout)
        document = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        tampered_path = ROOT / "reports/.lab-evaluation-tampered.json"
        document["measurement"]["channels"] = {"composite": {"rmse_track_b": 0.0831}}
        tampered_path.write_bytes(evaluate.canonical_bytes(document))
        try:
            completed = subprocess.run(
                [UV, "run", "--quiet", *DEPENDENCIES, "python", "scripts/evaluate_bias60_on_lab.py",
                 "--check-only", "--out", str(tampered_path.relative_to(ROOT))],
                cwd=ROOT, capture_output=True, text=True, timeout=1200)
            self.assertEqual(completed.returncode, 1, completed.stdout)
            self.assertIn("CHANGED", completed.stdout)
        finally:
            tampered_path.unlink(missing_ok=True)

    def test_no_wall_clock_and_no_forbidden_value_in_the_committed_products(self) -> None:
        document = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        self.assertTrue(document["gates"]["report_has_no_temporal_field"])
        self.assertTrue(document["gates"]["report_is_strict_json"])
        self.assertTrue(document["gates"]["no_forbidden_value_in_report"])
        encoded = REPORT_PATH.read_text(encoding="utf-8")
        for token in config()["forbidden_value_tokens"]:
            self.assertNotIn(token, encoded, token)
        def keys(node, trail=""):
            found = []
            if isinstance(node, dict):
                for key, child in node.items():
                    found.extend(keys(child, f"{trail}/{key}"))
                    found.append(f"{trail}/{key}")
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    found.extend(keys(child, f"{trail}[{index}]"))
            return found
        names = set(keys(document))
        self.assertNotIn("/measurement/channels/composite/lab_tract_id", names)
        self.assertFalse([name for name in names if name.endswith("/lab_tract_id")
                          and not name.startswith("/artifact_contract")])
        self.assertEqual(document["access"]["lab_rows_opened"], 0)


if __name__ == "__main__":
    unittest.main()
