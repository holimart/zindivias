"""Tests for the passive leaderboard snapshot and Bias Scorecard capture.

Row: bias-lb-snapshot-and-scorecard. The row is passive reporting: it must snapshot the public
leaderboard and the organiser scorecards without uploading, without storing credentials, and without
letting any of those numbers into the model chain.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UV = "/home/martin/hddProjects/langywrap/uv"
SCRIPT = ROOT / "scripts/snapshot_lb_and_scorecards.py"
CONFIG_PATH = ROOT / "config" / "lb-scorecard-snapshot-preregistered.json"
REPORT_PATH = ROOT / "reports" / "lb-scorecard-snapshot.json"
METHODOLOGY = (ROOT / "docs/writeup/methodology.md").read_text(encoding="utf-8")
CLAIM_AUDIT = json.loads((ROOT / "reports/writeup-claim-evidence-audit.json").read_text())
CONFIG = json.loads(CONFIG_PATH.read_text())
REPORT = json.loads(REPORT_PATH.read_text())
SCORECARD_FIELDS = ("bias_indicator", "status", "disparity_ratio", "coverage_gap_percentage",
                    "coverage_gap_description", "reason")


def load_module():
    spec = importlib.util.spec_from_file_location("snapshot_lb", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_script(args: list[str]) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    return subprocess.run([UV, "run", "--quiet", "python", str(SCRIPT), *args], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=900, check=False,
                          env=environment)


def payload_of(stdout: str) -> dict:
    return json.JSONDecoder().raw_decode(stdout)[0]


def scorecard_files() -> list[Path]:
    return sorted(path for path in (ROOT / "reports/scorecards").glob("*.json")
                  if path.name != "index.json")


module = load_module()


class SnapshotTests(unittest.TestCase):
    def test_gates_all_pass_and_nothing_failed(self) -> None:
        self.assertTrue(REPORT["summary"]["gates_all_passed"], REPORT["summary"]["failed_gates"])
        self.assertEqual(REPORT["summary"]["failed_gates"], [])
        self.assertTrue(all(value is True for key, value in REPORT["gates"].items()))

    def test_snapshot_file_is_named_by_its_utc_timestamp(self) -> None:
        snapshot_utc = REPORT["summary"]["snapshot_utc"]
        self.assertRegex(snapshot_utc, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
        self.assertEqual(Path(REPORT["summary"]["snapshot_file"]).name,
                         module.filename_stamp(snapshot_utc) + ".json")
        self.assertTrue((ROOT / REPORT["summary"]["snapshot_file"]).is_file())
        self.assertEqual(sha256(ROOT / REPORT["summary"]["snapshot_file"]),
                         REPORT["summary"]["snapshot_sha256"])

    def test_pinned_ledger_snapshot_is_used_not_a_drifting_latest(self) -> None:
        self.assertEqual(REPORT["summary"]["snapshot_utc"], CONFIG["pinned_snapshot_utc"])
        self.assertEqual(REPORT["standings"]["our_best_public"], 0.091440198)
        self.assertEqual(REPORT["standings"]["our_rank"], 274)
        self.assertEqual(REPORT["standings"]["participants"], 1441)
        self.assertEqual(REPORT["standings"]["ranked_teams"], 390)

    def test_top20_table_is_ranked_and_non_decreasing(self) -> None:
        rows = REPORT["top20_public_rmse"]
        self.assertEqual(len(rows), CONFIG["top_n"])
        self.assertEqual([row["rank"] for row in rows], list(range(1, CONFIG["top_n"] + 1)))
        scores = [row["public_rmse"] for row in rows]
        self.assertEqual(scores, sorted(scores))
        self.assertTrue(all(row["team_handle"] is None or isinstance(row["team_handle"], str)
                            for row in rows))

    def test_gap_to_top10_boundary_is_our_best_minus_the_rank_ten_score(self) -> None:
        our_best = REPORT["standings"]["our_best_public"]
        rank_10 = REPORT["standings"]["rank_10"]
        gap = REPORT["gaps"]["our_best_minus_rank_10"]
        self.assertAlmostEqual(gap, our_best - rank_10, delta=1e-12)
        self.assertEqual(REPORT["summary"]["gap_to_top10_boundary"], gap)
        self.assertGreater(gap, 0.0)
        self.assertEqual(REPORT["standings"]["perfect_scores"], 6)
        self.assertEqual(REPORT["gaps"]["best_positive_score"], 9.1526e-05)

    def test_every_our_submission_is_listed_with_its_public_score_and_scorecard(self) -> None:
        rows = REPORT["our_submissions_in_the_public_column"]
        self.assertEqual(sorted(row["submission_id"] for row in rows),
                         sorted(CONFIG["required_scorecard_submission_ids"]))
        for row in rows:
            self.assertTrue(row["scorecard_available"], row)
            self.assertGreaterEqual(row["teams_strictly_better"], 0)
            self.assertLessEqual(row["teams_strictly_better"] + row["teams_at_or_worse"],
                                 REPORT["standings"]["ranked_teams"])


class ScorecardTests(unittest.TestCase):
    def test_every_required_submission_has_a_hash_pinned_scorecard(self) -> None:
        index = json.loads((ROOT / "reports/scorecards/index.json").read_text())
        self.assertEqual(sorted(index["required_submission_ids"]),
                         sorted(CONFIG["required_scorecard_submission_ids"]))
        self.assertTrue(set(index["required_submission_ids"]).issubset(
            set(index["scorecards_present"])))
        self.assertGreaterEqual(len(index["successful_scorecards_present"]),
                                CONFIG["minimum_scorecards"])
        for row in index["scorecards"]:
            self.assertEqual(sha256(ROOT / "reports/scorecards" / row["file"]), row["sha256"],
                             row["file"])
            self.assertTrue(row["ledger_scores_agree"] is None or row["ledger_scores_agree"] is True)

    def test_each_scorecard_carries_the_full_organiser_rubric(self) -> None:
        for path in scorecard_files():
            document = json.loads(path.read_text())
            self.assertEqual(len(document["scorecard"]), 9, path.name)
            for dimension, values in document["scorecard"].items():
                self.assertEqual(sorted(values), sorted(SCORECARD_FIELDS), f"{path}:{dimension}")
            self.assertEqual(document["derived"]["dimensions_total"], 9, path.name)
            self.assertEqual(document["preregistration"]["http_methods_used"], ["GET"], path.name)
            self.assertEqual(document["provenance"]["platform_html_scorecard_available"], False)

    def test_all_zero_anchor_scorecard_is_undefined_by_the_organisers(self) -> None:
        document = json.loads((ROOT / "reports/scorecards/uRRw9KpB.json").read_text())
        self.assertEqual(document["derived"]["dimensions_status_undefined"], 9)
        self.assertEqual(document["derived"]["dimensions_status_ok"], 0)
        reasons = {values["reason"] for values in document["scorecard"].values()}
        self.assertEqual(reasons, {"Reference group has zero mean gap; ratio is undefined."})

    def test_scale_shrink_left_the_equity_profile_identical(self) -> None:
        c1 = json.loads((ROOT / "reports/scorecards/BvyKrR6v.json").read_text())
        p4 = json.loads((ROOT / "reports/scorecards/ySXi73rd.json").read_text())
        self.assertEqual(c1["scorecard"], p4["scorecard"])
        self.assertNotEqual(c1["submission"]["public_score"], p4["submission"]["public_score"])
        self.assertEqual(p4["derived"]["dimensions_flagged_not_similar"],
                         ["heat_summer", "tribal_vs_nontribal"])
        self.assertEqual(p4["scorecard"]["heat_summer"]["disparity_ratio"], 1.141)
        self.assertEqual(p4["scorecard"]["tribal_vs_nontribal"]["disparity_ratio"], 0.705)

    def test_component_estimator_is_flagged_on_rural_tribal_and_wildfire(self) -> None:
        document = json.loads((ROOT / "reports/scorecards/rTARuUaG.json").read_text())
        flagged = document["derived"]["dimensions_flagged_not_similar"]
        for dimension in ("urban_rural", "tribal_vs_nontribal", "wildfire", "heat_summer"):
            self.assertIn(dimension, flagged)
        self.assertLess(document["scorecard"]["urban_rural"]["disparity_ratio"], 0.3)
        self.assertLess(document["scorecard"]["tribal_vs_nontribal"]["disparity_ratio"], 0.6)


class SecrecyAndPassivityTests(unittest.TestCase):
    def test_no_secret_shaped_value_in_any_committed_output(self) -> None:
        paths = [REPORT_PATH, ROOT / "reports/leaderboard/index.json",
                 ROOT / "reports/scorecards/index.json"] + scorecard_files()
        paths += [Path(REPORT["summary"]["snapshot_file"])]
        paths = [path if path.is_absolute() else ROOT / path for path in paths]
        for path in set(paths):
            text = path.read_text(encoding="utf-8")
            for pattern in module.SECRET_PATTERNS:
                self.assertIsNone(re.search(pattern, text), f"{path.name}:{pattern}")
            keys: set[str] = set()

            def collect(node: object) -> None:
                if isinstance(node, dict):
                    keys.update(node)
                    for child in node.values():
                        collect(child)
                elif isinstance(node, list):
                    for child in node:
                        collect(child)

            collect(json.loads(text))
            self.assertNotIn("url", keys, f"{path.name} must not store the presigned URL field")

    def test_the_auth_token_never_reaches_a_committed_file(self) -> None:
        token_file = os.environ.get("ZINDI_AUTH_TOKEN_FILE")
        if not token_file or not Path(token_file).is_file():
            self.skipTest("no Zindi auth token file in this environment")
        token = Path(token_file).read_text(encoding="utf-8").strip()
        self.assertGreater(len(token), 10)
        for path in scorecard_files() + [REPORT_PATH, ROOT / "reports/scorecards/index.json",
                                         Path(REPORT["summary"]["snapshot_file"])]:
            path = path if path.is_absolute() else ROOT / path
            self.assertNotIn(token, path.read_text(encoding="utf-8"), path.name)

    def test_script_has_no_upload_or_post_path(self) -> None:
        self.assertFalse(module.post_indicators_present())
        source = SCRIPT.read_text()
        self.assertNotIn("data=", source.replace("data=body", ""))
        self.assertIn('"http_methods_used": ["GET"]', source)

    def test_ledger_is_never_written(self) -> None:
        ledger = module.ledger_root(None)
        before = {path: sha256(path) for path in list((ledger / "leaderboards").rglob("*.json.gz"))[:3]
                  if path.is_file()}
        before_events = sha256(ledger / "leaderboard-events.jsonl")
        with tempfile.TemporaryDirectory() as folder:
            completed = run_script(["--out", f"{folder}/report.json"])
            self.assertEqual(completed.returncode, 0, completed.stdout[-400:])
        self.assertEqual(sha256(ledger / "leaderboard-events.jsonl"), before_events)
        for path, digest in before.items():
            self.assertEqual(sha256(path), digest, path)

    def test_scorecard_never_influenced_a_model_parameter(self) -> None:
        self.assertEqual(REPORT["non_influence"]["scorecard_influenced_parameters"], [])
        self.assertTrue(REPORT["non_influence"]["no_upload_performed"])
        self.assertTrue(REPORT["gates"]["scorecard_never_influenced_parameters"])


class DeterminismTests(unittest.TestCase):
    def test_two_runs_are_byte_identical_and_match_the_committed_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            digests = []
            for index in (1, 2):
                completed = run_script(["--out", f"{folder}/report{index}.json"])
                self.assertEqual(completed.returncode, 0, completed.stdout[-400:])
                digests.append(sha256(Path(folder) / f"report{index}.json"))
            self.assertEqual(digests[0], digests[1])
            self.assertEqual(digests[0], sha256(REPORT_PATH))

    def test_check_only_accepts_the_committed_artefacts(self) -> None:
        completed = run_script(["--check-only"])
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("unchanged", completed.stdout)

    def test_serialises_no_unexpected_wall_clock_value(self) -> None:
        self.assertTrue(REPORT["gates"]["report_serialises_no_unexpected_wall_clock"])
        self.assertEqual(module.wall_clock_hits({key: value for key, value in REPORT.items()
                                                 if key != "preregistration"}), [])


class NegativeControlTests(unittest.TestCase):
    CASES = {
        "inject_secret": "no_secrets_in_committed_outputs",
        "secret_in_snapshot": "no_secrets_in_committed_outputs",
        "scorecard_field_dropped": "scorecard_fields_complete_per_dimension",
        "rank_without_timestamp": "snapshot_carries_utc_timestamp",
    }

    def sandbox(self, folder: str) -> list[str]:
        """A copy of the committed scorecards so a tamper run never touches the repo."""
        scorecards = Path(folder) / "scorecards"
        leaderboard = Path(folder) / "leaderboard"
        scorecards.mkdir(parents=True, exist_ok=True)
        leaderboard.mkdir(parents=True, exist_ok=True)
        for path in scorecard_files() + [ROOT / "reports/scorecards/index.json"]:
            shutil.copy2(path, scorecards / path.name)
        return ["--scorecards-dir", str(scorecards), "--leaderboard-dir", str(leaderboard)]

    def test_every_negative_control_fires(self) -> None:
        committed_before = {path: sha256(path) for path in scorecard_files()}
        with tempfile.TemporaryDirectory() as folder:
            for case, gate in self.CASES.items():
                completed = run_script(["--tamper", case, "--out", f"{folder}/{case}.json",
                                       *self.sandbox(folder)])
                self.assertNotEqual(completed.returncode, 0, case)
                payload = payload_of(completed.stdout)
                self.assertIn(gate, payload["failed_gates"], f"{case}: {payload['failed_gates']}")
        for path, digest in committed_before.items():
            self.assertEqual(sha256(path), digest, f"tamper run mutated {path}")

    def test_unmutated_control_passes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            completed = run_script(["--out", f"{folder}/clean.json", *self.sandbox(folder)])
            self.assertEqual(completed.returncode, 0, completed.stdout[-400:])
            self.assertTrue(payload_of(completed.stdout)["gates_all_passed"])

    def test_unknown_tamper_case_is_rejected(self) -> None:
        completed = run_script(["--tamper", "make_it_pass"])
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid choice", completed.stderr)

    def test_missing_pinned_event_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config = dict(CONFIG, pinned_snapshot_utc="2020-01-01T00:00:00+00:00")
            path = Path(folder) / "config.json"
            path.write_text(json.dumps(config))
            completed = run_script(["--config", str(path), "--out", f"{folder}/report.json",
                                   *self.sandbox(folder)])
            self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
            self.assertIn("BLOCKED", completed.stderr)


class WriteupReflectionTests(unittest.TestCase):
    def test_methodology_carries_a_scorecard_reflection(self) -> None:
        self.assertIn("## Scorecard reflection", METHODOLOGY)
        block = METHODOLOGY.split("## Scorecard reflection", 1)[1]
        for fragment in ("0.091440", "0.007553", "0.083887", "0.091011", "0.000429", "1.141",
                         "0.705", "0.248", "0.556", "0.621", "rank-10", "median"):
            self.assertIn(fragment, block, fragment)
        self.assertIn("reflection only", block)
        self.assertIn("no_upload_performed", block)
        self.assertIn("scorecard_influenced_parameters", block)

    def test_reflection_numbers_stay_fully_traceable(self) -> None:
        self.assertEqual(CLAIM_AUDIT["summary"]["fraction_quantitative_claims_traceable"], 1.0)
        self.assertEqual(CLAIM_AUDIT["summary"]["fraction_numeric_tokens_covered"], 1.0)
        sections = {claim["section"] for claim in CLAIM_AUDIT["claims"]}
        self.assertIn("Scorecard reflection", sections)

    def test_reflection_does_not_claim_a_target_or_a_service_outcome(self) -> None:
        block = METHODOLOGY.split("## Scorecard reflection", 1)[1]
        lowered = block.lower()
        for forbidden in ("hidden target", "the organiser scores", "dispatch failures occurred",
                          "evacuation", "saves lives"):
            self.assertNotIn(forbidden, lowered, forbidden)


if __name__ == "__main__":
    unittest.main()
