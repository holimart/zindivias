#!/usr/bin/env python3
"""Assemble the final Bias Bounty writeup packet for the organiser form.

Row: bias-writeup-claim-evidence-audit companion row bias-writeup-form-submission.

This build assembles and machine-validates the submission packet. It does not submit
anything: the row forbids submitting before the operator reviews the discovery narrative,
requires the operator to confirm the Zindi username, and the steward note says to deliver
the draft and mark the row blocked pending an operator decision. Identity, form URL and any
submission confirmation stay null rather than invented.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config/writeup-submission-preregistered.json"

URL_PATTERN = re.compile(r"https?://[^\s<>\"'`|]+")
OFFICIAL_HOSTS = ("https://api.zindi.world/", "https://www.zindi.africa/", "https://zindi.africa/")
_URL_TRAILING = ".,;:')]" + '"' + "'"


def normalize_url(raw: str) -> str:
    return raw.rstrip(_URL_TRAILING)





def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_or(path: Path, default: str) -> str:
    """Repo-relative path for a record, tolerating alternate output paths outside the repo."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        # alternate output path outside the repo: record it absolutely so the record stays
        # self-consistent (Path(ROOT) / absolute == absolute)
        return str(path)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def json_leaves(node: Any, prefix: str = "") -> dict[str, Any]:
    leaves: dict[str, Any] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            leaves.update(json_leaves(value, f"{prefix}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            leaves.update(json_leaves(value, f"{prefix}[{index}]"))
    else:
        leaves[prefix] = node
    return leaves


# --------------------------------------------------------------------------- sources
def final_two_report() -> dict[str, Any]:
    return load_json(ROOT / CONFIG["numeric_provenance"]["final_two"])


def successful_submissions() -> list[dict[str, Any]]:
    """Latest successful upload record per platform submission id, oldest id first."""
    latest: dict[str, dict[str, Any]] = {}
    for line in (ROOT / CONFIG["numeric_provenance"]["submission_log"]).read_text(
            encoding="utf-8").splitlines():
        record = json.loads(line)
        platform_id = record.get("platform_submission_id")
        if not platform_id:
            continue
        if record.get("status") != "successful":
            continue
        latest[platform_id] = record
    cards = {card["candidate_id"]: card
             for card in load_json(ROOT / CONFIG["numeric_provenance"]["candidates_registry"])
             ["candidate_set"]}
    final = {item["slot"]: item for item in final_two_report()["final_candidates"]}
    winner = final_two_report()["corrected_cgs_selection"]["winner"]
    rows = []
    for platform_id, record in latest.items():
        card = cards.get(record["candidate_id"], {})
        rows.append({"platform_submission_id": platform_id,
                     "candidate_id": record["candidate_id"],
                     "artifact": record["artifact"],
                     "artifact_sha256": record["sha256"],
                     "registry_sha256": card.get("sha256"),
                     "public_score": record.get("our_score"),
                     "score_source": record.get("score_source"),
                     "score_split": record.get("score_split", record.get("split")),
                     "uploaded_at_utc": record.get("timestamp_utc", record.get("upload_event_ts", record.get("ts"))),
                     "score_selection_status": (
                         "selected A by frozen corrected-order _cgs rule"
                         if record.get("candidate_id") == winner
                         else "pre-registered B companion; score excluded from A selection"
                         if record.get("candidate_id") == final["B"]["candidate_id"]
                         else "corrected-order _cgs A-rule cohort; not selected as A"
                         if str(record.get("candidate_id", "")).endswith("_cgs")
                         and record.get("candidate_id") != final["B"]["candidate_id"]
                         else "void for selection: five-column file had transport_gap in column 2"
                     ),
                     "card_phase": card.get("phase"),
                     "pre_registered": card.get("pre_registered")})
    rows.sort(key=lambda row: str(row["uploaded_at_utc"] or ""))
    return rows


def model_candidate() -> dict[str, Any]:
    wanted = CONFIG["model_evidence"]["frozen_candidate_id"]
    cards = load_json(ROOT / CONFIG["numeric_provenance"]["candidates_registry"])["candidate_set"]
    card = next(item for item in cards if item["candidate_id"] == wanted)
    audit = load_json(ROOT / "reports/estimator-v2-audit.json")
    return {"candidate_id": wanted, "artifact": card["artifact"], "sha256": card["sha256"],
            "rows": card["rows"], "registry_sha256_matches_card": card["sha256"]
            == CONFIG["model_evidence"]["frozen_candidate_sha256"],
            "upload_status": card.get("upload_status"),
            "predicted_public_rmse": card.get("parameters", {}).get("predicted_public_rmse"),
            "prediction_basis": card.get("parameters", {}).get("prediction_basis"),
            "build_commit": audit.get("reproducibility", {}).get("git_commit"),
            "artifact_sha256_in_build_audit": audit.get("artifact", {}).get("sha256") == card["sha256"]}


def _download_dates() -> dict[str, str]:
    manifest = Path(os.environ.get("COMPETITION_WORKSPACE", "")) / "download-manifest.json"
    dates: dict[str, str] = {}
    if manifest.is_file():
        for entry in json.loads(manifest.read_text(encoding="utf-8")).get("files", []):
            url = normalize_url(str(entry.get("source_url", "")))
            stamp = str(entry.get("downloaded_at", ""))
            if url and stamp:
                dates[url] = stamp[:10]
    return dates


def _label(line: str, raw: str) -> str:
    """Human-readable citation text for a register row, never containing a URL itself."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
    text = URL_PATTERN.sub("", text)
    text = re.sub(r"[*_`|]", "", text)
    text = re.sub(r"^\s*[-*\d.)\s]+", "", text).strip(" :-")
    return re.sub(r"\s{2,}", " ", text)[:120].strip()


def _literature_urls() -> set[str]:
    literature = ROOT / CONFIG["external_sources"]["literature_source"]
    return {normalize_url(raw) for raw in URL_PATTERN.findall(
        literature.read_text(encoding="utf-8"))}


def external_register(document_text: str) -> list[dict[str, Any]]:
    """Every external URL relevant to the writeup, classified, with the date it was accessed.

    Pools: published prior art in the literature brief, the official challenge endpoints the
    downloader used for provided data, and any URL cited in the assembled text itself.
    """
    literature = ROOT / CONFIG["external_sources"]["literature_source"]
    literature_date = git("log", "-1", "--format=%ad", "--date=format:%Y-%m-%d", "--",
                          str(literature.relative_to(ROOT)))
    writeup_date = git("log", "-1", "--format=%ad", "--date=format:%Y-%m-%d", "--",
                       "docs/writeup/methodology.md")
    official_dates = _download_dates()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(url: str, label: str, role: str, accessed_on: str, official: bool) -> None:
        if url in seen:
            return
        seen.add(url)
        rows.append({"url": url, "label": label[:120] or url, "accessed_on": accessed_on,
                     "role": role, "official": official})

    for line in literature.read_text(encoding="utf-8").splitlines():
        for raw in URL_PATTERN.findall(line):
            add(normalize_url(raw), _label(line, raw), CONFIG["external_sources"]["role"],
                literature_date, False)
    for url, accessed_on in sorted(official_dates.items()):
        add(url, "organiser-hosted challenge file used by the workspace downloader",
            "official challenge data endpoint provided by the organisers; not an external dataset",
            accessed_on, True)
    page = CONFIG["organiser_page"]
    add(page["url"], "official challenge page (quoted rules and membership note)",
        page["role"], page["documented_on"], True)
    for source in CONFIG["external_sources"].get("additional_public_sources", []):
        url = normalize_url(source["url"])
        add(url, source["label"], source["role"], source["accessed_on"],
            url.startswith(OFFICIAL_HOSTS))
    for source in CONFIG["external_sources"].get("required_external_layers", []):
        add(normalize_url(source["url"]), source["name"], source["role"],
            source["accessed_on"], False)
        if source.get("documentation_url"):
            add(normalize_url(source["documentation_url"]),
                source.get("documentation_label", source["name"] + " documentation"),
                source["role"], source["accessed_on"], False)
    for line in document_text.splitlines():
        for raw in URL_PATTERN.findall(line):
            url = normalize_url(raw)
            label = _label(line, raw)
            official = url.startswith(OFFICIAL_HOSTS)
            add(url, label,
                "official challenge data or platform endpoint" if official
                else CONFIG["external_sources"]["role"],
                official_dates.get(url, literature_date), official)
    rows.sort(key=lambda row: (not row["official"], row["url"]))
    return rows


def shift_headings(text: str) -> str:
    lines = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,5})\s+(.*)$", line)
        if match:
            lines.append("#" * (len(match.group(1)) + 1) + " " + match.group(2))
        else:
            lines.append(line)
    return "\n".join(lines)


# --------------------------------------------------------------------------- sections
def front_matter(config: dict[str, Any], submissions: list[dict[str, Any]],
                 candidate: dict[str, Any]) -> list[str]:
    identity = config["identity"]
    final = final_two_report()
    protocol = {item["slot"]: item for item in final["final_candidates"]}
    lines = [
        "# Bias Bounty Mapping Equity Challenge — discovery and methodology",
        "",
        "## Submission context",
        "",
        f"- Protocol A: `{protocol['A']['candidate_id']}`; leaderboard submission `"
        f"{protocol['A']['submission_id']}`.",
        f"- Protocol B: `{protocol['B']['candidate_id']}`; leaderboard submission `"
        f"{protocol['B']['submission_id']}`.",
        config["consideration_requested"]["stated_verbatim_in_writeup"],
        "",
        "## Contents",
        "",
        "1. Best Bias Discovery — findings.",
        "2. Methodology and score interpretation.",
        "3. Appendix A: [detailed methodology](methodology.md).",
        "4. Appendix B: [edge cases](edge-cases.md).",
        "5. Appendix C: [supporting discovery screens](appendices/screens-that-did-not-produce-a-finding.md).",
        "6. [Supporting diagnostic archive](appendix-diagnostics.md).",
        "7. Appendices D–H: upload records, official criteria, external-layer compliance, reproducibility, and provenance.",
        "",
    ]
    if identity.get("zindi_username_or_team") and identity.get("operator_confirmation_recorded"):
        lines.insert(4, f"- Zindi username/team: **{identity['zindi_username_or_team']}**.")
    selected = (config.get("final_two") or {}).get("selected", {})
    if selected.get("candidate_A") and selected.get("candidate_B"):
        lines.insert(4, f"- Confirmed final-two candidates: `{selected['candidate_A']}` and `{selected['candidate_B']}`.")
    submitted_at = config.get("submission_record", {}).get("submitted_at_utc")
    if submitted_at and config.get("submission_record", {}).get("confirmation_reference"):
        lines.insert(4, f"- This writeup was submitted at {submitted_at}.")
    return lines


def submission_status_line(config: dict[str, Any]) -> str:
    record_submission = config.get("submission_record", {})
    submitted = record_submission.get("submitted_at_utc")
    confirmation = record_submission.get("confirmation_reference")
    if submitted and confirmation:
        return (f"submitted at {submitted} with organiser confirmation "
                f"'{confirmation}' by {record_submission.get('submitted_by') or 'operator'} "
                f"using {record_submission.get('form_url_used') or config['form']['url']}")
    return "NOT SUBMITTED — staged for operator submission"


def final_two_names(config: dict[str, Any]) -> str:
    selected = (config.get("final_two") or {}).get("selected") or {}
    first, second = selected.get("candidate_A"), selected.get("candidate_B")
    if first and second:
        return f"`{first}` and `{second}`"
    protocol = {item["slot"]: item["candidate_id"]
                for item in final_two_report()["final_candidates"]}
    if protocol.get("A") and protocol.get("B"):
        return f"`{protocol['A']}` and `{protocol['B']}` (protocol outcome; operator confirmation pending)"
    return "not selected yet (recorded as null rather than invented)"


def consideration(config: dict[str, Any]) -> list[str]:
    lines = ["## Appendix E: Prize consideration and official criteria", ""]
    request = config["consideration_requested"]
    lines += [request["stated_verbatim_in_writeup"], ""]
    for prize in config["prizes"]:
        amount = f"${prize['amount_usd']:,}"
        lines += [f"### {prize['section_label']} — {prize['name']} ({amount})", ""]
        lines += [f"- Prize: {prize['name']} ({amount}).",
                  f"- Official note: {prize['judge_quote']}"]
        weights = prize.get("criteria_weights")
        if weights:
            lines += ["- Judging criteria and weights (official text):"]
            lines += [f"  - {name}: {weight}%." for name, weight in weights.items()]
            lines += [f"- Tie-break: {prize.get('tie_break_quote', '')}"]
        lines += [""]
    return lines


def leaderboard_table(submissions: list[dict[str, Any]], candidate: dict[str, Any]) -> list[str]:
    lines = ["## Appendix D: Leaderboard submission records and score eligibility", "",
             "Historical five-column uploads placed `transport_gap` second, so their returned public values are "
             "preserved as records but void for A/B selection. The `_cgs` rows use `GEOID,coverage_gap_score`; "
             "the four cohort scores were used only by the frozen A rule, which selected the final A shown "
             "below; the B companion score was excluded from A selection. No public score is used to alter "
             "a candidate value.", "",
             "| Platform submission id | Candidate | Artifact | Artifact SHA-256 | Public RMSE "
             "| Split | Score source | Selection eligibility | Uploaded (UTC) |",
             "|---|---|---|---|---:|---|---|---|---|"]
    for row in submissions:
        lines.append("| {platform_submission_id} | {candidate_id} | `{artifact}` | "
                     "`{artifact_sha256}` | {score} | {split} | {source} | {eligibility} | {when} |".format(
                         **{**row,
                            "score": "n/a" if row["public_score"] is None
                            else f"{row['public_score']:.9g}",
                            "split": row["score_split"] or "n/a",
                            "source": row["score_source"] or "n/a",
                            "eligibility": row["score_selection_status"],
                            "when": row["uploaded_at_utc"] or "n/a"}))
    lines += ["",
              f"The scored methodology candidate in this writeup is `{candidate['candidate_id']}`, "
              f"built from provided features only (commit `{candidate['build_commit']}`). "
              f"{CONFIG['model_evidence']['upload_status_quote']}",
              "",
              "The earlier five-column uploads above are retained as historical calibration records for "
              "auditability. Their column-2 scores measured `transport_gap` and are void for selection; "
              "the correctly ordered `_cgs` evaluations are separately labeled, and no score value, fitted "
              "constant or probe outcome is used to change any candidate prediction or discovery claim.",
              ""]
    final = final_two_report()
    a = next(item for item in final["final_candidates"] if item["slot"] == "A")
    b = next(item for item in final["final_candidates"] if item["slot"] == "B")
    rule = final["corrected_cgs_selection"]
    lines += [f"Final-two protocol outcome: A=`{a['candidate_id']}` / `{a['submission_id']}` "
              f"({a['public_rmse']:.9g}); B=`{b['candidate_id']}` / `{b['submission_id']}` "
              f"({b['public_rmse']:.9g}). A was selected from exactly four corrected-order `_cgs` "
              f"scores; unique winner `{rule['winner']}` exceeded all pairwise noise guards. "
              "B is the pre-registered companion, not an A-rule competitor.", ""]
    return lines


def external_section(register: list[dict[str, Any]], provided_audit: dict[str, Any]) -> list[str]:
    lines = ["## Appendix F: External layers and source register", ""]
    lines += ["Four public external layers were used only for the discovery screen described in the linked supporting analysis; none enters scored predictions. Retrieval dates and licence/terms verification status are recorded below. Where terms were not verified, no reuse or public-domain claim is made.", ""]
    layers = CONFIG["external_sources"].get("required_external_layers", [])
    if layers:
        lines += ["| Layer | Source URL | Retrieved | Licence / terms status |", "|---|---|---|---|"]
        lines += [f"| {row['name']} | {row['url']} | {row['accessed_on']} | {row['licence_terms']} |"
                  for row in layers]
        lines += ["", "Source-file hashes, download timestamps, and source-page licence checks are recorded in `$COMPETITION_WORKSPACE/external-discovery-2026-10-27/MANIFEST.json`; the data files are not redistributed in this packet.", ""]
    lines += ["The general register below lists prior-art sources, organiser-hosted challenge files, and public discovery endpoints. Scored predictions read only files in the organiser data package.", ""]
    if not register:
        lines += ["(no external sources consulted)", ""]
    else:
        lines += ["| URL | Reference | Accessed | Role |", "|---|---|---|---|"]
        lines += [f"| {row['url']} | {row['label']} | {row['accessed_on']} | {row['role']} |"
                  for row in register]
        lines += [""]
    acceptance = provided_audit.get("acceptance", {})
    lines += [f"Provided-data-only check for the scored predictions "
              f"(`{CONFIG['external_sources']['provided_data_evidence']}`): "
              f"field-lineage fraction {acceptance.get('fraction_fields_with_manifest_entry')}, "
              f"{acceptance.get('prohibited_source_dependencies')} prohibited source dependencies, "
              f"{acceptance.get('unclassified_input_sources')} unclassified inputs, "
              f"external data added to scored predictions: "
              f"{provided_audit.get('prohibitions', {}).get('external_data_added_to_scored_predictions')}.",
              ""]
    return lines


def reproducibility_section(audit: dict[str, Any]) -> list[str]:
    measured = audit.get("summary", {})
    lines = ["## Appendix G: Reproducibility audit", "",
             "A machine-checked claim-to-evidence audit backs every number in this writeup.", ""]
    for key in ("claims_total", "claims_fully_traceable", "fraction_quantitative_claims_traceable",
                "numeric_tokens_total", "numeric_tokens_covered",
                "fraction_numeric_tokens_covered", "distinct_verify_commands",
                "distinct_reproduce_commands", "derived_aggregates_verified",
                "claims_asserting_accuracy"):
        if key in measured:
            lines.append(f"- `{key}`: {measured[key]}")
    unresolved = len(audit.get("unresolved", []))
    lines += ["", f"Current audit status: {measured.get('claims_fully_traceable')} of "
              f"{measured.get('claims_total')} quantitative claims are fully traceable "
              f"({measured.get('fraction_quantitative_claims_traceable')}); "
              f"{measured.get('numeric_tokens_covered')} of {measured.get('numeric_tokens_total')} "
              f"numeric tokens are covered ({measured.get('fraction_numeric_tokens_covered')}). "
              f"There are {unresolved} unresolved claim-evidence items. The full claim audit does "
              "not pass overall acceptance because three registered report-product differences "
              "remain outside accepted disclosure paths (`reports/writeup-claim-evidence-audit.json#/commands/undisclosed_product_changes`). This is a reproduction limitation, not an accuracy claim. The final-two "
              "result is separately traced to `reports/final-two.json` and machine-compared by the "
              "packet verifier.", "",
              "The claim inventory, audit configuration, and execution details are available in "
              "[`claim-evidence-audit.md`](claim-evidence-audit.md) and the linked JSON reports: "
              "`reports/writeup-claim-evidence-audit.json`, `reports/writeup-claim-inventory.json`, "
              "and `reports/writeup-claim-evidence-execution.json`.", "",
              "Deterministic audit artifacts: "
              "`reports/writeup-claim-evidence-audit.json`, "
              "`reports/writeup-claim-inventory.json`, "
              "`reports/writeup-claim-evidence-execution.json`.", ""]
    return lines


def provenance_section() -> list[str]:
    prohibited = CONFIG["prohibited_content"]
    lines = ["## Appendix H: Data provenance and prohibited-source statement", "",
             "- Scored predictions are built only from the organiser-provided Overture roads, "
             "buildings and POI extracts, the provided census/ACS table, the provided strata "
             "table and the platform submission template, plus fixed documented priors.",
             "- The organisers removed the reference layers "
             f"({', '.join(prohibited['removed_reference_layers'])}). None of those layers, nor an "
             "equivalent copy, is used to build scored predictions or to reconstruct, derive, "
             "approximate, or calibrate target values.",
             "- The organiser's 2026-10-08 Challenge Update (Zindi discussion #35250) permits "
             "removed reference layers as supporting comparison evidence in Best Bias Discovery, "
             "provided they are not used for target reconstruction or calibration. The Camp Fire "
             "comparison uses the provided USFS Wildfire Risk to Communities count and incident-filtered "
             "CAL FIRE DINS inspection points; the border finding uses a provided USFS building-count "
             "stratum. These are descriptive discovery comparisons only, not model inputs or target "
             "reconstruction. DINS provenance is recorded in Appendix F.",
             "- Overture source tags named Google Open Buildings and USGS lidar are source labels in the "
             "provided source-provenance report; no external Google or USGS footprint/reference layer "
             "was accessed or used to build predictions or reconstruct the target.",
             "- No sample score placeholder, no label, and no leaderboard-probe outcome is used as "
             "model evidence. The retired leaderboard-fitted constants and probe values of the "
             "retired probe set are listed in `reports/probe-retirement-2026-10-24.json` and "
             "appear nowhere in the model chain, in this writeup, or in any claim evidence, by "
             "name or by value.",
             "- Equity and hazard strata columns are used only for the descriptive discovery "
             "screen, never as model inputs; the descriptive reports state that they are not "
             "accuracy measurements.",
             "- All code and tooling used is open source, as the rules require.", "",
             "### Quoted official page text", ""]
    page = CONFIG["organiser_page"]
    lines += [f"Source: {page['url']} (documented {page['documented_on']}). {page['role']}.", ""]
    lines += [f"> {entry['quote']}" for entry in page["extracts"]]
    lines += [""]
    for entry in page["extracts"]:
        lines.append(f"- Used for: {entry['used_for']}.")
    return lines


AUTHORED_SECTIONS = ("front_matter", "consideration", "leaderboard_submissions",
                     "external_source_register", "reproducibility_appendix",
                     "provenance_statement")


def assemble(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    submissions = successful_submissions()
    candidate = model_candidate()
    provided_audit = load_json(ROOT / CONFIG["external_sources"]["provided_data_evidence"])
    claim_audit = load_json(ROOT / CONFIG["numeric_provenance"]["claim_audit_report"])

    parts: list[str] = []
    authored: list[str] = []
    for section in config["assembly"]["sections"]:
        kind = section["kind"]
        chunk: list[str] = []
        if kind == "front_matter":
            chunk += front_matter(config, submissions, candidate)
        elif kind == "consideration":
            chunk += consideration(config)
        elif kind == "leaderboard_submissions":
            chunk += leaderboard_table(submissions, candidate)
        elif kind == "external_source_register":
            chunk += []  # filled after the register is built from the full draft
        elif kind == "reproducibility_appendix":
            chunk += reproducibility_section(claim_audit)
        elif kind == "provenance_statement":
            chunk += provenance_section()
        elif kind in {"detailed_methodology_appendix", "edge_cases", "discovery_screens_appendix"}:
            chunk += [f"## {section['heading']}", ""]
            if kind == "detailed_methodology_appendix":
                chunk += ["Detailed formulas, assumptions, and reproduction notes are in the linked [methodology source](methodology.md). Alternative estimator-family and sensitivity diagnostics are in [Appendix diagnostics](appendix-diagnostics.md).", ""]
            elif kind == "edge_cases":
                chunk += ["The complete edge-case table and calculation policy are available in the linked [edge-case appendix](edge-cases.md).", ""]
            else:
                chunk += ["Supporting discovery screens and limitations are available in the linked [screen appendix](appendices/screens-that-did-not-produce-a-finding.md). External-layer URLs, retrieval dates, and licence/terms status are summarized in Appendix F.", ""]
        else:
            chunk += [f"## {section['heading']}", ""]
            source = ROOT / section["source"]
            chunk += [shift_headings(source.read_text(encoding="utf-8").strip()), ""]
        if kind in AUTHORED_SECTIONS and kind != "external_source_register":
            authored += chunk
        parts.append((kind, chunk))
    draft = "\n".join(line for _, chunk in parts for line in chunk).rstrip() + "\n"
    register = external_register(draft)
    final_parts: list[str] = []
    for kind, chunk in parts:
        if kind == "external_source_register":
            chunk = external_section(register, provided_audit)
            authored += chunk
        final_parts += chunk
    markdown = "\n".join(final_parts).rstrip() + "\n"
    facts = {"submissions": submissions, "candidate": candidate,
             "external_register": register, "authored_text": "\n".join(authored),
             "provided_audit": provided_audit, "claim_audit": claim_audit}
    return markdown, facts


# --------------------------------------------------------------------------- pdf
def _writeup_pdf_module():
    name = "_bias_writeup_pdf_builder"
    if name in sys.modules:
        return sys.modules[name]
    path = ROOT / "scripts/build_writeup_pdf.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load PDF builder: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def markdown_to_pdf(markdown: str, out_path: Path,
                    markdown_path: Path | None = None) -> dict[str, Any]:
    source_path = markdown_path or ROOT / CONFIG["assembly"]["output_markdown"]
    return _writeup_pdf_module().build_pdf(source_path, out_path)


def inspect_writeup_pdf(path: Path) -> dict[str, int]:
    return _writeup_pdf_module().inspect_pdf(path)


def _legacy_markdown_to_pdf(markdown: str, out_path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab import rl_config
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import Image as ReportLabImage, KeepTogether, Paragraph, \
        SimpleDocTemplate, Spacer, Table, TableStyle

    # byte-reproducible PDF: fixed document identity, no wall-clock in the file
    rl_config.invariant = 1

    def reproducible_canvas(filename, **kwargs):
        kwargs.pop("invariant", None)
        return rl_canvas.Canvas(filename, invariant=1, **kwargs)

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], alignment=TA_LEFT, fontSize=9,
                          leading=12, spaceAfter=4)
    code = ParagraphStyle("code", parent=body, fontName="Courier", fontSize=7.5, leading=9.5)
    quote = ParagraphStyle("quote", parent=body, leftIndent=10, fontName="Times-Italic")
    heads = {level: ParagraphStyle(f"h{level}", parent=styles[f"Heading{min(level, 4)}"],
                                   fontSize=max(16 - level * 1.5, 9.5), leading=max(19 - level, 12),
                                   spaceBefore=8, spaceAfter=4) for level in range(1, 6)}

    def escape(text: str) -> str:
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
        return text.replace("&", "&amp;").replace("<b>", "\x01").replace("</b>", "\x02") \
                   .replace("<i>", "\x03").replace("</i>", "\x04").replace("<", "&lt;") \
                   .replace(">", "&gt;").replace("\x01", "<b>").replace("\x02", "</b>") \
                   .replace("\x03", "<i>").replace("\x04", "</i>")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Bias Bounty final writeup", author="comp-zindi-bias-bounty",
                            subject="Zindi Bias Bounty methodology and discovery writeup",
                            creator="scripts/build_final_writeup.py",
                            canvasFactory=reproducible_canvas)
    story: list[Any] = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("```"):
            index += 1
            block = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1
            story.append(KeepTogether([Paragraph(escape("  ".join(block)[:1400] or " "), code),
                                       Spacer(1, 4)]))
            continue
        image = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if image:
            image_path = (ROOT / "docs/writeup" / image.group(2)).resolve()
            if not image_path.is_file():
                raise FileNotFoundError(f"writeup image missing: {image_path}")
            graphic = ReportLabImage(str(image_path))
            scale = min(doc.width / graphic.drawWidth, 125 * mm / graphic.drawHeight, 1.0)
            graphic.drawWidth *= scale
            graphic.drawHeight *= scale
            story.append(KeepTogether([graphic, Spacer(1, 5)]))
            index += 1
            continue
        heading = re.match(r"^(#{1,5})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            story.append(Paragraph(escape(heading.group(2)), heads[level]))
            index += 1
            continue
        if stripped.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", cell or "-") for cell in cells):
                    rows.append([Paragraph(escape(cell), body) for cell in cells])
                index += 1
            if rows:
                table = Table(rows, hAlign="LEFT", repeatRows=1)
                table.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
                story.append(table)
                story.append(Spacer(1, 5))
            continue
        if stripped.startswith("> "):
            story.append(Paragraph(escape(stripped[2:]), quote))
            index += 1
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", stripped) or re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if bullet:
            story.append(Paragraph("&bull; " + escape(bullet.group(1)), body))
            index += 1
            continue
        story.append(Paragraph(escape(stripped), body))
        index += 1
    doc.build(story)


# --------------------------------------------------------------------------- record
def load_operator_inputs_template(config: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / config["assembly"]["operator_inputs_template"]
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return operator_inputs_template(config)


def build_record(config: dict[str, Any], facts: dict[str, Any], markdown_path: Path,
                 pdf_path: Path,
                 bundle_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    claim_report = facts["claim_audit"]
    measured = claim_report.get("summary", {})
    provided = facts["provided_audit"]
    final_two = load_json(ROOT / CONFIG["numeric_provenance"]["final_two"])
    status, ready, missing = submission_state(config)
    record = {
        "schema_version": 1,
        "row_key": config["row_key"],
        "lane": config["lane"],
        "competition": config["competition"],
        "status": status,
        "ready_for_submission": ready,
        "pending_operator_inputs": missing,
        "operator_decision_inputs_pending": [
            "review and approve the Best Bias Discovery narrative",
            "confirm the Zindi username or team name",
            "supply the organiser's methodology-form URL",
            "confirm protocol final-two IDs V2_model_cgs (A) and B_from_A_cgs (B) for UI/form use"],
        "organiser_page": config["organiser_page"],
        "operator_input_schema": config["operator_input_schema"],
        "revision": {"git_commit": git("rev-parse", "HEAD"),
                     "tree_clean": git("status", "--porcelain") == "",
                     "pdf_engine": CONFIG["assembly"]["pdf_engine"],
                     "pdf_toolchain": CONFIG["assembly"]["pdf_toolchain"]},
        "pdf_validation": (inspect_writeup_pdf(pdf_path) | {
            "sha256": sha256_file(pdf_path),
            "max_bytes_exclusive": 10_000_000,
            "reportlab_version": "4.4.10"
        }) if pdf_path.suffix.lower() == ".pdf" and pdf_path.is_file() else {},
        "identity": dict(config["identity"]),
        "form": dict(config["form"]),
        "prizes": config["prizes"],
        "consideration_requested": config["consideration_requested"],
        "associated_leaderboard_submissions": facts["submissions"],
        "model_candidate": facts["candidate"],
        "final_two": {"status": final_two["status"],
                      "protocol": final_two["protocol"],
                      "selected": dict(config["final_two"]["selected"]),
                      "protocol_selected": [
                          {key: item[key] for key in ("slot", "candidate_id", "artifact", "sha256",
                                                       "submission_id", "public_rmse", "selection_status")}
                          for item in final_two["final_candidates"]],
                      "selection_trace": final_two["corrected_cgs_selection"],
                      "operator_confirmation_pending": not all(
                          config["final_two"]["selected"].values()),
                      "selection_note": "Protocol A/B results are recorded from reports/final-two.json. "
                                        "Operator confirmation and Zindi UI selection remain pending; "
                                        "the operator-owned confirmation fields stay empty."},
        "external_source_register": facts["external_register"],
        "external_data_policy": {
            "scored_predictions_use_only_provided_data":
                CONFIG["external_sources"]["scored_predictions_use_only_provided_data"],
            "provided_data_audit": CONFIG["external_sources"]["provided_data_evidence"],
            "prohibited_source_dependencies":
                provided.get("acceptance", {}).get("prohibited_source_dependencies"),
            "field_lineage_fraction": provided.get("acceptance", {}).get(
                "fraction_fields_with_manifest_entry"),
            "input_count": len(provided.get("inputs", {}).get("entries", [])
                               or provided.get("inputs", {})),
            "prohibitions": dict(provided.get("prohibitions", {}))},
        "claim_evidence_audit": {
            "report": CONFIG["numeric_provenance"]["claim_audit_report"],
            "claims_total": measured.get("claims_total"),
            "claims_fully_traceable": measured.get("claims_fully_traceable"),
            "fraction_quantitative_claims_traceable":
                measured.get("fraction_quantitative_claims_traceable"),
            "numeric_tokens_total": measured.get("numeric_tokens_total"),
            "numeric_tokens_covered": measured.get("numeric_tokens_covered"),
            "fraction_numeric_tokens_covered": measured.get("fraction_numeric_tokens_covered"),
            "distinct_verify_commands": measured.get("distinct_verify_commands"),
            "distinct_reproduce_commands": measured.get("distinct_reproduce_commands"),
            "derived_aggregates_verified": measured.get("derived_aggregates_verified"),
            "claims_asserting_accuracy": measured.get("claims_asserting_accuracy"),
            "claims_with_prohibited_data_language":
                measured.get("claims_with_prohibited_data_language")},
        "writeup_files": [
            {"path": relative_or(markdown_path, CONFIG["assembly"]["output_markdown"]),
             "role": "writeup_markdown", "sha256": sha256_file(markdown_path),
             "bytes": markdown_path.stat().st_size},
            {"path": relative_or(pdf_path, CONFIG["assembly"]["output_pdf"]),
             "role": "writeup_pdf", "sha256": sha256_file(pdf_path),
             "bytes": pdf_path.stat().st_size}],
        "submission_record": dict(config["submission_record"]),
        "submission_bundle": bundle_summary or {},
        "operator_inputs_template": load_operator_inputs_template(config),
        "blocking_prerequisites": blocking_prerequisites(config, final_two["status"]),
        "commands": dict(CONFIG["commands"]),
    }
    if bundle_summary:
        record["writeup_files"].append({"path": bundle_summary["path"],
                                        "role": "submission_bundle",
                                        "sha256": bundle_summary["sha256"],
                                        "bytes": bundle_summary["bytes"]})
    record["acceptance"] = acceptance(record, facts["authored_text"])
    record["acceptance"]["all_passed"] = all(
        value is True for key, value in record["acceptance"].items() if key != "all_passed")
    return record


def blocking_prerequisites(config: dict[str, Any], final_two_status: str) -> list[str]:
    prerequisites: list[str] = []
    if not config["identity"].get("zindi_username_or_team"):
        prerequisites.append("operator confirmation of the Zindi username or team name "
                             "(identity.zindi_username_or_team)")
    if not config["identity"].get("operator_confirmation_recorded"):
        prerequisites.append("operator confirmation flag for that identity "
                             "(identity.operator_confirmation_recorded)")
    if not config["form"].get("url"):
        prerequisites.append("organiser writeup form URL: the saved rules text keeps the sentence "
                             "but not the hyperlink target, and the live page is client-rendered, "
                             "so the operator must supply it (form.url)")
    if config["submission_record"].get("operator_review") == "not recorded":
        prerequisites.append("operator review of the Best Bias Discovery narrative "
                             "(submission_record.operator_review)")
    selected = config["final_two"]["selected"]
    if not (selected.get("candidate_A") and selected.get("candidate_B")):
        prerequisites.append("final-two selection of the two candidates whose submission IDs the "
                             f"form must name (reports/final-two.json status {final_two_status})")
    if not config["submission_record"].get("submitted_at_utc"):
        prerequisites.append("form submission evidence: UTC time, organiser confirmation reference "
                             "and submitter (submission_record.*)")
    return prerequisites


def build_bundle(config: dict[str, Any], record: dict[str, Any], markdown_path: Path,
                 pdf_path: Path) -> dict[str, Any]:
    """Hash-tied copy-paste sheet for the human who fills the organiser form."""
    entries = [
        {"path": relative_or(markdown_path, config["assembly"]["output_markdown"]),
         "role": "writeup_markdown", "sha256": sha256_file(markdown_path),
         "bytes": markdown_path.stat().st_size},
        {"path": relative_or(pdf_path, config["assembly"]["output_pdf"]),
         "role": "writeup_pdf", "sha256": sha256_file(pdf_path),
         "bytes": pdf_path.stat().st_size}]
    submission_lines = [
        f"{row['candidate_id']} | {row['platform_submission_id']} | {row['artifact_sha256']}"
        for row in record["associated_leaderboard_submissions"]]
    candidate = record["model_candidate"]
    upload = candidate["upload_status"]
    blocks = {
        "zindi_identity_line": "Zindi username or team name: "
                               + (record["identity"]["zindi_username_or_team"]
                                  or "PENDING_OPERATOR_CONFIRMATION"),
        "prize_request_line": config["consideration_requested"]["stated_verbatim_in_writeup"],
        "submission_ids_block": "\n".join(submission_lines),
        "model_candidate_block": (
            f"{candidate['candidate_id']} | artifact {candidate['artifact']} | sha256 "
            f"{candidate['sha256']} | uploaded {str(upload['uploaded']).lower()} | uploads used "
            f"{upload['uploads_used_for_this_candidate']}"),
        "writeup_hashes_block": "\n".join(
            f"{item['path']} | sha256 {item['sha256']} | {item['bytes']} bytes" for item in entries),
        "form_status_line": (f"Form URL: {record['form']['url']}"
                             if record["form"]["url"] else
                             "Form URL: to be supplied by the operator (not recoverable from the "
                             "saved rules text or the client-rendered organiser page)")}
    return {"schema_version": 1,
            "purpose": "single copy-paste sheet for the organiser writeup form, hash-tied to the "
                       "committed writeup artefacts",
            "packet_status": record["status"],
            "ready_for_submission": record["ready_for_submission"],
            "pending_operator_inputs": record["pending_operator_inputs"],
            "form_url": record["form"]["url"],
            "files": entries,
            "paste_blocks": blocks}


def operator_inputs_template(config: dict[str, Any]) -> dict[str, Any]:
    """Keep operator-owned fields blank and attach the registered, non-actioning paste packet."""
    template: dict[str, Any] = {}
    for path in ALLOWED_OPERATOR_PATHS:
        node = template
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (False if path == "identity.operator_confirmation_recorded"
                           else "not recorded" if path == "submission_record.operator_review"
                           else None)
    template["prepared_packet"] = config["prepared_operator_packet"]
    return template


def bundle_matches(record: dict[str, Any], files: dict[str, Any]) -> bool:
    summary = record.get("submission_bundle") or {}
    bundle = summary.get("bundle") or {}
    entry = files.get(summary.get("path", ""))
    if not entry or not bundle:
        return False
    if {item["role"] for item in bundle["files"]} != {"writeup_markdown", "writeup_pdf"}:
        return False
    for item in bundle["files"]:
        mirror = next((existing for existing in record["writeup_files"]
                       if existing["role"] == item["role"]), None)
        if mirror is None or mirror["sha256"] != item["sha256"] or mirror["bytes"] != item["bytes"]:
            return False
        path = ROOT / item["path"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            return False
    summary_path = ROOT / summary["path"]
    return (summary["sha256"] == sha256_file(summary_path)
            and summary["bytes"] == summary_path.stat().st_size
            and entry["sha256"] == summary["sha256"])


def bundle_blocks_present(record: dict[str, Any], text: str) -> bool:
    bundle = (record.get("submission_bundle") or {}).get("bundle") or {}
    blocks = bundle.get("paste_blocks") or {}
    expected = {"zindi_identity_line", "prize_request_line", "submission_ids_block",
                "model_candidate_block", "writeup_hashes_block", "form_status_line"}
    if set(blocks) != expected or not all(value for value in blocks.values()):
        return False
    if blocks["prize_request_line"] not in text:
        return False
    for line in blocks["submission_ids_block"].splitlines():
        submission_id = line.split("|")[1].strip() if "|" in line else ""
        if submission_id not in text:
            return False
    return True


def template_matches_whitelist(record: dict[str, Any]) -> bool:
    template = record.get("operator_inputs_template")
    if not isinstance(template, dict) or not template:
        return False
    flattened: set[str] = set()

    def walk(node: dict[str, Any], prefix: str = "") -> None:
        for key, value in node.items():
            path = f"{prefix}{key}"
            if isinstance(value, dict):
                walk(value, path + ".")
            else:
                flattened.add(path)

    operator_fields = {key: value for key, value in template.items()
                       if key != "prepared_packet"}
    walk(operator_fields)
    if flattened != set(ALLOWED_OPERATOR_PATHS):
        return False
    if template.get("prepared_packet") != CONFIG.get("prepared_operator_packet"):
        return False
    if set(record["operator_input_schema"]["fields"]) != flattened:
        return False
    for path in ALLOWED_OPERATOR_PATHS:
        node: Any = template
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if path == "identity.operator_confirmation_recorded":
            if node is not False:
                return False
        elif path == "submission_record.operator_review":
            if node != "not recorded":
                return False
        elif node is not None:
            return False
    return True


def acceptance(record: dict[str, Any], authored_text: str = "") -> dict[str, Any]:
    """Measured packet-integrity gates. Operator-owned inputs are reported, never assumed."""
    submitted = record["status"] == "submitted_confirmed_by_operator"
    pdf_path = ROOT / CONFIG["assembly"]["output_pdf"]
    pdf_entry = next((item for item in record["writeup_files"]
                      if item.get("role") == "writeup_pdf"), None)
    recorded_pdf_path = ROOT / pdf_entry["path"] if pdf_entry else pdf_path
    pdf_meta = record.get("pdf_validation", {})
    pdf_actual = inspect_writeup_pdf(recorded_pdf_path) if recorded_pdf_path.is_file() else {}
    files = {item["path"]: item for item in record["writeup_files"]}
    markdown = ROOT / "docs/writeup/final-writeup.md"
    text = markdown.read_text(encoding="utf-8") if markdown.is_file() else ""
    prizes = record["prizes"]
    declared_ids = {row["platform_submission_id"]
                    for row in record["associated_leaderboard_submissions"]}
    registry_ids = {card["candidate_id"] for card in
                    load_json(ROOT / CONFIG["numeric_provenance"]["candidates_registry"])
                    ["candidate_set"]}
    named_candidates = {row["candidate_id"]
                        for row in record["associated_leaderboard_submissions"]}
    candidate_id = record["model_candidate"]["candidate_id"]
    prohibited = CONFIG["prohibited_content"]
    value_hits = [value for value in prohibited["retired_leaderboard_constant_values"]
                  if value in text or value in authored_text]
    name_hits = [name for name in prohibited["retired_leaderboard_constant_names"]
                 if name in text or name in authored_text]
    register_urls = {row["url"] for row in record["external_source_register"]}
    doc_urls = {normalize_url(url) for url in URL_PATTERN.findall(text)}
    return {
        "final_writeup_markdown_present": bool(markdown.is_file() and len(text) > 20000)
        and "docs/writeup/final-writeup.md" in files,
        "writeup_pdf_present": pdf_path.is_file() and pdf_actual.get("bytes", 0) > 0,
        "writeup_pdf_under_10_mb": recorded_pdf_path.is_file()
        and recorded_pdf_path.stat().st_size < 10_000_000,
        "writeup_pdf_page_count_sane": 5 <= pdf_actual.get("page_count", 0) <= 80,
        "writeup_pdf_clickable_links_present": pdf_actual.get("link_annotations", 0) > 0,
        "writeup_pdf_figures_embedded": pdf_actual.get("embedded_images", 0) > 0,
        "writeup_pdf_validation_hash_matches": (
            pdf_entry is not None and recorded_pdf_path.is_file()
            and pdf_meta.get("sha256") == sha256_file(recorded_pdf_path)
            == pdf_entry.get("sha256")),
        "writeup_pdf_validation_counts_match": all(
            pdf_meta.get(key) == pdf_actual.get(key) for key in
            ("bytes", "page_count", "link_annotations", "embedded_images")),
        "recorded_hashes_match_bytes": all(
            (ROOT / item["path"]).is_file() and sha256_file(ROOT / item["path"]) == item["sha256"]
            for item in record["writeup_files"]),
        "both_prizes_stated_with_amounts": all(
            prize["name"] in text and f"${prize['amount_usd']:,}" in text for prize in prizes),
        "discovery_section_labelled": any(
            prize["section_label"] in text for prize in prizes
            if prize["name"] == "Best Bias Discovery"),
        "documentation_section_labelled": any(
            prize["section_label"] in text for prize in prizes
            if prize["name"] == "Best Documentation"),
        "discovery_criteria_weights_quoted": all(
            f"{name}:" in text and f"{weight}%" in text
            for prize in prizes if prize.get("criteria_weights")
            for name, weight in prize["criteria_weights"].items()),
        "consideration_statement_verbatim": record["consideration_requested"][
            "stated_verbatim_in_writeup"] in text,
        "operator_identity_confirmation_recorded": bool(
            record["identity"]["operator_confirmation_recorded"]),
        "form_url_available": bool(record["form"]["url"]),
        "submission_confirmation_recorded": bool(record["submission_record"]["submitted_at_utc"]
                                                and record["submission_record"]
                                                ["confirmation_reference"]),
        "submission_ids_tie_to_successful_upload_log": bool(declared_ids) and declared_ids <= {
            row["platform_submission_id"] for row in successful_submissions()},
        "submission_candidate_ids_registered": named_candidates <= registry_ids
        and candidate_id in registry_ids,
        "final_two_selection_ids_registered": (
            all(item["candidate_id"] in registry_ids
                for item in record["final_two"].get("protocol_selected", []))
            and (not submitted or all(record["final_two"]["selected"][slot] in registry_ids
                                      for slot in ("candidate_A", "candidate_B")))),
        "final_two_protocol_trace_matches_source": (
            record["final_two"].get("status") == final_two_report().get("status")
            and record["final_two"].get("protocol_selected") == [
                {key: item[key] for key in ("slot", "candidate_id", "artifact", "sha256",
                                             "submission_id", "public_rmse", "selection_status")}
                for item in final_two_report()["final_candidates"]]
            and record["final_two"].get("selection_trace")
                == final_two_report().get("corrected_cgs_selection")),
        "operator_confirmation_and_ui_selection_stay_pending": (
            record["final_two"].get("operator_confirmation_pending") is True
            and not record["final_two"]["selected"]["candidate_A"]
            and not record["final_two"]["selected"]["candidate_B"]
            and not submitted),
        "submission_bundle_hash_tied": bundle_matches(record, files),
        "submission_bundle_paste_blocks_verbatim": bundle_blocks_present(record, text),
        "operator_inputs_template_matches_whitelist": template_matches_whitelist(record),
        "model_candidate_artifact_hash_pinned": bool(
            record["model_candidate"]["sha256"]
            == CONFIG["model_evidence"]["frozen_candidate_sha256"]
            and record["model_candidate"]["registry_sha256_matches_card"]
            and record["model_candidate"]["artifact_sha256_in_build_audit"]),
        "model_candidate_non_upload_disclosed": CONFIG["model_evidence"]["upload_status_quote"]
        in text,
        "final_two_selection_declared_not_invented": (
            record["final_two"]["status"] == CONFIG["model_evidence"]["final_two_status"]
            and ((bool(record["final_two"]["selected"]["candidate_A"])
                  and bool(record["final_two"]["selected"]["candidate_B"]))
                 if submitted else
                 (record["final_two"]["selected"]["candidate_A"] is None
                  and record["final_two"]["selected"]["candidate_B"] is None))),
        "organiser_page_extracts_quoted_verbatim": all(
            entry["quote"] in text for entry in record["organiser_page"]["extracts"])
            and record["organiser_page"]["url"] in register_urls,
        "form_url_status_documented": bool(record["form"]["url"]) or (
            bool(record["organiser_page"]["form_url_search"].get("attempted_on"))
            and bool(record["organiser_page"]["form_url_search"].get("pages_attempted"))
            and bool(record["organiser_page"]["form_url_search"].get("outcome"))),
        "external_layer_licence_statuses_documented": all(
            layer["name"] in text and layer["url"] in text and layer["accessed_on"] in text
            and layer["licence_terms"] in text
            for layer in CONFIG["external_sources"].get("required_external_layers", [])), 
        "external_claims_carry_url_and_access_date": bool(doc_urls <= register_urls) and all(
            row["url"] and row["accessed_on"] and row["role"]
            for row in record["external_source_register"]) and len(
                record["external_source_register"]) >= 1 and all(
                    row["official"] or re.match(r"^\d{4}-\d{2}-\d{2}$", row["accessed_on"])
                    for row in record["external_source_register"]),
        "prior_art_literature_fully_registered": _literature_urls() <= register_urls,
        "no_retired_leaderboard_constant_named_or_valued": not value_hits and not name_hits,
        "claim_audit_covers_writeup_numbers": bool(record["claim_evidence_audit"][
            "fraction_quantitative_claims_traceable"]) and record["claim_evidence_audit"][
            "fraction_quantitative_claims_traceable"] == 1.0 and record["claim_evidence_audit"][
            "numeric_tokens_total"] == record["claim_evidence_audit"]["numeric_tokens_covered"],
        "provided_data_only_for_scored_predictions": (
            record["external_data_policy"]["prohibited_source_dependencies"] == 0
            and record["external_data_policy"]["prohibitions"].get(
                "external_data_added_to_scored_predictions") is False
            and record["external_data_policy"]["prohibitions"].get("target_labels_read") is False
            and record["external_data_policy"]["prohibitions"].get(
                "leaderboard_feedback_read_for_modeling") is False),
        "no_unilateral_submission_claimed": (
            (submitted and all(record["submission_record"][key]
                               for key in ("submitted_at_utc", "confirmation_reference",
                                           "submitted_by", "form_url_used"))
             and record["submission_record"]["operator_review"] != "not recorded"
             and record["identity"]["operator_confirmation_recorded"]
             and bool(record["identity"]["zindi_username_or_team"]))
            if submitted else
            (record["status"] == "prepared_pending_operator_review"
             and not record["ready_for_submission"]
             and all(record["submission_record"][key] is None for key in
                     ("submitted_at_utc", "confirmation_reference", "submitted_by",
                      "form_url_used"))
             and record["submission_record"]["operator_review"] == "not recorded"
             and not record["identity"]["operator_confirmation_recorded"])),
    }


ALLOWED_OPERATOR_PATHS = (
    "identity.zindi_username_or_team", "identity.operator_confirmation_recorded",
    "form.url", "submission_record.submitted_at_utc",
    "submission_record.confirmation_reference", "submission_record.submitted_by",
    "submission_record.form_url_used", "submission_record.operator_review",
    "final_two.selected.candidate_A", "final_two.selected.candidate_B")


def apply_operator_inputs(config: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """Merge only the operator-owned fields; anything else in the file is ignored."""
    merged = json.loads(json.dumps(config))
    for path in ALLOWED_OPERATOR_PATHS:
        node: Any = inputs
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is None:
            continue
        target = merged
        parts = path.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = node
    if merged["submission_record"].get("form_url_used") and not merged["form"].get("url"):
        merged["form"]["url"] = merged["submission_record"]["form_url_used"]
    return merged


def submission_state(config: dict[str, Any]) -> tuple[str, bool, list[str]]:
    required = config["operator_input_schema"]["ready_requires"]
    missing = []
    for path in required:
        node: Any = config
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if not node:
            missing.append(path)
    if not missing:
        return "submitted_confirmed_by_operator", True, []
    return "prepared_pending_operator_review", False, missing


def main() -> int:
    global CONFIG
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--markdown", type=Path,
                        default=ROOT / "docs/writeup/final-writeup.md")
    parser.add_argument("--pdf", type=Path,
                        default=ROOT / CONFIG["assembly"]["output_pdf"])
    parser.add_argument("--record", type=Path, default=ROOT / "reports/writeup-submission.json")
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--operator-inputs", type=Path, default=None,
                        help="JSON of operator-owned fields, merged into the preregistration")
    parser.add_argument("--bundle", type=Path, default=None)
    parser.add_argument("--write-operator-inputs-template", action="store_true",
                        help="(re)write the all-null operator input template and exit")
    args = parser.parse_args()

    CONFIG = load_config(args.config)
    if args.operator_inputs:
        CONFIG = apply_operator_inputs(CONFIG, json.loads(
            args.operator_inputs.read_text(encoding="utf-8")))
        print(f"merged operator inputs from {args.operator_inputs}")
    markdown, facts = assemble(CONFIG)
    if args.check_only:
        existing = args.markdown.read_text(encoding="utf-8") if args.markdown.is_file() else ""
        same = existing == markdown
        print(("unchanged" if same else "DIFFERS") + f" {args.markdown}")
        return 0 if same else 1
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown, encoding="utf-8")
    if not args.skip_pdf:
        markdown_to_pdf(markdown, args.pdf, args.markdown)
    pdf_for_record = args.pdf if not args.skip_pdf else args.markdown
    template_path = ROOT / CONFIG["assembly"]["operator_inputs_template"]
    template_path.parent.mkdir(parents=True, exist_ok=True)
    if args.write_operator_inputs_template or not template_path.is_file():
        template_path.write_text(json.dumps(operator_inputs_template(CONFIG), indent=2,
                                           ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"operator input template: {template_path}")
        if args.write_operator_inputs_template:
            return 0
    preliminary = build_record(CONFIG, facts, args.markdown, pdf_for_record)
    bundle = build_bundle(CONFIG, preliminary, args.markdown, pdf_for_record)
    bundle_path = args.bundle or ROOT / CONFIG["assembly"]["output_bundle"]
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    summary = {"path": relative_or(bundle_path, CONFIG["assembly"]["output_bundle"]),
               "sha256": sha256_file(bundle_path), "bytes": bundle_path.stat().st_size,
               "bundle": bundle}
    record = build_record(CONFIG, facts, args.markdown, pdf_for_record, summary)
    args.record.parent.mkdir(parents=True, exist_ok=True)
    with args.record.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, ensure_ascii=False, sort_keys=False)
        handle.write("\n")
    failed = sorted(key for key, value in record["acceptance"].items() if value is not True)
    print(json.dumps({"status": record["status"],
                      "ready_for_submission": record["ready_for_submission"],
                      "acceptance_all_passed": record["acceptance"]["all_passed"],
                      "pending_operator_inputs": [key for key in failed],
                      "markdown_sha256": next(item["sha256"] for item
                                              in record["writeup_files"]
                                              if item["role"] == "writeup_markdown"),
                      "pdf_sha256": next((item["sha256"] for item in record["writeup_files"]
                                          if item["role"] == "writeup_pdf"), None),
                      "external_sources": len(record["external_source_register"]),
                      "bundle_sha256": (record.get("submission_bundle") or {}).get("sha256"),
                      "associated_submissions": len(record["associated_leaderboard_submissions"])},
                     indent=1))
    print(("PACKET READY FOR OPERATOR REVIEW: " if not failed else
           "PACKET ASSEMBLED, OPERATOR INPUTS MISSING (" + ", ".join(failed) + "): ")
          + "packet is staged only; nothing was submitted to the organiser")
    return 0


CONFIG = load_config()

if __name__ == "__main__":
    sys.exit(main())
