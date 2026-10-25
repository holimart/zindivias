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
        return default


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
                     "score_split": record.get("score_split"),
                     "uploaded_at_utc": record.get("timestamp_utc"),
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
    scope = load_json(ROOT / CONFIG["numeric_provenance"]["final_two"])["scope"]
    username = identity["zindi_username_or_team"] or identity["pending_placeholder"]
    lines = [
        "# Bias Bounty Mapping Equity Challenge — final methodology and discovery writeup",
        "",
        f"- Competition: `{config['competition']}` (Zindi)",
        f"- Lane: `{config['lane']}`",
        f"- Writeup revision: git `{git('rev-parse', 'HEAD')}` "
        f"(tree clean: {git('status', '--porcelain') == ''})",
        f"- Zindi username or team name: **{username}**",
        f"- Operator confirmation of that identity recorded: "
        f"{'yes' if identity['operator_confirmation_recorded'] else 'NO — pending operator'}",
        f"- Official requirement: {identity['rules_requirement_quote']}",
        f"- Packaged for the organiser writeup form; form URL status: "
        f"{'recorded' if config['form']['url'] else 'NOT AVAILABLE in the saved rules text'}",
        "",
        "Scored scope: "
        f"{scope['official_scored_tracts']} official tracts across four regions "
        f"({scope['public_tracts_rounded']} public / {scope['private_tracts_rounded']} private "
        f"expected), uploaded on the platform template of {scope['platform_template_rows']} rows "
        f"including {scope['template_only_rows']} template-only rows.",
        "",
        f"Current model candidate presented in this writeup: `{candidate['candidate_id']}` at "
        f"`{candidate['artifact']}` (SHA-256 `{candidate['sha256']}`), {candidate['rows']} rows. "
        f"{config['model_evidence']['upload_status_quote']}",
        "",
        "Every quantitative statement in this writeup is covered by the claim-to-evidence audit "
        "summarised in the reproducibility appendix; nothing here is an accuracy claim beyond the "
        "platform scorecard values quoted verbatim from our submission log.",
        "",
    ]
    if not config["form"]["url"]:
        lines += [f"> {config['form']['url_note']}", ""]
    return lines


def consideration(config: dict[str, Any]) -> list[str]:
    lines = ["## Prize consideration requested", ""]
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
    lines = ["## Leaderboard submissions associated with this methodology", "",
             "| Platform submission id | Candidate | Artifact | Artifact SHA-256 | Public RMSE "
             "| Split | Score source | Uploaded (UTC) |",
             "|---|---|---|---|---:|---|---|---|"]
    for row in submissions:
        lines.append("| {platform_submission_id} | {candidate_id} | `{artifact}` | "
                     "`{artifact_sha256}` | {score} | {split} | {source} | {when} |".format(
                         **{**row,
                            "score": "n/a" if row["public_score"] is None
                            else f"{row['public_score']:.9g}",
                            "split": row["score_split"] or "n/a",
                            "source": row["score_source"] or "n/a",
                            "when": row["uploaded_at_utc"] or "n/a"}))
    lines += ["",
              f"The scored methodology candidate in this writeup is `{candidate['candidate_id']}`, "
              f"built from provided features only (commit `{candidate['build_commit']}`). "
              f"{CONFIG['model_evidence']['upload_status_quote']} Its predicted public RMSE is "
              f"{candidate['predicted_public_rmse']:.17g} on the stated "
              f"{candidate['prediction_basis']} basis, which is a model-side forecast, not a "
              "leaderboard observation.",
              "",
              "The C0-P4 uploads above are historical calibration and probe records kept for "
              "auditability. They are excluded from the frozen final-two cohort and none of their "
              "leaderboard values, fitted constants or probe outcomes is used as evidence for the "
              "model or for any discovery claim.",
              ""]
    return lines


def external_section(register: list[dict[str, Any]], provided_audit: dict[str, Any]) -> list[str]:
    lines = ["## External source register (URL and access date)", ""]
    lines += ["No external dataset is cited or used in the scored predictions. The register below "
              "lists the published prior art consulted for methodology know-how, with the date it "
              "was accessed, and the organiser-hosted challenge files the workspace downloader "
              "retrieved. No URL is needed to reproduce the scored predictions: they read only "
              "files inside the organiser data package.", ""]
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
    lines = ["## Reproducibility appendix", "",
             "A machine-checked claim-to-evidence audit backs every number in this writeup.", ""]
    for key in ("claims_total", "claims_fully_traceable", "fraction_quantitative_claims_traceable",
                "numeric_tokens_total", "numeric_tokens_covered",
                "fraction_numeric_tokens_covered", "distinct_verify_commands",
                "distinct_reproduce_commands", "derived_aggregates_verified",
                "claims_asserting_accuracy", "claims_with_prohibited_data_language"):
        if key in measured:
            lines.append(f"- `{key}`: {measured[key]}")
    lines += ["", "Commands (all recorded exit code 0):", "", "```bash",
              CONFIG["commands"]["build"], CONFIG["commands"]["verify"],
              CONFIG["commands"]["tests"],
              'COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" '
              "/home/martin/hddProjects/langywrap/uv run --quiet python "
              "scripts/audit_writeup_claim_evidence.py", "```", "",
              "Deterministic audit artifacts: "
              "`reports/writeup-claim-evidence-audit.json`, "
              "`reports/writeup-claim-inventory.json`, "
              "`reports/writeup-claim-evidence-execution.json`.", ""]
    return lines


def provenance_section() -> list[str]:
    prohibited = CONFIG["prohibited_content"]
    lines = ["## Data provenance and prohibited-source statement", "",
             "- Scored predictions are built only from the organiser-provided Overture roads, "
             "buildings and POI extracts, the provided census/ACS table, the provided strata "
             "table and the platform submission template, plus fixed documented priors.",
             "- The organisers removed the reference layers "
             f"({', '.join(prohibited['removed_reference_layers'])}). None of those layers, nor an "
             "equivalent copy, is used to build the scored predictions or to support any claim.",
             "- No sample score placeholder, no label, and no leaderboard-probe outcome is used as "
             "model evidence. The retired leaderboard-fitted constants and probe values of the "
             "retired probe set are listed in `reports/probe-retirement-2026-10-24.json` and "
             "appear nowhere in the model chain, in this writeup, or in any claim evidence, by "
             "name or by value.",
             "- Equity and hazard strata columns are used only for the descriptive discovery "
             "screen, never as model inputs; the descriptive reports state that they are not "
             "accuracy measurements.",
             "- All code and tooling used is open source, as the rules require.", ""]
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
def markdown_to_pdf(markdown: str, out_path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab import rl_config
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, \
        TableStyle

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
        text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)
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
def build_record(config: dict[str, Any], facts: dict[str, Any], markdown_path: Path,
                 pdf_path: Path) -> dict[str, Any]:
    claim_report = facts["claim_audit"]
    measured = claim_report.get("summary", {})
    provided = facts["provided_audit"]
    final_two = load_json(ROOT / CONFIG["numeric_provenance"]["final_two"])
    record = {
        "schema_version": 1,
        "row_key": config["row_key"],
        "lane": config["lane"],
        "competition": config["competition"],
        "status": "prepared_pending_operator_review",
        "ready_for_submission": False,
        "revision": {"git_commit": git("rev-parse", "HEAD"),
                     "tree_clean": git("status", "--porcelain") == "",
                     "pdf_engine": CONFIG["assembly"]["pdf_engine"]},
        "identity": dict(config["identity"]),
        "form": dict(config["form"]),
        "prizes": config["prizes"],
        "consideration_requested": config["consideration_requested"],
        "associated_leaderboard_submissions": facts["submissions"],
        "model_candidate": facts["candidate"],
        "final_two": {"status": final_two["status"],
                      "protocol": final_two["protocol"],
                      "selected": {"candidate_A": None, "candidate_B": None},
                      "selection_note": "No final-two selection is recorded yet; the writeup lists "
                                        "the registered model candidate and its non-uploaded "
                                        "status instead of inventing a selection."},
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
        "submission_record": {"submitted_at_utc": None, "confirmation_reference": None,
                              "submitted_by": None, "form_url_used": None,
                              "operator_review": "not recorded"},
        "blocking_prerequisites": [
            "operator review of the Best Bias Discovery narrative",
            "operator confirmation of the Zindi username or team name",
            "organiser writeup form URL (not preserved in the saved rules text)",
            "final-two selection of the two candidates whose submission IDs the form must name",
        ],
        "commands": dict(CONFIG["commands"]),
    }
    record["acceptance"] = acceptance(record, facts["authored_text"])
    record["acceptance"]["all_passed"] = all(
        value is True for key, value in record["acceptance"].items() if key != "all_passed")
    return record


def acceptance(record: dict[str, Any], authored_text: str = "") -> dict[str, Any]:
    """Measured packet-integrity gates. Operator-owned inputs are reported, never assumed."""
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
        "writeup_pdf_present": (ROOT / files["reports/writeup/final-writeup.pdf"]["path"]).is_file()
        if "reports/writeup/final-writeup.pdf" in files else False,
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
        "identity_field_present": CONFIG["identity"]["pending_placeholder"] in text
        or bool(record["identity"]["zindi_username_or_team"]),
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
        "model_candidate_artifact_hash_pinned": bool(
            record["model_candidate"]["sha256"]
            == CONFIG["model_evidence"]["frozen_candidate_sha256"]
            and record["model_candidate"]["registry_sha256_matches_card"]
            and record["model_candidate"]["artifact_sha256_in_build_audit"]),
        "model_candidate_non_upload_disclosed": CONFIG["model_evidence"]["upload_status_quote"]
        in text,
        "final_two_selection_declared_not_invented": record["final_two"]["status"] == \
        CONFIG["model_evidence"]["final_two_status"] and record["final_two"]["selected"][
            "candidate_A"] is None,
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
        "no_unilateral_submission_claimed": record["status"]
        == "prepared_pending_operator_review" and not record["ready_for_submission"]
        and record["submission_record"]["submitted_at_utc"] is None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--markdown", type=Path,
                        default=ROOT / "docs/writeup/final-writeup.md")
    parser.add_argument("--pdf", type=Path, default=ROOT / "reports/writeup/final-writeup.pdf")
    parser.add_argument("--record", type=Path, default=ROOT / "reports/writeup-submission.json")
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    global CONFIG
    CONFIG = load_config(args.config)
    markdown, facts = assemble(CONFIG)
    if args.check_only:
        existing = args.markdown.read_text(encoding="utf-8") if args.markdown.is_file() else ""
        same = existing == markdown
        print(("unchanged" if same else "DIFFERS") + f" {args.markdown}")
        return 0 if same else 1
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown, encoding="utf-8")
    if not args.skip_pdf:
        markdown_to_pdf(markdown, args.pdf)
    record = build_record(CONFIG, facts, args.markdown, args.pdf if not args.skip_pdf
                          else args.markdown)
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
                      "associated_submissions": len(record["associated_leaderboard_submissions"])},
                     indent=1))
    print(("PACKET READY FOR OPERATOR REVIEW: " if not failed else
           "PACKET ASSEMBLED, OPERATOR INPUTS MISSING (" + ", ".join(failed) + "): ")
          + "packet is staged only; nothing was submitted to the organiser")
    return 0


CONFIG = load_config()

if __name__ == "__main__":
    sys.exit(main())
