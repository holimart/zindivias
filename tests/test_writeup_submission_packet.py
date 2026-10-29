"""Tests for the assembled Bias Bounty writeup submission packet.

Row: bias-writeup-form-submission. The packet is staged for operator review only; these tests
also prove the checker fails closed when the packet lies about anything the operator owns.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


builder = _module("build_final_writeup")
verify = _module("verify_writeup_submission")

CONFIG = json.loads((ROOT / "config/writeup-submission-preregistered.json").read_text())
RECORD = json.loads((ROOT / "reports/writeup-submission.json").read_text())
MARKDOWN = (ROOT / "docs/writeup/final-writeup.md").read_text(encoding="utf-8")
OPERATOR_GATES = {"operator_identity_confirmation_recorded", "form_url_available",
                  "submission_confirmation_recorded"}


class PacketPresenceTests(unittest.TestCase):
    def test_protocol_final_two_matches_report_and_ui_confirmation_stays_pending(self) -> None:
        final = json.loads((ROOT / "reports/final-two.json").read_text())
        slots = {item["slot"]: item for item in RECORD["final_two"]["protocol_selected"]}
        self.assertEqual(slots["A"]["candidate_id"], "V2_model_cgs")
        self.assertEqual(slots["A"]["submission_id"], "hvLtPVmr")
        self.assertEqual(slots["A"]["public_rmse"], 0.11102814)
        self.assertEqual(slots["B"]["candidate_id"], "B_from_A_cgs")
        self.assertEqual(slots["B"]["submission_id"], "eAaWXWxM")
        self.assertEqual(slots["B"]["public_rmse"], 0.097535343)
        self.assertEqual(RECORD["final_two"]["selection_trace"],
                         final["corrected_cgs_selection"])
        self.assertTrue(RECORD["final_two"]["operator_confirmation_pending"])
        self.assertEqual(RECORD["final_two"]["selected"],
                         {"candidate_A": None, "candidate_B": None})
        self.assertEqual(len(RECORD["operator_decision_inputs_pending"]), 4)
        for item in slots.values():
            self.assertIn(item["submission_id"], MARKDOWN)
            self.assertIn(item["sha256"], MARKDOWN)

    def test_markdown_and_pdf_exist_and_are_hash_recorded(self) -> None:
        self.assertTrue((ROOT / "docs/writeup/final-writeup.md").is_file())
        pdf = ROOT / "reports/writeup/final-writeup.pdf"
        self.assertTrue(pdf.is_file())
        self.assertEqual(pdf.read_bytes()[:5], b"%PDF-")
        self.assertGreater((ROOT / "docs/writeup/final-writeup.md").stat().st_size, 40000)
        for item in RECORD["writeup_files"]:
            path = ROOT / item["path"]
            self.assertTrue(path.is_file(), item["path"])
            self.assertEqual(verify.sha256_file(path), item["sha256"])
            self.assertEqual(path.stat().st_size, item["bytes"])

    def test_status_is_staged_and_no_submission_is_claimed(self) -> None:
        self.assertEqual(RECORD["status"], "prepared_pending_operator_review")
        self.assertFalse(RECORD["ready_for_submission"])
        for key in ("submitted_at_utc", "confirmation_reference", "submitted_by",
                    "form_url_used"):
            self.assertIsNone(RECORD["submission_record"][key])
        self.assertEqual(RECORD["submission_record"]["operator_review"], "not recorded")
        self.assertNotIn("form submitted", MARKDOWN.lower())

    def test_operator_and_disclosed_claim_audit_gates_are_open(self) -> None:
        open_gates = {key for key, value in RECORD["acceptance"].items() if value is not True}
        self.assertEqual(open_gates, OPERATOR_GATES | {"all_passed"})
        self.assertIn("full claim audit does not pass", MARKDOWN.lower())


class RulesContentTests(unittest.TestCase):
    def test_both_prizes_amounts_and_sections_are_stated(self) -> None:
        for prize in CONFIG["prizes"]:
            self.assertIn(prize["name"], MARKDOWN)
            self.assertIn(f"${prize['amount_usd']:,}", MARKDOWN)
            self.assertIn(prize["section_label"], MARKDOWN)

    def test_discovery_criteria_weights_and_tie_break_are_quoted(self) -> None:
        discovery = next(prize for prize in CONFIG["prizes"] if prize["name"]
                         == "Best Bias Discovery")
        for name, weight in discovery["criteria_weights"].items():
            self.assertIn(f"{name}:", MARKDOWN)
            self.assertIn(f"{weight}%", MARKDOWN)
        self.assertIn(discovery["tie_break_quote"], MARKDOWN)

    def test_consideration_statement_is_verbatim(self) -> None:
        self.assertIn(CONFIG["consideration_requested"]["stated_verbatim_in_writeup"], MARKDOWN)

    def test_identity_and_form_url_stay_uninvented(self) -> None:
        self.assertIsNone(RECORD["identity"]["zindi_username_or_team"])
        self.assertIsNone(RECORD["form"]["url"])
        self.assertIn(CONFIG["identity"]["pending_placeholder"], MARKDOWN)

    def test_model_candidate_is_declared_not_uploaded(self) -> None:
        self.assertIn(CONFIG["model_evidence"]["upload_status_quote"], MARKDOWN)
        self.assertIn(CONFIG["model_evidence"]["format_fix_candidate_id"], MARKDOWN)
        self.assertIn(CONFIG["model_evidence"]["format_fix_sha256"], MARKDOWN)
        status = RECORD["model_candidate"]["upload_status"]
        self.assertFalse(status["uploaded"])
        self.assertFalse(status["live_upload_attempted"])
        self.assertEqual(status["uploads_used_for_this_candidate"], 0)


class ProvenanceTieTests(unittest.TestCase):
    def test_associated_submissions_tie_to_successful_uploads_and_registry(self) -> None:
        rows = [json.loads(line) for line in
                (ROOT / "reports/submissions.jsonl").read_text(encoding="utf-8").splitlines()]
        successful = {(row["platform_submission_id"], row["candidate_id"], row["sha256"])
                      for row in rows if row.get("platform_submission_id")
                      and row.get("status") == "successful"}
        registry = {card["candidate_id"]: card for card in
                    json.loads((ROOT / "reports/candidates.json").read_text())["candidate_set"]}
        self.assertTrue(RECORD["associated_leaderboard_submissions"])
        for row in RECORD["associated_leaderboard_submissions"]:
            self.assertIn((row["platform_submission_id"], row["candidate_id"],
                           row["artifact_sha256"]), successful)
            self.assertEqual(row["registry_sha256"], registry[row["candidate_id"]]["sha256"])

    def test_model_candidate_hash_is_pinned_in_three_places(self) -> None:
        candidate = RECORD["model_candidate"]
        registry = {card["candidate_id"]: card for card in
                    json.loads((ROOT / "reports/candidates.json").read_text())["candidate_set"]}
        audit = json.loads((ROOT / "reports/estimator-v2-audit.json").read_text())
        self.assertEqual(candidate["sha256"], CONFIG["model_evidence"]["frozen_candidate_sha256"])
        self.assertEqual(candidate["sha256"], registry["V2_model"]["sha256"])
        self.assertEqual(candidate["sha256"], audit["artifact"]["sha256"])

    def test_external_register_is_complete_and_dated(self) -> None:
        register = RECORD["external_source_register"]
        self.assertGreaterEqual(len(register), 10)
        for row in register:
            self.assertTrue(row["url"].startswith("http"))
            self.assertRegex(row["accessed_on"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(row["role"])
        registered = {row["url"] for row in register}
        self.assertTrue({url.rstrip('.,;:)\'"')
                         for url in verify.URL_PATTERN.findall(MARKDOWN)} <= registered)
        literature = (ROOT / "docs/literature/completeness-prior-art.md").read_text()
        self.assertTrue({url.rstrip('.,;:)\'"')
                         for url in verify.URL_PATTERN.findall(literature)} <= registered)

    def test_no_retired_leaderboard_constant_appears_anywhere(self) -> None:
        haystack = MARKDOWN + json.dumps(RECORD)
        prohibited = CONFIG["prohibited_content"]
        for value in prohibited["retired_leaderboard_constant_values"]:
            self.assertNotIn(value, haystack)
        for name in prohibited["retired_leaderboard_constant_names"]:
            self.assertNotIn(name, haystack)

    def test_record_serializes_no_wall_clock_key(self) -> None:
        stack = [RECORD]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, child in node.items():
                    self.assertFalse(any(word in key.lower() for word in
                                         verify.FORBIDDEN_KEY_WORDS), key)
                    stack.append(child)
            elif isinstance(node, list):
                stack.extend(node)


class BuildTests(unittest.TestCase):
    def test_assembly_is_deterministic(self) -> None:
        builder.CONFIG = builder.load_config()
        first, _ = builder.assemble(builder.CONFIG)
        second, _ = builder.assemble(builder.CONFIG)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 40000)

    def test_claim_audit_figures_in_the_record_match_the_audit_report(self) -> None:
        summary = json.loads((ROOT / "reports/writeup-claim-evidence-audit.json")
                             .read_text())["summary"]
        recorded = RECORD["claim_evidence_audit"]
        for key in ("claims_total", "claims_fully_traceable",
                    "fraction_quantitative_claims_traceable", "numeric_tokens_total",
                    "numeric_tokens_covered"):
            self.assertEqual(recorded[key], summary[key])
        self.assertEqual(summary["fraction_quantitative_claims_traceable"], 1.0)
        self.assertEqual(summary["numeric_tokens_covered"], summary["numeric_tokens_total"])
        self.assertEqual(json.loads((ROOT / "reports/writeup-claim-evidence-audit.json")
                                    .read_text())["unresolved"], [])


class NegativeControlTests(unittest.TestCase):
    """A tampered packet must be rejected, so the gates are not decorative."""

    def check(self, mutate) -> list[str]:
        record = copy.deepcopy(RECORD)
        mutate(record)
        return verify.check(record, CONFIG)["failures"]

    def test_unmutated_packet_passes(self) -> None:
        self.assertEqual(verify.check(copy.deepcopy(RECORD), CONFIG)["failures"], [])

    def test_forged_submission_confirmation_is_rejected(self) -> None:
        def mutate(record):
            record["submission_record"]["submitted_at_utc"] = "2026-10-26T00:00:00Z"
            record["submission_record"]["confirmation_reference"] = "made-up"
        self.assertTrue(self.check(mutate))

    def test_ready_flag_flip_is_rejected(self) -> None:
        self.assertTrue(self.check(lambda record: record.__setitem__("ready_for_submission", True)))

    def test_invented_username_without_confirmation_is_rejected(self) -> None:
        self.assertTrue(self.check(lambda record: record["identity"].__setitem__(
            "zindi_username_or_team", "some-team-name")))

    def test_tampered_submission_id_is_rejected(self) -> None:
        def mutate(record):
            record["associated_leaderboard_submissions"][0]["platform_submission_id"] = "ZZZZZZZZ"
        self.assertTrue(any("submission id" in item for item in self.check(mutate)))

    def test_tampered_model_candidate_hash_is_rejected(self) -> None:
        def mutate(record):
            record["model_candidate"]["sha256"] = "f" * 64
        failures = self.check(mutate)
        self.assertTrue(any("model candidate" in item for item in failures))

    def test_undated_external_source_is_rejected(self) -> None:
        def mutate(record):
            record["external_source_register"][0]["accessed_on"] = ""
        self.assertTrue(any("register row" in item for item in self.check(mutate)))

    def test_injected_retired_constant_is_rejected(self) -> None:
        def mutate(record):
            record["notes"] = CONFIG["prohibited_content"]["retired_leaderboard_constant_values"][0]
        self.assertTrue(any("retired leaderboard constant" in item for item in self.check(mutate)))

    def test_disagreement_with_the_claim_audit_is_rejected(self) -> None:
        def mutate(record):
            record["claim_evidence_audit"]["numeric_tokens_covered"] = 1
        self.assertTrue(any("claim audit" in item for item in self.check(mutate)))

    def test_broken_record_hash_is_rejected(self) -> None:
        def mutate(record):
            record["writeup_files"][0]["sha256"] = "0" * 64
        self.assertTrue(any("sha256 does not match" in item for item in self.check(mutate)))


class OperatorFillTests(unittest.TestCase):
    """The operator can complete the packet with one input file; partial input is rejected."""

    INPUTS = {
        "identity": {"zindi_username_or_team": "operator-supplied-team",
                     "operator_confirmation_recorded": True},
        "form": {"url": "https://forms.example.org/bias-bounty-writeup"},
        "submission_record": {"submitted_at_utc": "2026-10-26T14:05:00Z",
                              "confirmation_reference": "organiser reply #BB-4417",
                              "submitted_by": "operator-handle",
                              "form_url_used": "https://forms.example.org/bias-bounty-writeup",
                              "operator_review": "discovery narrative reviewed and approved"},
        "final_two": {"selected": {"candidate_A": "V2_model_cgs",
                                     "candidate_B": "B_from_A_cgs"}},
    }

    def packet(self, inputs: dict, tmp: Path):
        builder.CONFIG = builder.load_config()
        merged = builder.apply_operator_inputs(builder.CONFIG, inputs)
        markdown, facts = builder.assemble(merged)
        markdown_path = tmp / "final-writeup.md"
        pdf_path = tmp / "final-writeup.pdf"
        markdown_path.write_text(markdown, encoding="utf-8")
        pdf_path.write_bytes(b"%PDF-1.4\nunit-test stub\n%%EOF\n")
        preliminary = builder.build_record(merged, facts, markdown_path, pdf_path)
        bundle = builder.build_bundle(merged, preliminary, markdown_path, pdf_path)
        bundle_path = tmp / "submission-bundle.json"
        bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        summary = {"path": str(bundle_path), "sha256": builder.sha256_file(bundle_path),
                   "bytes": bundle_path.stat().st_size, "bundle": bundle}
        return markdown, builder.build_record(merged, facts, markdown_path, pdf_path, summary)

    def test_full_operator_inputs_produce_a_confirmed_packet(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            markdown, record = self.packet(copy.deepcopy(self.INPUTS), Path(folder))
            self.assertEqual(record["status"], "submitted_confirmed_by_operator")
            self.assertTrue(record["ready_for_submission"])
            self.assertEqual(record["pending_operator_inputs"], [])
            self.assertEqual(record["blocking_prerequisites"], [])
            self.assertIn("operator-supplied-team", markdown)
            self.assertIn("submitted at 2026-10-26T14:05:00Z", markdown)
            self.assertIn("`V2_model_cgs` and `B_from_A_cgs`", markdown)
            result = verify.check(copy.deepcopy(record), CONFIG)
            self.assertEqual(result["failures"], [])

    def test_partial_operator_inputs_are_rejected(self) -> None:
        inputs = copy.deepcopy(self.INPUTS)
        del inputs["submission_record"]["confirmation_reference"]
        with tempfile.TemporaryDirectory() as folder:
            _, record = self.packet(inputs, Path(folder))
            self.assertEqual(record["status"], "prepared_pending_operator_review")
            self.assertFalse(record["ready_for_submission"])
            failures = "".join(verify.check(copy.deepcopy(record), CONFIG)["failures"])
            self.assertIn("submission confirmation must stay null", failures)
            self.assertIn("operator review may only be recorded by the operator", failures)

    def test_operator_input_file_cannot_touch_evidence_or_prizes(self) -> None:
        tampered = copy.deepcopy(self.INPUTS)
        tampered["prizes"] = [{"name": "Best Bias Discovery", "amount_usd": 99999}]
        tampered["writeup_files"] = [{"path": "x", "sha256": "0" * 64, "bytes": 1}]
        builder.CONFIG = builder.load_config()
        merged = builder.apply_operator_inputs(builder.CONFIG, tampered)
        self.assertEqual(merged["prizes"], CONFIG["prizes"])
        self.assertNotIn("writeup_files", merged)
        self.assertEqual(merged["identity"]["zindi_username_or_team"], "operator-supplied-team")

    def test_submission_bundle_is_hash_tied_and_copy_paste_ready(self) -> None:
        bundle = RECORD["submission_bundle"]
        path = ROOT / bundle["path"]
        self.assertTrue(path.is_file())
        self.assertEqual(builder.sha256_file(path), bundle["sha256"])
        self.assertEqual(path.stat().st_size, bundle["bytes"])
        content = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(content, bundle["bundle"])
        mirrors = {item["role"]: item for item in RECORD["writeup_files"]}
        for item in content["files"]:
            self.assertEqual(mirrors[item["role"]]["sha256"], item["sha256"])
            self.assertEqual(builder.sha256_file(ROOT / item["path"]), item["sha256"])
        blocks = content["paste_blocks"]
        self.assertIn(blocks["prize_request_line"], MARKDOWN)
        for line in blocks["submission_ids_block"].splitlines():
            self.assertIn(line.split("|")[1].strip(), MARKDOWN)
        self.assertIn("PENDING_OPERATOR_CONFIRMATION", blocks["zindi_identity_line"])
        self.assertIn("uploads used 0", blocks["model_candidate_block"])

    def test_submission_bundle_tampering_is_detected(self) -> None:
        tampered = copy.deepcopy(RECORD)
        tampered["submission_bundle"]["sha256"] = "0" * 64
        failures = "".join(verify.check(tampered, CONFIG)["failures"])
        self.assertIn("submission bundle sha256 does not match bytes on disk", failures)
        tampered = copy.deepcopy(RECORD)
        tampered["submission_bundle"]["bundle"]["paste_blocks"]["zindi_identity_line"] = (
            "Zindi username or team name: invented-handle")
        failures = "".join(verify.check(tampered, CONFIG)["failures"])
        self.assertIn("submission bundle content differs from the record", failures)

    def test_operator_inputs_template_is_empty_and_matches_the_whitelist(self) -> None:
        path = ROOT / CONFIG["assembly"]["operator_inputs_template"]
        self.assertTrue(path.is_file())
        template = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(template, RECORD["operator_inputs_template"])
        operator_fields = {key: value for key, value in template.items()
                           if key != "prepared_packet"}
        self.assertEqual(set(verify.flatten(operator_fields)),
                         set(CONFIG["operator_input_schema"]["fields"]))
        self.assertEqual(template["prepared_packet"], CONFIG["prepared_operator_packet"])
        self.assertEqual(template["identity"]["zindi_username_or_team"], None)
        self.assertIs(template["identity"]["operator_confirmation_recorded"], False)
        self.assertEqual(template["submission_record"]["operator_review"], "not recorded")
        self.assertEqual(builder.template_matches_whitelist(RECORD), True)
        final = json.loads((ROOT / "reports/final-two.json").read_text())
        slots = {item["slot"]: item for item in final["final_candidates"]}
        for slot, packet_key in (("A", "submission_A"), ("B", "submission_B")):
            prepared = template["prepared_packet"][packet_key]
            self.assertEqual(prepared["candidate_id"], slots[slot]["candidate_id"])
            self.assertEqual(prepared["submission_id"], slots[slot]["submission_id"])
            self.assertEqual(prepared["artifact_sha256"], slots[slot]["sha256"])
        packet_text = (ROOT / template["prepared_packet"]["markdown_path"]).read_text()
        self.assertIn(template["prepared_packet"]["form_url"], packet_text)
        self.assertIn(template["prepared_packet"]["writeup_pdf_sha256"], packet_text)
        self.assertIn("not submitted", packet_text.lower())
        self.assertIn("not selected", packet_text.lower())
        filled = copy.deepcopy(template)
        filled["identity"]["zindi_username_or_team"] = "sneaked-in"
        record = copy.deepcopy(RECORD)
        record["operator_inputs_template"] = filled
        self.assertEqual(builder.template_matches_whitelist(record), False)
        tampered = copy.deepcopy(RECORD)
        tampered["operator_inputs_template"]["prepared_packet"]["submission_A"]["submission_id"] = "wrong"
        failures = "".join(verify.check(tampered, CONFIG)["failures"])
        self.assertIn("prepared operator answer for final-two slot A differs", failures)

    def test_preflight_reports_missing_and_ignored_operator_keys(self) -> None:
        import io
        import contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = verify.preflight_operator_inputs(
                RECORD, CONFIG, ROOT / CONFIG["assembly"]["operator_inputs_template"])
        self.assertEqual(code, 0)
        report = json.JSONDecoder().raw_decode(buffer.getvalue())[0]
        self.assertFalse(report["would_report_submitted"])
        self.assertTrue(report["still_missing"])
        buffer = io.StringIO()
        inputs = copy.deepcopy(self.INPUTS)
        inputs["prizes"] = [{"amount_usd": 1}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "inputs.json"
            path.write_text(json.dumps(inputs), encoding="utf-8")
            with contextlib.redirect_stdout(buffer):
                verify.preflight_operator_inputs(RECORD, CONFIG, path)
        report = json.JSONDecoder().raw_decode(buffer.getvalue())[0]
        self.assertTrue(report["would_report_submitted"])
        self.assertEqual(report["ignored_non_whitelisted_keys"], ["prizes"])

    def test_official_page_extracts_and_form_url_search_are_recorded(self) -> None:
        page = RECORD["organiser_page"]
        self.assertTrue(page["url"].startswith("https://zindi.africa/"))
        self.assertRegex(page["documented_on"], r"^\d{4}-\d{2}-\d{2}$")
        for entry in page["extracts"]:
            self.assertIn(entry["quote"], MARKDOWN)
            self.assertTrue(entry["used_for"])
        registered = {row["url"] for row in RECORD["external_source_register"]}
        self.assertIn(page["url"], registered)
        for attempted in page["form_url_search"]["pages_attempted"]:
            self.assertIn(attempted, registered)
        self.assertIn(page["form_url_search"]["outcome"], MARKDOWN)
        self.assertIsNone(RECORD["form"]["url"])
        self.assertTrue(RECORD["operator_input_schema"]["fields"])


if __name__ == "__main__":
    unittest.main()
