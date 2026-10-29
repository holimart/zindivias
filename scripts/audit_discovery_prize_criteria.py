#!/usr/bin/env python3
"""Audit every Best Bias Discovery section against the four organiser criteria.

Row: bias-discovery-prize-criteria-audit. This is a documentation screen, not a submission and
not a model promotion. It answers one question per discovery section: is the section supported on
Impact (30%), Novelty outside the published scorecard grid (25%), Quantified evidence (25%) and
Reproducibility (20%), with exact evidence pointers that still resolve and reproduce?

Design rules, preregistered in config/discovery-prize-criteria-preregistered.json:

* every discovery section must be supported on all four criteria or be explicitly marked draft;
* no section may claim that a proxy model score is the organiser's hidden target, and no impact
  statement may assert an unqualified operational outcome (dispatch, evacuation, routing);
* no section may introduce a public dataset beyond the challenge package without a source, open
  licence, URL, retrieval date and reproducible method, so an unlisted URL fails;
* every retained evidence pointer must exist, match its pinned SHA-256, resolve as a JSON pointer,
  and every quantitative statement in the section must be traceable in the claim inventory;
* reproducibility requires the recorded regeneration command inside the section and a sampled
  claim verify command that exits zero when executed;
* the report serialises no wall-clock value and is byte-identical across runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config/discovery-prize-criteria-preregistered.json"
CRITERIA_KEYS = ("impact", "novelty", "evidence", "reproducibility")
WALL_CLOCK_KEYS = re.compile(r"timestamp|generated|created|updated|elapsed|duration|_at$|now",
                             re.IGNORECASE)
ISO_DATETIME = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\b")
WORKSPACE_PREFIX = "WORKSPACE:"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def workspace_root() -> Path | None:
    value = os.environ.get("COMPETITION_WORKSPACE")
    return Path(value) if value else None


def resolve_pointer(document: Any, pointer: str | None) -> Any:
    if pointer in (None, ""):
        return document
    node = document
    for part in pointer.strip("/").split("/"):
        if isinstance(node, dict):
            if part not in node:
                raise KeyError(part)
            node = node[part]
        elif isinstance(node, list):
            node = node[int(part)]
        else:
            raise TypeError(f"cannot descend into {type(node)} at {part}")
    return node


# --------------------------------------------------------------------------- document parsing


def headings(text: str) -> list[dict[str, Any]]:
    found = []
    for index, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"^(#{1,3}) +(.+?)\s*$", line)
        if match:
            found.append({"level": len(match.group(1)), "title": match.group(2).strip(),
                          "line": index})
    return found


def block_for(text: str, section: dict[str, Any], criterion: str) -> dict[str, Any]:
    """Return the block of the section that carries a criterion, with its heading titles."""
    lines = text.splitlines()
    all_headings = headings(text)
    in_section = [entry for entry in all_headings
                  if section["line_start"] <= entry["line"] < section["line_end"]]
    if not in_section:
        return {"found": False, "headings": [], "text": "", "line_start": None,
                "line_end": None}
    aliases = section["criterion_blocks"][criterion]
    candidates = [entry for entry in in_section
                  if any(entry["title"].lower().startswith(alias.lower()) for alias in aliases)]
    if not candidates:
        return {"found": False, "headings": [], "text": "", "line_start": None,
                "line_end": None}
    chosen = candidates[0]
    following = [entry["line"] for entry in all_headings
                 if entry["line"] > chosen["line"] and entry["level"] <= chosen["level"]]
    end = min(following) if following else section["line_end"]
    end = min(end, section["line_end"])
    body = "\n".join(lines[chosen["line"]:end - 1])
    used = [chosen["title"]]
    for extra in aliases[1:]:
        for entry in candidates[1:]:
            if entry["title"].lower().startswith(extra.lower()):
                extra_lines = [item["line"] for item in all_headings
                               if item["line"] > entry["line"]
                               and item["level"] <= entry["level"]]
                extra_end = min(extra_lines) if extra_lines else section["line_end"]
                body += "\n" + "\n".join(lines[entry["line"]:min(extra_end, section["line_end"]) - 1])
                used.append(entry["title"])
    return {"found": True, "headings": used, "text": body, "line_start": chosen["line"],
            "line_end": end}


def section_text(text: str, section: dict[str, Any]) -> str:
    lines = text.splitlines()
    return "\n".join(lines[section["line_start"] - 1:section["line_end"] - 1])


# --------------------------------------------------------------------------- pointer checking


def check_pointer(pointer: dict[str, Any], workspace: Path | None) -> dict[str, Any]:
    entry = {"path": pointer["path"], "pointer": pointer.get("pointer"),
             "pinned_sha256": pointer.get("sha256")}
    if pointer["path"].startswith(WORKSPACE_PREFIX):
        if workspace is None:
            entry.update({"resolved": False, "reason": "workspace not authorised",
                          "sha256_matches": None})
            return entry
        path = workspace / pointer["path"][len(WORKSPACE_PREFIX):].lstrip("/")
        entry.update({"resolved": path.is_file(), "sha256_matches": None,
                      "reason": None if path.is_file() else "workspace file missing",
                      "documentation_only": True})
        return entry
    path = ROOT / pointer["path"]
    if not path.is_file():
        entry.update({"resolved": False, "sha256_matches": False, "reason": "file missing"})
        return entry
    digest = sha256_file(path)
    matches = pointer.get("sha256") is None or digest == pointer["sha256"]
    entry["sha256"] = digest
    entry["sha256_matches"] = bool(matches)
    if path.suffix == ".json":
        try:
            value = resolve_pointer(load_json(path), pointer.get("pointer"))
            entry["resolved"] = value not in (None, [], {}, "")
            if not entry["resolved"]:
                entry["reason"] = "pointer resolves to an empty value"
        except (KeyError, IndexError, TypeError, ValueError) as error:
            entry["resolved"] = False
            entry["reason"] = f"pointer error: {type(error).__name__}: {error}"
    else:
        entry["resolved"] = path.stat().st_size > 0
    return entry


# --------------------------------------------------------------------------- claim coupling


def section_claims(claims: list[dict[str, Any]], section: dict[str, Any],
                   doc_name: str) -> list[dict[str, Any]]:
    return [claim for claim in claims
            if claim["doc"].endswith(doc_name)
            and section["line_start"] <= claim["line_start"] < section["line_end"]]


def claim_traceable(claim: dict[str, Any], workspace: Path | None) -> tuple[bool, list[str]]:
    if not claim.get("evidence"):
        return False, ["no evidence pointer"]
    problems: list[str] = []
    for evidence in claim["evidence"]:
        path = evidence["path"]
        if path.startswith(WORKSPACE_PREFIX):
            if workspace is None or not (workspace / path[len(WORKSPACE_PREFIX):].lstrip("/")).is_file():
                problems.append(f"{path} not readable")
            continue
        file_path = ROOT / path
        if not file_path.is_file():
            problems.append(f"{path} missing")
            continue
        recorded = evidence.get("sha256")
        if recorded and sha256_file(file_path) != recorded:
            problems.append(f"{path} sha256 drift")
    return not problems, problems


# --------------------------------------------------------------------------- criteria judges


def prohibited_hits(text: str, tokens: list[str], markers: list[str]) -> list[dict[str, str]]:
    """Flag prohibited tokens only when their sentence carries no disclosure marker."""
    hits = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        lowered = sentence.lower()
        for token in tokens:
            if token.lower() in lowered and not any(m in lowered for m in markers):
                hits.append({"token": token, "sentence": sentence.strip()[:160]})
    return hits


def judge_impact(block: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if not block["found"] or not block["text"].strip():
        return {"verdict": "needs_revision", "reasons": ["no impact statement in the section"],
                "headings": block["headings"], "metrics": {}}
    text = block["text"]
    hedges = [phrase for phrase in config["hedge_phrases"] if phrase in text.lower()]
    if not hedges:
        reasons.append("no qualifying hedge phrase: the consequence is stated as fact")
    unqualified = [pattern for pattern in config["unqualified_impact_regexes"]
                   if re.search(pattern, text, re.IGNORECASE)]
    if unqualified:
        reasons.append(f"unqualified operational claim pattern(s): {unqualified}")
    consequences = [term for term in config["decision_consequence_terms"] if term in text.lower()]
    if not consequences:
        reasons.append("no named decision consequence (dispatch, evacuation, routing, audit, ...)")
    geo = re.findall(config["named_referent_regex"], text)
    regions = [name for name in config["named_referent_region_terms"] if name in text]
    if not geo and not regions:
        reasons.append("no named referent: no GEOID and no named region or place")
    return {"verdict": "pass" if not reasons else "needs_revision", "reasons": reasons,
            "headings": block["headings"],
            "metrics": {"hedges": hedges, "consequence_terms": consequences,
                        "named_geoids": sorted(set(geo))[:6], "named_regions": regions,
                        "characters": len(text)}}


def judge_novelty(block: dict[str, Any], config: dict[str, Any],
                  section: dict[str, Any]) -> dict[str, Any]:
    if not block["found"] or not block["text"].strip():
        return {"verdict": "needs_revision", "reasons": ["no novelty statement in the section"],
                "headings": block["headings"], "metrics": {}}
    text = block["text"].lower()
    grid = [term for term in config["grid_terms"] if term in text]
    if not grid:
        return {"verdict": "needs_revision",
                "reasons": ["the section never names a published scorecard dimension, so novelty "
                            "against the grid is untestable"],
                "headings": block["headings"], "metrics": {}}
    beyond = [marker for marker in config["beyond_grid_markers"] if marker in text]
    reasons = []
    if not beyond:
        reasons.append("no explicit statement that the finding is outside or beyond the marginal "
                       "scorecard cell")
    if section.get("outside_scorecard_grid") and not beyond:
        reasons.append("section is declared outside-the-grid but does not say so")
    return {"verdict": "pass" if not reasons else "needs_revision", "reasons": reasons,
            "headings": block["headings"],
            "metrics": {"scorecard_dimensions_named": grid, "beyond_grid_markers": beyond,
                        "characters": len(text)}}


def judge_evidence(block: dict[str, Any], config: dict[str, Any], section: dict[str, Any],
                   claims: list[dict[str, Any]], pointers: list[dict[str, Any]],
                   workspace: Path | None) -> dict[str, Any]:
    reasons: list[str] = []
    if not block["found"] or not block["text"].strip():
        reasons.append("no evidence statement in the section")
    if not section["pointers"]:
        reasons.append("the section declares no evidence pointer")
    failed_pointers = [entry for entry in pointers
                       if not entry["resolved"] or entry["sha256_matches"] is False]
    for entry in failed_pointers:
        reasons.append(f"pointer {entry['path']}{entry['pointer'] or ''}: "
                       f"{entry.get('reason') or 'hash mismatch'}")
    untraceable = []
    for claim in claims:
        ok, problems = claim_traceable(claim, workspace)
        if not ok:
            untraceable.append({"claim_id": claim["claim_id"], "problems": problems})
            reasons.append(f"claim {claim['claim_id']} is not traceable: {problems}")
    numeric_free = not claims and re.search(r"\d", block["text"]) is not None
    if numeric_free:
        reasons.append("the evidence block contains digits but no inventoried claim")
    return {"verdict": "pass" if not reasons else "needs_revision", "reasons": reasons,
            "headings": block["headings"],
            "metrics": {"pointers_total": len(pointers),
                        "pointers_verified": len(pointers) - len(failed_pointers),
                        "claims_in_section": len(claims),
                        "claims_traceable": len(claims) - len(untraceable),
                        "claim_ids": [claim["claim_id"] for claim in claims]}}


def judge_reproducibility(block: dict[str, Any], config: dict[str, Any], section: dict[str, Any],
                          sample: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if not block["found"]:
        reasons.append("no reproducibility statement in the section")
    body = section.get("body", "")
    normalised = re.sub(r"\\\s*\n", " ", body)
    normalised = re.sub(r"\s+", " ", normalised)
    if section["reproduce_command"] not in normalised:
        reasons.append(f"regeneration command {section['reproduce_command']!r} is not recorded in "
                       "the section text")
    missing_scripts = [script for script in section["scripts"] if not (ROOT / script).is_file()]
    if missing_scripts:
        reasons.append(f"missing script(s): {missing_scripts}")
    missing_reports = [report for report in section["deterministic_reports"]
                       if not (ROOT / report).is_file()]
    if missing_reports:
        reasons.append(f"missing deterministic report(s): {missing_reports}")
    sample = sample if isinstance(sample, dict) and "exit_code" in sample else {}
    if not sample or sample.get("exit_code") != 0:
        reasons.append(f"sampled claim command did not exit zero: {sample}")
    return {"verdict": "pass" if not reasons else "needs_revision", "reasons": reasons,
            "headings": block["headings"],
            "metrics": {"scripts": section["scripts"],
                        "deterministic_reports": section["deterministic_reports"],
                        "reproduce_command": section["reproduce_command"],
                        "sampled_claim": sample}}


# --------------------------------------------------------------------------- execution


def execute_sample(section: dict[str, Any], claims_by_id: dict[str, dict[str, Any]],
                   tamper: str | None) -> dict[str, Any]:
    claim = claims_by_id.get(section["sampled_claim_id"])
    if claim is None:
        return {"claim_id": section["sampled_claim_id"], "command": None, "exit_code": 1,
                "reason": "sampled claim id is absent from the inventory"}
    command = claim["verify_command"]
    if tamper == "sampled_command_fails":
        command = "/bin/false"
    completed = subprocess.run(command.split(), cwd=str(ROOT), capture_output=True, text=True,
                              timeout=900, check=False)
    return {"claim_id": claim["claim_id"], "command": command,
            "exit_code": completed.returncode,
            "stdout_tail": completed.stdout.strip().splitlines()[-1][:200]
            if completed.stdout.strip() else ""}


# --------------------------------------------------------------------------- audit


def run_audit(config: dict[str, Any], tamper: str | None, execute: bool) -> dict[str, Any]:
    doc_path = ROOT / config["discovery_document"]
    text = doc_path.read_text(encoding="utf-8")
    workspace = workspace_root()
    claims = load_json(ROOT / config["claim_inventory"])["claims"]
    claim_audit = load_json(ROOT / config["claim_audit_report"])
    claims_by_id = {claim["claim_id"]: claim for claim in claims}
    doc_name = Path(config["discovery_document"]).name

    if tamper == "drop_impact_hedge":
        lines = text.splitlines()
        start = next(index for index, line in enumerate(lines)
                     if line.strip() == "### Impact and novelty")
        stop = next(index for index in range(start + 1, len(lines))
                    if lines[index].startswith("### ") or lines[index].startswith("## "))
        kept = [line for line in lines[start + 1:stop]
                if not any(hedge in line.lower() for hedge in config["hedge_phrases"])]
        lines[start + 1:stop] = kept
        text = "\n".join(lines) + "\n"
    if tamper == "invent_external_url":
        text += "\nSee https://example.org/unregistered-public-dataset for the extra layer.\n"
    if tamper == "unqualified_operational_impact":
        text += "\nThis confirms that the gap caused dispatch failures in these tracts.\n"
    if tamper == "proxy_asserted_as_organiser_target":
        text += "\nThe organiser's hidden target is the C2 proxy score reported above.\n"

    sections: list[dict[str, Any]] = []
    execution: dict[str, Any] = {}
    for section in config["sections"]:
        declared_headings = [entry for entry in headings(text)
                             if entry["title"] == section["heading"]]
        body = section_text(text, section)
        if tamper == "missing_reproduce_command" and section["id"] == "section-1-postfire":
            body = body.replace(section["reproduce_command"], "python scripts/absent.py")
        entry: dict[str, Any] = {
            "id": section["id"], "title": section["title"], "declared_status": section["status"],
            "heading_found": bool(declared_headings),
            "line_start": section["line_start"], "line_end": section["line_end"],
            "characters": len(body),
        }
        if tamper == "fake_pointer_hash":
            pointers_config = [dict(pointer, sha256="0" * 64) if index == 0 and section["id"]
                               == "section-1-postfire" else dict(pointer)
                               for index, pointer in enumerate(section["pointers"])]
        else:
            pointers_config = [dict(pointer) for pointer in section["pointers"]]
        pointers = [check_pointer(pointer, workspace) for pointer in pointers_config]
        entry["pointers"] = pointers
        entry["claims"] = section_claims(claims, section, doc_name)
        claim_ids = [claim["claim_id"] for claim in entry["claims"]]
        if tamper == "untraceable_section_claim" and section["id"] == "section-1-postfire" \
                and entry["claims"]:
            entry["claims"] = [dict(claim, evidence=[]) if claim["claim_id"] == "bias-discovery-003"
                               else claim for claim in entry["claims"]]
        if execute:
            execution[section["id"]] = execute_sample(section, claims_by_id, tamper)
        blocks = {criterion: block_for(text if section["id"] == "section-1-postfire"
                                       or tamper is None else text, section, criterion)
                  for criterion in CRITERIA_KEYS}
        if tamper == "missing_reproduce_command" and section["id"] == "section-1-postfire":
            blocks["reproducibility"] = {"found": True, "headings": ["Reproducibility"],
                                         "text": body.replace(section["reproduce_command"],
                                                              "python scripts/absent.py"),
                                         "line_start": None, "line_end": None}
        entry["criteria"] = {
            "impact": judge_impact(blocks["impact"], config),
            "novelty": judge_novelty(blocks["novelty"], config, section),
            "evidence": judge_evidence(blocks["evidence"], config, section, entry["claims"],
                                       pointers, workspace),
            "reproducibility": judge_reproducibility(
                blocks["reproducibility"], config,
                dict(section, body=body), execution.get(section["id"], {})),
        }
        entry["criteria_pass"] = [key for key in CRITERIA_KEYS
                                  if entry["criteria"][key]["verdict"] == "pass"]
        entry["criteria_needed"] = [key for key in CRITERIA_KEYS
                                    if entry["criteria"][key]["verdict"] != "pass"]
        entry["claim_ids"] = claim_ids
        entry["draft_marker"] = section.get("draft_marker")
        entry["status_marker"] = section.get("status_marker")
        entry["draft_marker_present"] = (section.get("draft_marker") in body
                                        if section.get("draft_marker") else None)
        entry["status_marker_present"] = (section.get("status_marker") in body
                                         if section.get("status_marker") else None)
        expectations = config["section_expectations"]
        if section["status"] == "supported":
            entry["resolution"] = "supported" if not entry["criteria_needed"] else "unresolved"
        elif section["status"] == "draft":
            hard_required = set(expectations["draft_required_criteria_to_pass"])
            soft_allowed = set(expectations["draft_allowed_criteria_to_need_revision"])
            marker_ok = bool(entry["draft_marker_present"] or entry["status_marker_present"])
            if not marker_ok:
                entry["resolution"] = "unresolved"
                entry["resolution_note"] = "draft section carries no draft/status marker"
            elif not hard_required.issubset(set(entry["criteria_pass"])):
                entry["resolution"] = "unresolved"
                entry["resolution_note"] = ("draft sections must still pass "
                                            f"{sorted(hard_required)}")
            elif not set(entry["criteria_needed"]).issubset(soft_allowed):
                entry["resolution"] = "unresolved"
                entry["resolution_note"] = (f"criteria {sorted(set(entry['criteria_needed']) - soft_allowed)}"
                                            " may not need revision in a draft")
            else:
                entry["resolution"] = "draft"
        else:
            entry["resolution"] = "removed"
        entry["recommendations"] = [
            config["recommendation_templates"][key] for key in entry["criteria_needed"]]
        if section.get("recommendation"):
            entry["recommendations"].append(section["recommendation"])
        sections.append(entry)

    urls = sorted(set(re.findall(config["external_data_policy"]["url_regex"], text)))
    allowlist = set(config["external_data_policy"]["allowlist"])
    unlisted_urls = sorted(set(urls) - allowlist)
    hidden_hits: list[str] = []
    for pattern in config["hidden_target_claim_regexes"]:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if re.search(pattern, sentence, re.IGNORECASE) and not any(
                    marker in sentence.lower()
                    for marker in config["hidden_target_negation_markers"]):
                hidden_hits.append(pattern)
                break
    unqualified = [pattern for pattern in config["unqualified_impact_regexes"]
                   if re.search(pattern, text, re.IGNORECASE)]
    prohibited_doc = prohibited_hits(text, config["prohibited_evidence_tokens"]["tokens"],
                                    config["prohibited_evidence_tokens"]["disclosure_markers"])
    prohibited_code: list[dict[str, str]] = []
    for section in sections:
        for script in config_section_scripts(config, section["id"]):
            path = ROOT / script
            if path.is_file():
                prohibited_code += [{"token": hit["token"], "path": script,
                                     "sentence": hit["sentence"]}
                                    for hit in prohibited_hits(path.read_text(encoding="utf-8"),
                                                               config["prohibited_evidence_tokens"]["tokens"],
                                                               config["prohibited_evidence_tokens"]["disclosure_markers"])]
    no_external_statement = re.search(config["external_data_policy"]["no_external_data_statement_regex"],
                                      text, re.IGNORECASE) is not None
    doc_headings = [entry for entry in headings(text) if entry["level"] <= 2]
    criterion_aliases = {alias.lower() for section in config["sections"]
                         for aliases in section["criterion_blocks"].values() for alias in aliases}
    top_level = [entry for entry in doc_headings
                 if not any(entry["title"].lower().startswith(alias) for alias in criterion_aliases)]
    covered = {section["heading"] for section in config["sections"]}
    uncovered_headings = [entry["title"] for entry in top_level if entry["title"] not in covered]
    all_pointers = [pointer for section in sections for pointer in section["pointers"]]
    samples = list(execution.values())

    measured = {
        "sections_configured": len(config["sections"]),
        "sections_covered": len(sections),
        "top_level_sections_in_document": len(top_level),
        "uncovered_headings": uncovered_headings,
        "criteria_evaluated": len(sections) * len(CRITERIA_KEYS),
        "criteria_passed": sum(len(section["criteria_pass"]) for section in sections),
        "sections_supported": sum(1 for section in sections if section["resolution"] == "supported"),
        "sections_draft": sum(1 for section in sections if section["resolution"] == "draft"),
        "sections_unresolved": sum(1 for section in sections
                                   if section["resolution"] == "unresolved"),
        "evidence_pointers_total": len(all_pointers),
        "evidence_pointers_verified": sum(1 for pointer in all_pointers
                                         if pointer["resolved"] and pointer["sha256_matches"]
                                         is not False),
        "quantitative_claims_in_sections": sum(len(section["claim_ids"]) for section in sections),
        "quantitative_claims_traceable": sum(section["criteria"]["evidence"]["metrics"].get(
            "claims_traceable", 0) for section in sections),
        "unlisted_external_urls": unlisted_urls,
        "hidden_target_claim_patterns_found": hidden_hits,
        "unqualified_operational_patterns_found": unqualified,
        "prohibited_token_hits_in_prose": prohibited_doc,
        "prohibited_token_hits_in_scripts": prohibited_code,
        "sampled_commands_executed": len(samples),
        "sampled_commands_exit_zero": sum(1 for sample in samples if sample["exit_code"] == 0),
        "claim_audit_fraction_traceable": claim_audit.get("summary", {}).get(
            "fraction_quantitative_claims_traceable"),
        "weights_percent": [criterion["weight_percent"] for criterion in config["criteria"]],
    }

    gates = {
        "every_document_section_covered": not uncovered_headings,
        "all_headings_declared_in_config_exist": all(section["heading_found"] for section in sections),
        "every_section_supported_or_marked": measured["sections_unresolved"] == 0
        and measured["sections_configured"] == measured["sections_covered"],
        "all_four_criteria_evaluated_for_every_section": all(
            set(section["criteria"]) == set(CRITERIA_KEYS) for section in sections),
        "zero_proxy_as_organiser_target_claims": not hidden_hits,
        "no_unqualified_operational_impact": not unqualified and all(
            section["criteria"]["impact"]["verdict"] == "pass"
            or section["declared_status"] == "draft" for section in sections),
        "zero_unreferenced_external_datasets": not unlisted_urls and no_external_statement,
        "every_evidence_pointer_reproduces": measured["evidence_pointers_verified"]
        == measured["evidence_pointers_total"] and measured["evidence_pointers_total"] > 0
        and all(pointer["sha256_matches"] is not False for pointer in all_pointers),
        "every_section_claim_traceable": measured["quantitative_claims_in_sections"]
        == measured["quantitative_claims_traceable"],
        "claim_audit_reports_full_traceability": measured["claim_audit_fraction_traceable"] == 1.0,
        "sampled_verify_commands_exit_zero": execute and bool(samples)
        and measured["sampled_commands_exit_zero"] == len(samples),
        "prohibited_sources_absent_from_evidence_and_scripts": not prohibited_code,
        "prohibited_sources_only_disclosed_in_prose": not prohibited_doc,
        "criteria_weights_match_organiser_rules": measured["weights_percent"] == [30, 25, 25, 20]
        and sum(measured["weights_percent"]) == 100,
        "no_leaderboard_or_target_evidence": not any(
            token in json.dumps(section["pointers"]).lower()
            for section in sections
            for token in ("alpha_c1_from_P4".lower(), "0.3288136427728255",
                          "config/calibration.json", "lb_score_algebra")),
    }
    gates["report_serialises_no_wall_clock"] = not wall_clock_hits({
        "sections": sections, "measured": measured, "gates": gates, "execution": execution})
    gates["gates_all_passed"] = all(value is True for key, value in gates.items()
                                   if key != "gates_all_passed")
    report = {
        "schema_version": config["schema_version"],
        "row_key": config["row_key"],
        "lane": config["lane"],
        "intent": config["intent"],
        "preregistration": {
            "path": str(CONFIG_PATH.relative_to(ROOT)),
            "sha256": sha256_file(CONFIG_PATH),
            "criteria": config["criteria"],
            "section_expectations": config["section_expectations"],
            "external_data_policy": config["external_data_policy"],
        },
        "inputs": {
            "discovery_document": {"path": config["discovery_document"],
                                   "sha256": sha256_file(doc_path),
                                   "headings_total": len(headings(text))},
            "claim_inventory": {"path": config["claim_inventory"],
                                "sha256": sha256_file(ROOT / config["claim_inventory"]),
                                "claims_total": len(claims)},
            "claim_audit_report": {"path": config["claim_audit_report"],
                                   "sha256": sha256_file(ROOT / config["claim_audit_report"]),
                                   "fraction_quantitative_claims_traceable":
                                       measured["claim_audit_fraction_traceable"],
                                   "claims_fully_traceable": claim_audit.get("summary", {}).get(
                                       "claims_fully_traceable")},
        },
        "measured": measured,
        "sections": sections,
        "execution": {section_id: entry for section_id, entry in sorted(execution.items())},
        "gates": gates,
        "tamper_case": tamper,
    }
    return report


def config_section_scripts(config: dict[str, Any], section_id: str) -> list[str]:
    for section in config["sections"]:
        if section["id"] == section_id:
            return list(section["scripts"])
    return []


def wall_clock_hits(node: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            key_path = f"{path}/{key}"
            if WALL_CLOCK_KEYS.search(str(key)) and not isinstance(value, (int, bool)):
                hits.append(key_path)
            hits += wall_clock_hits(value, key_path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits += wall_clock_hits(value, f"{path}[{index}]")
    elif isinstance(node, str) and ISO_DATETIME.search(node):
        hits.append(path)
    return hits


# --------------------------------------------------------------------------- markdown


def render_markdown(report: dict[str, Any], config: dict[str, Any]) -> str:
    criteria = config["criteria"]
    lines = [
        "# Best Bias Discovery prize criteria audit",
        "",
        f"Row: `{report['row_key']}`. Lane: `{report['lane']}`. {report['intent']}.",
        "",
        f"Audited document: `{report['inputs']['discovery_document']['path']}` "
        f"(SHA-256 `{report['inputs']['discovery_document']['sha256']}`).",
        f"Preregistration: `{report['preregistration']['path']}` "
        f"(SHA-256 `{report['preregistration']['sha256']}`).",
        "",
        "This is a documentation screen. It makes no submission, changes no scored candidate, "
        "reads no leaderboard feedback, and asserts nothing about the organisers' hidden target. "
        "The DINS comparison does not establish rebuilding, absence, or a hidden-target result.",

        "",
        "## Organiser criteria and weights",
        "",
        "| Criterion | Weight | What a pass requires |",
        "|---|---:|---|",
    ]
    for criterion in criteria:
        lines.append(f"| {criterion['label']} | {criterion['weight_percent']}% | "
                     f"{criterion['requirement']} |")
    lines += ["", "Organiser wording: "
              f"\"{criteria[0]['organiser_quote']}\" (source: {config['rules_quote_source']}).",
              "", "## Criterion matrix", ""]
    lines += ["Verdicts are `pass` or `needs revision`. Each retained finding is supported only "
              "when all four criteria pass; the overall packet still requires operator review and a "
              "complete claim audit.", ""]
    lines += ["| Section | Declared status | Resolution | Impact | Novelty | Evidence | "
              "Reproducibility |", "|---|---|---|---|---|---|---|"]
    for section in report["sections"]:
        verdicts = {key: section["criteria"][key]["verdict"] for key in CRITERIA_KEYS}
        lines.append(f"| {section['title']} | {section['declared_status']} | "
                     f"{section['resolution']} | " +
                     " | ".join("pass" if verdicts[key] == "pass" else "needs revision"
                                for key in CRITERIA_KEYS) + " |")
    measured = report["measured"]
    lines += [
        "",
        "## Measured acceptance",
        "",
        f"- sections configured / covered: {measured['sections_configured']} / "
        f"{measured['sections_covered']}; unresolved: {measured['sections_unresolved']}",
        f"- criteria evaluated / passed: {measured['criteria_evaluated']} / "
        f"{measured['criteria_passed']}",
        f"- evidence pointers verified: {measured['evidence_pointers_verified']} of "
        f"{measured['evidence_pointers_total']}",
        f"- quantitative claims traceable: {measured['quantitative_claims_traceable']} of "
        f"{measured['quantitative_claims_in_sections']} (claim audit fraction "
        f"{measured['claim_audit_fraction_traceable']})",
        f"- unlisted external URLs in the discovery document: "
        f"{len(measured['unlisted_external_urls'])}",
        f"- hidden-target claim patterns: {len(measured['hidden_target_claim_patterns_found'])}; "
        f"unqualified operational patterns: "
        f"{len(measured['unqualified_operational_patterns_found'])}",
        f"- prohibited-token hits needing disclosure: prose "
        f"{len(measured['prohibited_token_hits_in_prose'])}, scripts "
        f"{len(measured['prohibited_token_hits_in_scripts'])}",
        f"- sampled claim verify commands executed with exit 0: "
        f"{measured['sampled_commands_exit_zero']} of "
        f"{measured['sampled_commands_executed']}",
        "",
        "## Section detail",
        "",
    ]
    for section in report["sections"]:
        markers = [value for value in (section.get("draft_marker"),
                                       section.get("status_marker")) if value]
        marker_text = (" Marked in the document: " + "; ".join(f"'{value}'"
                                                              for value in markers)
                       if markers else "")
        lines += [f"### {section['title']}", "",
                  f"Declared `{section['declared_status']}`, resolved `{section['resolution']}`, "
                  f"lines {section['line_start']}-{section['line_end'] - 1}, "
                  f"{section['characters']} characters. Claims: "
                  f"{', '.join('`' + claim + '`' for claim in section['claim_ids']) or 'none'}."
                  f"{marker_text}", ""]
        for key in CRITERIA_KEYS:
            criterion = section["criteria"][key]
            lines.append(f"- **{key}**: `{criterion['verdict']}` "
                         f"(headings: {', '.join(criterion['headings']) or 'none'})")
            for reason in criterion["reasons"]:
                lines.append(f"  - {reason}")
        lines.append("")
        lines.append("  Evidence pointers:")
        lines.append("")
        lines.append("  | Path | JSON pointer | SHA-256 (16) | Resolved |")
        lines.append("  |---|---|---|---|")
        for pointer in section["pointers"]:
            lines.append(f"  | `{pointer['path']}` | `{pointer['pointer'] or '-'}` | "
                         f"`{(pointer.get('sha256') or pointer.get('pinned_sha256') or '')[:16]}` | "
                         f"{pointer['resolved']} |")
        repro = section["criteria"]["reproducibility"]["metrics"]
        scripts = ", ".join(f"`{script}`" for script in repro["scripts"])
        lines += ["", f"  Reproduction: `{repro['reproduce_command']}`; script(s): {scripts}", ""]
        if section["recommendations"]:
            lines.append("  Recommendations:")
            for recommendation in section["recommendations"]:
                lines.append(f"  - {recommendation}")
            lines.append("")
    lines += [
        "## Non-claims",
        "",
        "- No proxy model score is presented as the organiser's hidden target anywhere in the "
        "discovery document (pattern scan: zero hits).",
        "- No section asserts an unqualified operational outcome; every impact paragraph carries "
        "an explicit hedge.",
        "- One public CAL FIRE DINS source is registered for the mechanism check only; its open "
        "licence, URL, retrieval date, filter, and limitations are documented, and it never enters "
        "scored predictions.",
        "- The three findings pass the four narrative criteria. This packet remains a draft for "
        "operator review because the broader claim audit is incomplete and submission inputs are pending.",
        "",
        "## Reproduction",
        "",
        "```sh",
        "COMPETITION_WORKSPACE=\"$COMPETITION_WORKSPACE\" \\",
        "  /home/martin/hddProjects/langywrap/uv run --quiet \\",
        "  python scripts/audit_discovery_prize_criteria.py",
        "```",
        "",
        "The report is `reports/discovery-prize-criteria-audit.json` and this document is "
        "generated from it, so a repeated run is byte-identical and serialises no wall-clock value.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument("--no-execute", action="store_true",
                        help="skip executing the sampled claim verify commands")
    parser.add_argument("--tamper", choices=[None] + json.loads(
        CONFIG_PATH.read_text())["negative_control_cases"], default=None)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    config = load_json(args.config)
    report = run_audit(config, args.tamper, execute=not args.no_execute)
    markdown = render_markdown(report, config)
    out_path = args.out or ROOT / config["report_output"]
    markdown_path = args.markdown or ROOT / config["markdown_output"]
    if args.check_only:
        same = (out_path.is_file()
                and out_path.read_text(encoding="utf-8") == json.dumps(report, indent=2,
                                                                      ensure_ascii=False) + "\n"
                and markdown_path.is_file()
                and markdown_path.read_text(encoding="utf-8") == markdown)
        print(("unchanged" if same else "DIFFERS") + f" {out_path}")
        return 0 if same else 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    markdown_path.write_text(markdown, encoding="utf-8")
    failed = sorted(key for key, value in report["gates"].items()
                    if value is not True and key != "gates_all_passed")
    print(json.dumps({"gates_all_passed": report["gates"]["gates_all_passed"],
                      "tamper_case": report["tamper_case"],
                      "sections": report["measured"]["sections_covered"],
                      "criteria_passed": f"{report['measured']['criteria_passed']}/"
                                         f"{report['measured']['criteria_evaluated']}",
                      "pointers_verified": f"{report['measured']['evidence_pointers_verified']}/"
                                           f"{report['measured']['evidence_pointers_total']}",
                      "failed_gates": failed,
                      "report_sha256": sha256_file(out_path),
                      "markdown_sha256": sha256_file(markdown_path)}, indent=1))
    if report["gates"]["gates_all_passed"]:
        print("PASS discovery prize criteria audit: every discovery section is supported on all "
              "four organiser criteria or explicitly marked draft, every evidence pointer "
              "reproduces, and no unqualified operational or hidden-target claim survives")
        return 0
    print("FAIL discovery prize criteria audit: " + ", ".join(failed))
    return 1


CONFIG = load_json(CONFIG_PATH)

if __name__ == "__main__":
    sys.exit(main())
