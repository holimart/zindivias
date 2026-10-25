"""Tests for the Best Bias Discovery prize criteria audit.

Row: bias-discovery-prize-criteria-audit. The audit is a documentation screen: it must cover every
discovery section, judge all four organiser criteria mechanically, verify every evidence pointer,
and fail closed when the narrative over-claims.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UV = "/home/martin/hddProjects/langywrap/uv"
SCRIPT = ROOT / "scripts/audit_discovery_prize_criteria.py"
CONFIG_PATH = ROOT / "config/discovery-prize-criteria-preregistered.json"
REPORT_PATH = ROOT / "reports/discovery-prize-criteria-audit.json"
MARKDOWN_PATH = ROOT / "docs/writeup/bias-prize-criteria-audit.md"
DOC_PATH = ROOT / "docs/writeup/bias-discovery.md"
CRITERIA = ("impact", "novelty", "evidence", "reproducibility")


def load_module():
    spec = importlib.util.spec_from_file_location("audit_dpc", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_audit(args: list[str]) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment.setdefault("COMPETITION_WORKSPACE",
                           str(Path(os.environ["HOME"]) / "competitionsall-workspace"
                               / "zindi-bias-bounty"))
    return subprocess.run([UV, "run", "--quiet", "python", str(SCRIPT), *args], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=1800, check=False,
                          env=environment)


CONFIG = json.loads(CONFIG_PATH.read_text())
REPORT = json.loads(REPORT_PATH.read_text())
MARKDOWN = MARKDOWN_PATH.read_text(encoding="utf-8")
DOC = DOC_PATH.read_text(encoding="utf-8")
module = load_module()


class CoverageTests(unittest.TestCase):
    def test_every_document_section_is_covered(self) -> None:
        aliases = {alias.lower() for section in CONFIG["sections"]
                   for group in section["criterion_blocks"].values() for alias in group}
        top_level = [entry for entry in module.headings(DOC)
                     if entry["level"] <= 2
                     and not any(entry["title"].lower().startswith(alias) for alias in aliases)]
        covered = {section["heading"] for section in CONFIG["sections"]}
        self.assertEqual([entry["title"] for entry in top_level if entry["title"] not in covered],
                         [])
        self.assertEqual(REPORT["measured"]["uncovered_headings"], [])
        self.assertEqual(REPORT["measured"]["sections_configured"],
                         REPORT["measured"]["sections_covered"])
        self.assertGreaterEqual(REPORT["measured"]["sections_covered"], 4)

    def test_all_four_criteria_are_judged_for_every_section(self) -> None:
        for section in REPORT["sections"]:
            self.assertEqual(set(section["criteria"]), set(CRITERIA), section["id"])
            for key in CRITERIA:
                self.assertIn(section["criteria"][key]["verdict"], ("pass", "needs_revision"))
        self.assertEqual(REPORT["measured"]["criteria_evaluated"],
                         REPORT["measured"]["sections_covered"] * 4)

    def test_every_section_is_supported_or_explicitly_marked(self) -> None:
        self.assertEqual(REPORT["measured"]["sections_unresolved"], 0)
        for section in REPORT["sections"]:
            self.assertIn(section["resolution"], ("supported", "draft", "removed"))
            if section["declared_status"] == "draft":
                self.assertTrue(section["draft_marker_present"] or section["status_marker_present"])
                self.assertTrue(set(section["criteria_needed"]).issubset(
                    set(CONFIG["section_expectations"]["draft_allowed_criteria_to_need_revision"])))
            else:
                self.assertEqual(section["criteria_needed"], [])

    def test_criteria_weights_match_the_organiser_rules(self) -> None:
        weights = [criterion["weight_percent"] for criterion in CONFIG["criteria"]]
        self.assertEqual(weights, [30, 25, 25, 20])
        self.assertEqual(sum(weights), 100)
        self.assertTrue(REPORT["gates"]["criteria_weights_match_organiser_rules"])


class EvidenceTests(unittest.TestCase):
    def test_every_pointer_resolves_and_matches_its_pinned_hash(self) -> None:
        total = 0
        for section in REPORT["sections"]:
            for pointer in section["pointers"]:
                total += 1
                self.assertTrue(pointer["resolved"], pointer)
                self.assertIsNot(pointer["sha256_matches"], False, pointer)
                if pointer["pinned_sha256"]:
                    self.assertEqual(sha256(ROOT / pointer["path"]), pointer["pinned_sha256"],
                                     pointer["path"])
        self.assertEqual(total, REPORT["measured"]["evidence_pointers_total"])
        self.assertEqual(total, REPORT["measured"]["evidence_pointers_verified"])

    def test_pointer_hash_drift_is_detected(self) -> None:
        pointer = dict(CONFIG["sections"][0]["pointers"][0], sha256="0" * 64)
        entry = module.check_pointer(pointer, module.workspace_root())
        self.assertIs(entry["sha256_matches"], False)
        missing = module.check_pointer({"path": "reports/not-a-real-report.json",
                                        "pointer": None, "sha256": None},
                                       module.workspace_root())
        self.assertIs(missing["resolved"], False)
        bad = module.check_pointer({"path": "reports/discovery-postfire.json",
                                    "pointer": "/no/such/key", "sha256": None},
                                   module.workspace_root())
        self.assertIs(bad["resolved"], False)
        self.assertIn("pointer error", bad["reason"])

    def test_section_claims_partition_the_document_and_are_traceable(self) -> None:
        claims = json.loads((ROOT / CONFIG["claim_inventory"]).read_text())["claims"]
        doc_name = Path(CONFIG["discovery_document"]).name
        in_doc = [claim for claim in claims if claim["doc"].endswith(doc_name)]
        assigned = [claim_id for section in REPORT["sections"] for claim_id in section["claim_ids"]]
        self.assertEqual(sorted(assigned), sorted(claim["claim_id"] for claim in in_doc))
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(REPORT["measured"]["quantitative_claims_traceable"],
                         REPORT["measured"]["quantitative_claims_in_sections"])
        self.assertTrue(REPORT["gates"]["claim_audit_reports_full_traceability"])

    def test_sampled_commands_are_the_inventory_verify_commands_and_exit_zero(self) -> None:
        claims = {claim["claim_id"]: claim
                  for claim in json.loads((ROOT / CONFIG["claim_inventory"]).read_text())["claims"]}
        self.assertEqual(len(REPORT["execution"]), len(CONFIG["sections"]))
        for section_config in CONFIG["sections"]:
            sample = REPORT["execution"][section_config["id"]]
            self.assertEqual(sample["exit_code"], 0, sample)
            self.assertEqual(sample["command"],
                             claims[section_config["sampled_claim_id"]]["verify_command"])


class ClaimDisciplineTests(unittest.TestCase):
    def test_no_hidden_target_or_unqualified_operational_claims(self) -> None:
        self.assertEqual(REPORT["measured"]["hidden_target_claim_patterns_found"], [])
        self.assertEqual(REPORT["measured"]["unqualified_operational_patterns_found"], [])
        self.assertTrue(REPORT["gates"]["zero_proxy_as_organiser_target_claims"])
        self.assertTrue(REPORT["gates"]["no_unqualified_operational_impact"])

    def test_negated_hidden_target_sentence_is_not_a_violation(self) -> None:
        text = ("An all-region comparison can be mistaken for a disparity; these are plausible "
                "risks, not evidence of a true coverage gap.")
        hits = [pattern for pattern in CONFIG["hidden_target_claim_regexes"]
                if re.search(pattern, text, re.IGNORECASE)]
        self.assertTrue(hits)
        negated = all(any(marker in sentence.lower()
                          for marker in CONFIG["hidden_target_negation_markers"])
                      for sentence in re.split(r"(?<=[.!?])\s+", text)
                      if re.search("|".join(CONFIG["hidden_target_claim_regexes"]), sentence,
                                   re.IGNORECASE))
        self.assertTrue(negated)

    def test_impact_blocks_always_carry_a_hedge_and_a_consequence(self) -> None:
        for section in REPORT["sections"]:
            metrics = section["criteria"]["impact"]["metrics"]
            self.assertTrue(metrics.get("hedges"), section["id"])
            self.assertTrue(metrics.get("consequence_terms"), section["id"])
            self.assertTrue(metrics.get("named_geoids") or metrics.get("named_regions"),
                            section["id"])

    def test_no_external_dataset_is_referenced_without_a_record(self) -> None:
        urls = re.findall(CONFIG["external_data_policy"]["url_regex"], DOC)
        self.assertEqual(sorted(set(urls) - set(CONFIG["external_data_policy"]["allowlist"])), [])
        self.assertTrue(REPORT["gates"]["zero_unreferenced_external_datasets"])

    def test_prohibited_sources_are_absent_from_scripts_and_disclosed_in_prose(self) -> None:
        self.assertEqual(REPORT["measured"]["prohibited_token_hits_in_scripts"], [])
        self.assertEqual(REPORT["measured"]["prohibited_token_hits_in_prose"], [])

    def test_novelty_is_judged_against_a_named_scorecard_dimension(self) -> None:
        for section in REPORT["sections"]:
            metrics = section["criteria"]["novelty"]["metrics"]
            self.assertTrue(metrics.get("scorecard_dimensions_named"), section["id"])
            self.assertTrue(metrics.get("beyond_grid_markers"), section["id"])


class DeterminismTests(unittest.TestCase):
    def test_report_and_markdown_are_byte_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "a"
            second = Path(folder) / "b"
            for target in (first, second):
                completed = run_audit(["--out", str(target / "report.json"),
                                       "--markdown", str(target / "audit.md")])
                self.assertEqual(completed.returncode, 0, completed.stdout[-400:])
            self.assertEqual(sha256(first / "report.json"), sha256(second / "report.json"))
            self.assertEqual(sha256(first / "audit.md"), sha256(second / "audit.md"))
            self.assertEqual(sha256(first / "report.json"), sha256(REPORT_PATH))
            self.assertEqual(sha256(first / "audit.md"), sha256(MARKDOWN_PATH))

    def test_serialises_no_wall_clock_value(self) -> None:
        self.assertTrue(REPORT["gates"]["report_serialises_no_wall_clock"])
        self.assertEqual(module.wall_clock_hits(REPORT), [])
        self.assertIsNone(re.search(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\b",
                                    MARKDOWN_PATH.read_text(encoding="utf-8")))

    def test_check_only_accepts_the_committed_artefacts(self) -> None:
        completed = run_audit(["--check-only"])
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("unchanged", completed.stdout)


class NegativeControlTests(unittest.TestCase):
    CASES = {
        "drop_impact_hedge": "no_unqualified_operational_impact",
        "invent_external_url": "zero_unreferenced_external_datasets",
        "fake_pointer_hash": "every_evidence_pointer_reproduces",
        "untraceable_section_claim": "every_section_claim_traceable",
        "unqualified_operational_impact": "no_unqualified_operational_impact",
        "proxy_asserted_as_organiser_target": "zero_proxy_as_organiser_target_claims",
        "missing_reproduce_command": "every_section_supported_or_marked",
        "sampled_command_fails": "sampled_verify_commands_exit_zero",
    }

    def test_every_negative_control_fires(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            for case, gate in self.CASES.items():
                execute = case == "sampled_command_fails"
                arguments = ["--tamper", case, "--out", f"{folder}/{case}.json",
                             "--markdown", f"{folder}/{case}.md"]
                if not execute:
                    arguments.append("--no-execute")
                completed = run_audit(arguments)
                payload = json.JSONDecoder().raw_decode(completed.stdout)[0]
                self.assertIn(gate, payload["failed_gates"], f"{case}: {payload['failed_gates']}")
                self.assertFalse(payload["gates_all_passed"], case)

    def test_unmutated_control_passes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            completed = run_audit(["--out", f"{folder}/clean.json",
                                   "--markdown", f"{folder}/clean.md"])
            self.assertEqual(completed.returncode, 0, completed.stdout[-500:])
            self.assertTrue(json.JSONDecoder().raw_decode(completed.stdout)[0]["gates_all_passed"])

    def test_unknown_tamper_case_is_rejected(self) -> None:
        completed = run_audit(["--tamper", "make_it_pass"])
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid choice", completed.stderr)


class MarkdownDeliverableTests(unittest.TestCase):
    def test_markdown_covers_matrix_pointers_and_recommendations(self) -> None:
        self.assertIn("# Best Bias Discovery prize criteria audit", MARKDOWN)
        for criterion in CONFIG["criteria"]:
            self.assertIn(criterion["label"], MARKDOWN)
            self.assertIn(f"{criterion['weight_percent']}%", MARKDOWN)
        for section in REPORT["sections"]:
            self.assertIn(section["title"], MARKDOWN)
            self.assertIn(f"### {section['title']}", MARKDOWN)
            for pointer in section["pointers"]:
                self.assertIn(pointer["path"], MARKDOWN)
        self.assertIn("needs revision", MARKDOWN)
        self.assertIn("Draft for operator review", MARKDOWN)
        self.assertIn("hidden target", MARKDOWN.lower())

    def test_markdown_states_the_non_claims(self) -> None:
        self.assertIn("No public dataset beyond the challenge package", MARKDOWN)
        self.assertIn("does not", MARKDOWN)
        self.assertIn("documentation screen", MARKDOWN)


if __name__ == "__main__":
    unittest.main()
