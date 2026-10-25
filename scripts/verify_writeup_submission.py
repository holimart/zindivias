#!/usr/bin/env python3
"""Verify the assembled Bias Bounty writeup submission packet (row bias-writeup-form-submission).

The packet is staged for operator review; nothing has been sent to the organiser. This checker
proves the packet is internally consistent, hash-tied to committed artifacts, and honest about the
operator-owned inputs it is still missing (Zindi username/team, organiser form URL, submission
confirmation). It never fills those in itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config/writeup-submission-preregistered.json"
URL_PATTERN = re.compile(r"https?://[^\s<>\"'`|]+")
FORBIDDEN_KEY_WORDS = ("generated_at", "timestamp", "utcnow", "elapsed", "duration", "wall_clock")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def leaves(node: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(leaves(value, f"{prefix}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.update(leaves(value, f"{prefix}[{index}]"))
    else:
        out[prefix] = node
    return out


def check(record: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    notes: dict[str, Any] = {}

    def require(condition: bool, label: str) -> None:
        if not condition:
            failures.append(label)

    markdown_path = ROOT / config["assembly"]["output_markdown"]
    pdf_path = ROOT / config["assembly"]["output_pdf"]
    require(markdown_path.is_file(), "final writeup markdown missing")
    require(pdf_path.is_file() and pdf_path.read_bytes()[:5] == b"%PDF-", "final writeup pdf missing")
    text = markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else ""

    # -- recorded hashes must match the bytes on disk -----------------------------------------
    paths = {item["role"]: item["path"] for item in record.get("writeup_files", [])}
    require(len(paths) == len(record["writeup_files"]), "duplicate role in writeup_files")
    for role in ("writeup_markdown", "writeup_pdf"):
        entry = next((item for item in record["writeup_files"] if item["role"] == role), None)
        require(entry is not None, f"record missing {role} entry")
        if entry:
            resolved = ROOT / entry["path"]
            require(resolved.is_file(), f"{role} file missing at recorded path {entry['path']}")
            if resolved.is_file():
                require(sha256_file(resolved) == entry["sha256"],
                        f"{role} sha256 does not match bytes on disk")
                require(resolved.stat().st_size == entry["bytes"], f"{role} byte size mismatch")
            if not str(entry["path"]).startswith("/"):
                require((ROOT / config["assembly"]["output_markdown"]).is_file()
                        and (ROOT / config["assembly"]["output_pdf"]).is_file(),
                        "canonical writeup artefacts missing")
                require(entry["path"] == config["assembly"].get(
                    "output_markdown" if role == "writeup_markdown" else "output_pdf"),
                    f"{role} recorded path does not match the configured artefact path")
    require(markdown_path.is_file() and pdf_path.is_file(),
            "canonical writeup markdown or pdf missing")
    recorded_markdown = next((item for item in record["writeup_files"]
                              if item["role"] == "writeup_markdown"), None)
    if recorded_markdown and (ROOT / recorded_markdown["path"]).is_file():
        # content checks read the file the record actually pins
        text = (ROOT / recorded_markdown["path"]).read_text(encoding="utf-8")

    # -- nothing may be invented ---------------------------------------------------------------
    submission = record["submission_record"]
    submitted = record["status"] == "submitted_confirmed_by_operator"
    require(record["status"] in ("prepared_pending_operator_review",
                                 "submitted_confirmed_by_operator"), "unexpected packet status")
    if submitted:
        require(record["ready_for_submission"] is True, "a confirmed packet must claim readiness")
        require(all(submission.get(key) for key in ("submitted_at_utc", "confirmation_reference",
                                                   "submitted_by", "form_url_used")),
                "a confirmed packet must carry submission time, confirmation, submitter and URL")
        require(submission.get("operator_review") not in (None, "not recorded"),
                "a confirmed packet must record the operator review of the discovery narrative")
        require(bool(record["identity"].get("zindi_username_or_team"))
                and bool(record["identity"].get("operator_confirmation_recorded")),
                "a confirmed packet needs an operator-confirmed username")
        require(bool(record["form"].get("url")), "a confirmed packet needs the form URL")
        for slot in ("candidate_A", "candidate_B"):
            require(bool(record["final_two"]["selected"].get(slot)),
                    f"a confirmed packet must name the selected {slot}")
    else:
        require(record["ready_for_submission"] is False, "packet must not claim readiness")
        require(all(submission.get(key) is None for key in
                    ("submitted_at_utc", "confirmation_reference", "submitted_by",
                     "form_url_used")),
                "submission confirmation must stay null until the operator submits the form")
        require(submission.get("operator_review") in (None, "not recorded"),
                "operator review may only be recorded by the operator")
        require(record["identity"]["zindi_username_or_team"] is None
                or record["identity"]["operator_confirmation_recorded"],
                "a username may only appear with recorded operator confirmation")
        if record["identity"]["zindi_username_or_team"] is None:
            require(config["identity"]["pending_placeholder"] in text,
                    "pending placeholder must be stated for the unconfirmed identity")
            require("PENDING" in text, "pending marker missing from the writeup")
        require(not record["identity"].get("operator_confirmation_recorded"),
                "identity confirmation may only be recorded by the operator")
        require(bool(record.get("blocking_prerequisites")),
                "a staged packet must list its blocking prerequisites")
        search = record["organiser_page"]["form_url_search"]
        require(bool(search.get("attempted_on")) and bool(search.get("pages_attempted"))
                and search.get("outcome") in text,
                "a missing form URL must be documented with the retrieval attempt")
    for phrase in ("we submitted the form", "submitted our writeup through the form"):
        require(phrase not in text.lower(), f"writeup claims a form submission: {phrase!r}")

    # -- quoted official page text -------------------------------------------------------------
    page = record["organiser_page"]
    require(bool(page.get("url")) and bool(page.get("documented_on")),
            "official page citation lacks url or documented_on")
    require(page["url"] in {row["url"] for row in record["external_source_register"]},
            "official page url is missing from the external source register")
    for entry in page["extracts"]:
        require(entry["quote"] in text, f"official quote missing from writeup: {entry['quote'][:40]}")
        require(bool(entry["used_for"]), f"official quote lacks a use note: {entry['quote'][:40]}")
    require(bool(record["operator_input_schema"].get("fields")),
            "operator input schema must be recorded so filling is one command")

    # -- rules-driven content -------------------------------------------------------------------
    for prize in config["prizes"]:
        require(prize["name"] in text, f"prize not stated: {prize['name']}")
        require(f"${prize['amount_usd']:,}" in text, f"prize amount not stated: {prize['name']}")
        require(prize["section_label"] in text, f"section not labelled: {prize['section_label']}")
        for name, weight in (prize.get("criteria_weights") or {}).items():
            require(f"{name}:" in text and f"{weight}%" in text,
                    f"criteria weight missing: {prize['name']} {name}")
    require(config["consideration_requested"]["stated_verbatim_in_writeup"] in text,
            "prize consideration statement missing verbatim")
    require(config["model_evidence"]["upload_status_quote"] in text,
            "non-upload disclosure missing for the model candidate")

    # -- submission ids and candidate hashes tie to the committed logs --------------------------
    log_rows = [json.loads(line) for line in
                (ROOT / config["numeric_provenance"]["submission_log"])
                .read_text(encoding="utf-8").splitlines()]
    successful = {(row["platform_submission_id"], row["candidate_id"], row["sha256"])
                  for row in log_rows if row.get("platform_submission_id")
                  and row.get("status") == "successful"}
    declared = [(row["platform_submission_id"], row["candidate_id"], row["artifact_sha256"])
                for row in record["associated_leaderboard_submissions"]]
    require(bool(declared), "no associated leaderboard submissions recorded")
    for item in declared:
        require(item in successful, f"submission id not tied to a successful upload: {item[0]}")
    registry = {card["candidate_id"]: card for card in
                load_json(ROOT / config["numeric_provenance"]["candidates_registry"])
                ["candidate_set"]}
    for row in record["associated_leaderboard_submissions"]:
        card = registry.get(row["candidate_id"])
        require(card is not None, f"candidate not registered: {row['candidate_id']}")
        if card:
            require(row["registry_sha256"] == card["sha256"],
                    f"registry hash mismatch for {row['candidate_id']}")
    candidate = record["model_candidate"]
    card = registry.get(candidate["candidate_id"])
    require(card is not None and card["sha256"]
            == config["model_evidence"]["frozen_candidate_sha256"],
            "model candidate hash is not the pinned frozen artifact")
    require(candidate["sha256"] == card["sha256"], "model candidate hash disagrees with registry")
    build_audit = load_json(ROOT / "reports/estimator-v2-audit.json")
    require(build_audit.get("artifact", {}).get("sha256") == candidate["sha256"],
            "model candidate hash does not tie to the build audit")
    upload_status = candidate["upload_status"]
    status_fields = upload_status if isinstance(upload_status, dict) else {}
    require(status_fields.get("uploaded") is False
            and status_fields.get("live_upload_attempted") is False
            and status_fields.get("uploads_used_for_this_candidate") == 0,
            "model candidate upload status must record a completed non-upload")

    # -- external source register ----------------------------------------------------------------
    register = record["external_source_register"]
    require(bool(register), "external source register is empty")
    for row in register:
        require(bool(row.get("url")) and bool(row.get("accessed_on")) and bool(row.get("role")),
                f"register row lacks url/date/role: {row.get('url')}")
        require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row["accessed_on"])) is not None,
                f"register row date is not YYYY-MM-DD: {row.get('url')}")
    doc_urls = {match.rstrip(".,;:')]\"'") for match in URL_PATTERN.findall(text)}
    registered = {row["url"] for row in register}
    require(doc_urls <= registered, f"cited urls absent from register: {sorted(doc_urls - registered)}")
    literature = ROOT / config["external_sources"]["literature_source"]
    literature_urls = {match.rstrip(".,;:')]\"'") for match in URL_PATTERN.findall(
        literature.read_text(encoding="utf-8"))}
    require(literature_urls <= registered,
            f"prior art urls missing from register: {sorted(literature_urls - registered)}")

    # -- prohibited content ----------------------------------------------------------------------
    prohibited = config["prohibited_content"]
    haystack = text + json.dumps(record)
    for value in prohibited["retired_leaderboard_constant_values"]:
        require(value not in haystack, f"retired leaderboard constant value present: {value}")
    for name in prohibited["retired_leaderboard_constant_names"]:
        require(name not in haystack, f"retired leaderboard constant name present: {name}")
    claim_config = load_json(ROOT / "config/writeup-claim-evidence-preregistered.json")
    markers = set(prohibited["allowed_disclosure_markers"]) | set(
        claim_config.get("disclosure_markers", [])) | set(
        claim_config.get("prohibited_prose_exceptions", [])) | {
        "no ", "not ", "never", "without", "instead of", "rather than", "rejected",
        "avoid", "excluding", "replace", "replac", "historical", "v0", "removed"}
    for layer in prohibited["removed_reference_layers"]:
        for match in re.finditer(r"\b" + re.escape(layer) + r"\b", text, flags=re.IGNORECASE):
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            sentence = text[start:end if end != -1 else len(text)]
            window = sentence.lower()
            if not any(marker in window for marker in markers):
                failures.append(
                    f"removed reference layer used without disclosure: {layer} :: "
                    f"{sentence.strip()[:100]}")

    # -- claim-audit tie ---------------------------------------------------------------------------
    audit = load_json(ROOT / config["numeric_provenance"]["claim_audit_report"])["summary"]
    for key in ("claims_total", "claims_fully_traceable", "fraction_quantitative_claims_traceable",
                "numeric_tokens_total", "numeric_tokens_covered"):
        require(record["claim_evidence_audit"].get(key) == audit.get(key),
                f"claim audit figure disagrees with the audit report: {key}")
    require(audit["fraction_quantitative_claims_traceable"] == 1.0,
            "claim audit no longer shows 100% traceability")
    require(audit["numeric_tokens_total"] == audit["numeric_tokens_covered"],
            "claim audit no longer covers every numeric token")

    # -- provided-data-only tie ----------------------------------------------------------------------
    provenance = load_json(ROOT / config["external_sources"]["provided_data_evidence"])
    require(provenance["acceptance"]["prohibited_source_dependencies"] == 0,
            "provided-data audit reports prohibited dependencies")
    for key, expected in (("external_data_added_to_scored_predictions", False),
                          ("target_labels_read", False),
                          ("leaderboard_feedback_read_for_modeling", False),
                          ("sample_score_placeholder_columns_read", False)):
        require(provenance["prohibitions"].get(key) is expected,
                f"provided-data prohibition violated or unreported: {key}")
        require(record["external_data_policy"]["prohibitions"].get(key) is expected,
                f"packet does not report prohibition {key}")

    # -- no wall-clock serialized -----------------------------------------------------------------------
    for path in leaves(record):
        key = path.rsplit("/", 1)[-1].strip("[]0123456789")
        if any(word in key.lower() for word in FORBIDDEN_KEY_WORDS):
            failures.append(f"wall-clock-like key serialized: {path}")

    pending = [key for key, value in record["acceptance"].items()
               if value is not True and key != "all_passed"]
    notes = {"pending_operator_inputs": pending,
             "blocking_prerequisites": record["blocking_prerequisites"],
             "associated_submissions": len(declared),
             "external_sources": len(register),
             "model_candidate": candidate["candidate_id"],
             "model_candidate_sha256": candidate["sha256"][:16],
             "markdown_sha256": next(item["sha256"] for item in record["writeup_files"]
                                     if item["role"] == "writeup_markdown")[:16],
             "pdf_sha256": next(item["sha256"] for item in record["writeup_files"]
                                if item["role"] == "writeup_pdf")[:16]}
    return {"ok": not failures, "failures": sorted(failures), "notes": notes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--record", type=Path,
                        default=ROOT / "reports/writeup-submission.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    config = load_json(args.config)
    record = load_json(args.record)
    result = check(record, config)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(json.dumps({"ok": result["ok"], "failures": result["failures"],
                          **result["notes"]}, indent=1))
    if result["ok"]:
        if record["status"] == "submitted_confirmed_by_operator":
            print("PASS writeup submission packet: operator-confirmed submission record complete "
                  "(UTC time, organiser confirmation, submitter, form URL, named final-two "
                  "candidates) and hash-tied to the writeup artefacts")
        else:
            print("PASS writeup submission packet: assembled, hash-tied and honest about its "
                  "pending operator inputs; staged only, nothing was submitted to the organiser")
        return 0
    print("FAIL writeup submission packet: " + "; ".join(result["failures"][:8]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
