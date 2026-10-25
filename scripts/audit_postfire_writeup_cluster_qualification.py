#!/usr/bin/env python3
"""Surface county-cluster uncertainty wherever the post-fire difference is claimed.

The preregistered county-cluster recheck of the published post-fire finding (
``reports/discovery-postfire-county-bootstrap.json``) widened the 95% interval of the high-burn minus
unburned difference from [+0.55, +20.89] to [-1.65, +16.67] percentage points and returned the verdict
``exploratory``. This screen checks the two organiser-facing writeups mechanically:

* every sentence that talks about that difference *and* uses an inferential, confidence or robustness
  cue must be qualified by the clustered result in the same sentence, so a reader cannot take
  tract-resampling sensitivity for clustered inference;
* every numeric literal quoted for the finding must agree with the frozen reports at the declared
  precision, and no interval literal may appear that the frozen reports do not contain;
* the qualification must survive assembly of the generated writeup, and the frozen design (cutoff,
  replicates, seed, cluster count, verdict) must be unchanged, so the prose can only ever be qualified
  to the design that was preregistered before results were seen.

It reads only the two writeups and three committed artifacts. It re-runs no resampling, reads no
reference layer, label, leaderboard value or platform endpoint, and uploads nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OPENED: list[str] = []


def read_text(path: Path) -> str:
    """Single funnel for file access so the audit can prove what it opened."""
    OPENED.append(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path))
    return path.read_text(encoding="utf-8")


def read_bytes(path: Path) -> bytes:
    OPENED.append(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path))
    return path.read_bytes()

from bias60.determinism import canonical_bytes, normalized_bytes, wall_clock_hits  # noqa: E402

DEFAULT_CONFIG = ROOT / "config/postfire-writeup-cluster-qualification-preregistered.json"


# --------------------------------------------------------------------------- helpers
def sha256_file(path: Path) -> str:
    return hashlib.sha256(read_bytes(path)).hexdigest()


def resolve(value: Any, pointer: str) -> Any:
    node = value
    for part in pointer.split("."):
        node = node[part]
    return node


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))  # the configuration is policy text, not a scan target
    config["__path__"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    config["__sha256__"] = hashlib.sha256(canonical_bytes(strip_meta(config))).hexdigest()
    return config


def strip_meta(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if not key.startswith("__")}


def load_frozen(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the three pinned artifacts, refusing a hash mismatch (fail closed)."""
    documents: dict[str, Any] = {}
    integrity: dict[str, Any] = {}
    for name, entry in config["frozen_reports"].items():
        path = ROOT / entry["path"]
        actual = sha256_file(path)
        if actual != entry["sha256"]:
            raise ValueError(f"frozen artifact {entry['path']} no longer matches its pinned SHA-256: "
                             f"{actual} != {entry['sha256']}; the writeup may not be qualified to a "
                             f"report that changed underneath it")
        integrity[name] = {"path": entry["path"], "pinned_sha256": entry["sha256"],
                           "actual_sha256": actual, "sha256_matches_pin": True}
        if not entry["path"].endswith(".json") or name == "county_bootstrap_config":
            documents[name] = None
            continue
        documents[name] = json.loads(read_text(path))
    return documents, integrity


def finding_block(text: str, doc: dict[str, Any]) -> str:
    start = text.find(doc["block_start_anchor"])
    if start < 0:
        raise ValueError(f"cannot locate the post-fire finding block in {doc['path']}")
    end = text.find(doc["block_end_anchor"], start + 1)
    if end < 0:
        raise ValueError(f"cannot find the end of the post-fire finding block in {doc['path']}")
    return text[start:end]


def normalise_block(block: str) -> str:
    """Ignore heading depth (the generated writeup demotes every heading by one level)."""
    lines = [re.sub(r"^#{1,6}\s+", "# ", line).strip() for line in block.splitlines()]
    return "\n".join(line for line in lines if line)


def split_sentences(block: str) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = []
    for paragraph_index, paragraph in enumerate(p.strip() for p in block.split("\n\n")):
        if not paragraph:
            continue
        for sentence_index, sentence in enumerate(re.split(r"(?<=[.!?])[ \t\n]+", paragraph)):
            statements.append({"paragraph": paragraph_index, "index": sentence_index,
                               "text": sentence.strip()})
    return statements


def numbers_of(text: str) -> list[str]:
    """Numeric tokens only, so a captured "95.99" agrees with the literal "95.99%".

    The sign is kept: [+0.55] and [-0.55] are different claims about the same statistic.
    """
    return re.findall(r"[+-]?\d+(?:\.\d+)?", text)


def any_match(patterns: list[str], text: str) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE)]


def is_affirmative(sentence: str, pattern: str, negation_markers: list[str]) -> bool:
    """True when the pattern is asserted, not disclosed.

    A disclosure such as "it does **not** claim that the organiser used this field" quotes a prohibited
    idea in order to refuse it, so the negation is checked inside the clause that carries the match
    rather than anywhere in the sentence.
    """
    for match in re.finditer(pattern, sentence, re.IGNORECASE):
        boundaries = [sentence.rfind(delimiter, 0, match.start()) for delimiter in ",;:"]
        clause_start = (max(boundaries) + 1) if max(boundaries) >= 0 else 0
        clause = sentence[clause_start:match.end()]
        if not any(marker in clause for marker in negation_markers):
            return True
    return False


def classify(statements: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    rules = config["classification"]
    out: list[dict[str, Any]] = []
    for item in statements:
        text = item["text"]
        subject = any_match(rules["subject_patterns"], text)
        cues = any_match(rules["inferential_cue_patterns"], text)
        exempt = any_match(rules["exemption_patterns"], text)
        qualified = any_match(rules["cluster_qualification_patterns"], text)
        inferential = bool(subject) and bool(cues)
        out.append({**item, "subject_patterns": subject, "inferential_cues": cues,
                    "exemption_patterns": exempt, "cluster_qualification": qualified,
                    "is_inferential_statement": inferential and not exempt,
                    "qualified": (not inferential) or bool(qualified) or bool(exempt),
                    "unqualified": bool(inferential) and not qualified and not exempt})
    return out


def frozen_interval_literal(values: list[float], config_format: str, scale: float,
                            places: int) -> str:
    scaled = [float(value) * scale for value in values]
    rounded = [round(value, places) for value in scaled]
    return config_format.format(*rounded)


def derived_literals(stats: list[dict[str, Any]], frozen: dict[str, Any]) -> dict[str, str]:
    literals: dict[str, str] = {}
    for stat in stats:
        value = resolve(frozen[stat["report"]], stat["pointer"])
        places = stat.get("round", 2)
        scale = stat.get("scale", 1.0)
        if isinstance(value, list):
            literals[stat["name"]] = frozen_interval_literal(value, stat["format"], scale, places)
        else:
            literals[stat["name"]] = stat["format"].format(float(value) * scale)
    return literals


# --------------------------------------------------------------------------- tampering
TAMPER_MUTATIONS = ("strip_cluster_interval", "strip_exploratory_verdict", "unqualify_first_mention",
                    "perturb_cluster_bound", "drop_design_contrast", "generated_block_diverges",
                    "cutoff_retuned", "seed_or_replicates_changed", "causal_claim")


def apply_tamper(tamper: str, documents: dict[str, Any], texts: dict[str, str],
                 literals: dict[str, str], config: dict[str, Any]) -> list[str]:
    """Mutate in-memory copies only; committed files are never written by a tamper run."""
    applied: list[str] = []
    if tamper not in TAMPER_MUTATIONS:
        raise ValueError(f"unknown tamper {tamper!r}")
    cluster = literals["county_cluster_interval_percentage_points"]
    contrast = config["qualification"]["contrast_requirement"]["contrast_phrases"][0]
    if tamper == "strip_cluster_interval":
        for doc_id, text in texts.items():
            texts[doc_id] = text.replace(cluster, "[the clustered interval]")
        applied.append("cluster interval literal removed from both writeups")
    elif tamper == "strip_exploratory_verdict":
        for doc_id, text in texts.items():
            texts[doc_id] = text.replace("exploratory", "reported")
        applied.append("word exploratory removed from both writeups")
    elif tamper == "unqualify_first_mention":
        for doc_id, text in texts.items():
            for pattern in config["classification"]["cluster_qualification_patterns"]:
                text = re.sub(pattern, "", text)
            texts[doc_id] = text
        applied.append("cluster qualification stripped from every sentence")
    elif tamper == "perturb_cluster_bound":
        for doc_id, text in texts.items():
            texts[doc_id] = text.replace("-1.65", "+0.65")
        applied.append("cluster lower bound rewritten to +0.65 in both writeups")
    elif tamper == "drop_design_contrast":
        for doc_id, text in texts.items():
            paragraph_start = text.find(contrast)
            if paragraph_start < 0:
                applied.append("contrast sentence already absent")
                continue
            start = text.rfind(". ", 0, paragraph_start)
            start = 0 if start < 0 else start + 2
            end = text.find(". ", paragraph_start)
            end = len(text) if end < 0 else end + 1
            texts[doc_id] = text[:start] + text[end:]
        applied.append("sentence contrasting the two resampling designs deleted")
    elif tamper == "generated_block_diverges":
        generated = [doc["id"] for doc in config["documents"]
                     if doc.get("role") == "generated_from_discovery"]
        for doc_id in generated:
            texts[doc_id] = texts[doc_id].replace("provided Overture building count",
                                                 "provided Overture building counts", 1)
        applied.append("generated writeup block diverged from its source document")
    elif tamper == "cutoff_retuned":
        report = documents["county_bootstrap"]
        report["analysis_population"]["cutoff_used"] = 0.75
        report["analysis_population"]["cutoff_values_permitted"] = [0.75]
        applied.append("frozen report claims the analysis used a 0.75 cutoff")
    elif tamper == "seed_or_replicates_changed":
        report = documents["county_bootstrap"]
        report["county_cluster_bootstrap"]["replicates"] = 25000
        report["county_cluster_bootstrap"]["valid_replicates"] = 25000
        report["county_cluster_bootstrap"]["positive_difference_replicates"] = 24000
        report["county_cluster_bootstrap"]["seed"] = 20261099
        applied.append("frozen report claims 25,000 replicates and a different cluster seed")
    elif tamper == "causal_claim":
        for doc_id, text in texts.items():
            marker = "## Reproducibility" if "## Reproducibility" in text else "### Reproducibility"
            insertion = ("\nThe 2018 fire caused the building-count gap in these tracts, which proves "
                         "that the reference layer is stale and that the organiser scores these tracts "
                         "as a true coverage gap.\n\n")
            texts[doc_id] = text.replace(marker, insertion + marker, 1)
        applied.append("affirmative causal and hidden-target sentence added")
    return applied


# --------------------------------------------------------------------------- audit
def claim_scope(block: str, doc: dict[str, Any]) -> str:
    """The part of the finding where claims live: Status, Impact, Novelty and Evidence.

    The Reproducibility subsection describes what the audit checks, which is prose about machinery,
    not an inferential claim about the difference, so it is excluded from statement classification
    while still being searched for the required numbers.
    """
    anchor = doc.get("claim_scope_end_anchor")
    if not anchor:
        return block
    position = block.find(anchor)
    return block if position < 0 else block[:position]


def audit_document(doc: dict[str, Any], text: str, literals: dict[str, str],
                   config: dict[str, Any]) -> dict[str, Any]:
    block = finding_block(text, doc)
    claims = claim_scope(block, doc)
    statements = classify(split_sentences(claims), config)
    rules = config["classification"]
    qualification = config["qualification"]
    contrast = qualification["contrast_requirement"]

    inferential = [item for item in statements if item["is_inferential_statement"]]
    unqualified = [item for item in statements if item["unqualified"]]

    interval_pattern = config["interval_literal_pattern"]
    allowed_intervals = {literals["tract_bootstrap_interval_percentage_points"],
                         literals["county_cluster_interval_percentage_points"]}
    found_intervals = [f"[{low}, {high}]" for low, high in re.findall(interval_pattern, block)]
    unfrozen = sorted(set(found_intervals) - allowed_intervals)

    missing_fragments = sorted(fragment for fragment in qualification["near_finding_fragments"]
                              if fragment not in block)

    # The first mention of the point difference must carry the clustered interval and the verdict.
    first_mention = next((item for item in statements
                          if qualification["point_difference_literal"] in item["text"]), None)
    first_mention_ok = bool(first_mention) and (
        literals["county_cluster_interval_percentage_points"] in first_mention["text"]
        and "exploratory" in first_mention["text"].lower())

    def has_contrast(sentence: str) -> bool:
        return bool(any_match(contrast["tract_groups"], sentence)
                    and any_match(contrast["cluster_groups"], sentence)
                    and any_match(contrast["contrast_phrases"], sentence))

    contrast_sentence = next((item["text"] for item in statements if has_contrast(item["text"])),
                             None)

    language = config["language"]
    lowered_block = block.lower()
    causal_hits = []
    for pattern in language["forbidden_patterns"] + language["hidden_target_patterns"]:
        for sentence in re.split(r"(?<=[.!?])[ \t\n]+", lowered_block):
            if is_affirmative(sentence, pattern, language["negation_markers"]):
                causal_hits.append({"pattern": pattern, "sentence": sentence[:240]})
    missing_hedges = sorted(hedge for hedge in language["required_hedges"]
                            if hedge not in lowered_block)

    statistics = {}
    for stat in config["frozen_statistics"]:
        literal = literals[stat["name"]]
        capture = stat.get("capture_pattern")
        minimum = stat["min_occurrences"]
        if capture:
            # A positional capture proves the number is quoted *in the place* the statistic belongs to,
            # so a stray copy elsewhere in the document cannot satisfy it.
            captured = [value if isinstance(value, str) else "".join(value)
                        for value in re.findall(capture, block)]
            agree = [value for value in captured if numbers_of(value) == numbers_of(literal)]
            statistics[stat["name"]] = {"expected_literal": literal, "captured": captured,
                                        "captures_agree": len(agree) >= minimum,
                                        "occurrences": len(agree), "min_occurrences": minimum,
                                        "capture_pattern": capture}
        else:
            occurrences = block.count(literal)
            statistics[stat["name"]] = {"expected_literal": literal, "occurrences": occurrences,
                                        "captures_agree": occurrences >= minimum,
                                        "min_occurrences": minimum}

    population_missing = sorted(label for label in config["population_literals"].values()
                               if label not in block)

    return {
        "id": doc["id"],
        "path": doc["path"],
        "role": doc["role"],
        "block_chars": len(block),
        "claim_scope_chars": len(claims),
        "claim_scope_end_anchor": doc.get("claim_scope_end_anchor"),
        "statements_examined": len(statements),
        "inferential_statements": len(inferential),
        "unqualified_statements": len(unqualified),
        "unqualified_statement_text": [item["text"] for item in unqualified],
        "inferential_statement_text": [{"text": item["text"],
                                        "cues": item["inferential_cues"],
                                        "qualification": item["cluster_qualification"]}
                                       for item in inferential],
        "interval_literals_found": found_intervals,
        "unfrozen_interval_literals": unfrozen,
        "missing_near_finding_fragments": missing_fragments,
        "first_mention": first_mention["text"] if first_mention else None,
        "first_mention_carries_cluster_interval_and_verdict": bool(first_mention_ok),
        "design_contrast_sentence": contrast_sentence,
        "causal_or_hidden_target_hits": causal_hits,
        "missing_hedges": missing_hedges,
        "frozen_statistics": statistics,
        "missing_population_literals": population_missing,
    }


def build_report(config: dict[str, Any], *, tamper: str | None = None) -> dict[str, Any]:
    frozen, integrity = load_frozen(config)
    texts = {doc["id"]: read_text(ROOT / doc["path"]) for doc in config["documents"]}
    literals = derived_literals(config["frozen_statistics"], frozen)
    applied = apply_tamper(tamper, frozen, texts, literals, config) if tamper else []

    audited = [audit_document(doc, texts[doc["id"]], literals, config)
               for doc in config["documents"]]

    pin = config["preregistration_pin"]
    cluster_report = frozen["county_bootstrap"]
    cluster = cluster_report["county_cluster_bootstrap"]
    tract = cluster_report["published_tract_level_reference"]
    analysis = cluster_report["analysis_population"]
    robustness = cluster_report["robustness"]
    drift = {
        "cutoff_used": analysis["cutoff_used"],
        "cutoff_values_permitted": analysis["cutoff_values_permitted"],
        "cluster_replicates": cluster["replicates"],
        "cluster_seed": cluster["seed"],
        "cluster_count": cluster["cluster_count"],
        "tract_replicates": tract["replicates"],
        "tract_seed": tract["seed"],
        "verdict": robustness["verdict"],
        "quantitative_claim_retained": robustness["quantitative_claim_retained"],
        "expected": pin,
    }
    drift_ok = (
        drift["cutoff_used"] == pin["cutoff"]
        and drift["cutoff_values_permitted"] == [pin["cutoff"]]
        and drift["cluster_replicates"] == pin["cluster_replicates"]
        and drift["cluster_seed"] == pin["cluster_seed"]
        and drift["cluster_count"] == pin["cluster_count"]
        and drift["tract_replicates"] == pin["tract_replicates"]
        and drift["tract_seed"] == pin["tract_seed"]
        and drift["verdict"] == pin["verdict"]
        and drift["quantitative_claim_retained"] is pin["quantitative_claim_retained"])

    source_blocks = {doc["id"]: normalise_block(finding_block(texts[doc["id"]], doc))
                     for doc in config["documents"]}
    generated_pairs = [(doc["id"], doc["derived_from"]) for doc in config["documents"]
                       if doc.get("role") == "generated_from_discovery"]
    generated_matches = {}
    for generated_id, derived_from in generated_pairs:
        source_id = next(doc["id"] for doc in config["documents"] if doc["path"] == derived_from)
        generated_matches[f"{generated_id}=={source_id}"] = (
            source_blocks[generated_id] == source_blocks[source_id])

    access = access_scan(config, OPENED)

    gates: dict[str, Any] = {
        "documents_located": all(entry["block_chars"] > 0 for entry in audited),
        "unqualified_inferential_statements_zero": all(
            entry["unqualified_statements"] == 0 for entry in audited),
        "classifier_is_not_vacuous": all(
            entry["inferential_statements"] >= config["classification"][
                "minimum_inferential_statements_per_document"] for entry in audited),
        "frozen_interval_literals_present": all(
            all(literal in finding_block(texts[entry["id"]],
                                         next(d for d in config["documents"]
                                             if d["id"] == entry["id"]))
                for literal in (literals["tract_bootstrap_interval_percentage_points"],
                                literals["county_cluster_interval_percentage_points"]))
            for entry in audited),
        "no_unfrozen_interval_literal": all(not entry["unfrozen_interval_literals"]
                                           for entry in audited),
        "near_finding_fragments_present": all(not entry["missing_near_finding_fragments"]
                                             for entry in audited),
        "exploratory_verdict_near_finding": all(
            "exploratory" in finding_block(texts[entry["id"]],
                                           next(d for d in config["documents"]
                                               if d["id"] == entry["id"]))
            for entry in audited),
        "first_mention_carries_cluster_interval_and_verdict": all(
            entry["first_mention_carries_cluster_interval_and_verdict"] for entry in audited),
        "tract_and_cluster_designs_distinguished": all(
            entry["design_contrast_sentence"] for entry in audited),
        "frozen_statistics_agree": all(
            stat["captures_agree"] for entry in audited
            for stat in entry["frozen_statistics"].values()),
        "population_literals_present": all(not entry["missing_population_literals"]
                                           for entry in audited),
        "preregistered_design_not_drifted": bool(drift_ok),
        "frozen_report_hashes_match_pin": all(
            entry["sha256_matches_pin"] for entry in integrity.values()),
        "generated_writeup_matches_source_block": all(
            value for value in generated_matches.values()) if generated_matches else False,
        "no_causal_or_hidden_target_claim": all(not entry["causal_or_hidden_target_hits"]
                                               for entry in audited),
        "required_hedges_present": all(not entry["missing_hedges"] for entry in audited),
        "only_allowlisted_files_opened": not access["unexpected_opened_paths"],
        "no_forbidden_path_opened": not access["forbidden_opened_paths"],
    }
    gates["all_passed"] = all(bool(value) for value in gates.values())

    report: dict[str, Any] = {
        "preregistration": {
            "audit_id": config["audit_id"],
            "backlog_item": config["backlog_item"],
            "lane": config["lane"],
            "title": config["title"],
            "intent": config["intent"],
            "config_path": config["__path__"],
            "config_sha256": config["__sha256__"],
            "prohibitions": config["prohibitions"],
            "negative_controls_declared": [case["id"] for case in config["negative_controls"]],
            "tamper_applied": tamper,
            "tamper_effects": applied,
        },
        "frozen_inputs": {
            "integrity": integrity,
            "derived_literals": literals,
            "classification_rules": config["classification"],
            "qualification": config["qualification"],
            "language": config["language"],
        },
        "design_drift": drift,
        "generated_against_source": generated_matches,
        "documents": audited,
        "unqualified_statements": [
            {"document": entry["id"], "text": text}
            for entry in audited for text in entry["unqualified_statement_text"]],
        "access": access,
        "gates": gates,
        "summary": {
            "documents_audited": len(audited),
            "statements_examined": sum(entry["statements_examined"] for entry in audited),
            "inferential_statements": sum(entry["inferential_statements"] for entry in audited),
            "unqualified_inferential_statements": sum(entry["unqualified_statements"]
                                                     for entry in audited),
            "gates_all_passed": gates["all_passed"],
        },
    }
    report["summary"]["report_sha256"] = hashlib.sha256(normalized_bytes(report)).hexdigest()
    value_hits = value_scan(report, config)
    report["forbidden_value_scan"] = value_hits
    report["gates"]["no_forbidden_value_in_report"] = not value_hits
    report["gates"]["report_is_strict_json"] = "NaN" not in json.dumps(report)
    report["gates"]["report_has_no_temporal_field"] = not wall_clock_hits(
        {"documents": audited, "design_drift": drift})
    report["gates"]["all_passed"] = all(bool(value) for value in report["gates"].values())
    report["summary"]["gates_all_passed"] = report["gates"]["all_passed"]
    report["summary"]["report_sha256"] = hashlib.sha256(normalized_bytes(report)).hexdigest()
    return report


def access_scan(config: dict[str, Any], opened: list[str]) -> dict[str, Any]:
    """Prove the audit opened nothing but the two writeups and the three pinned artifacts.

    Prohibitions are checked at the point of access, which is falsifiable, rather than in prose, where
    naming a forbidden layer in order to forbid it would count as a hit.
    """
    scan = config["forbidden_source_scan"]
    allowlist = set(scan["opened_paths_allowlist"])
    unique = sorted(set(opened))
    unexpected = sorted(path for path in unique if path not in allowlist)
    forbidden_opened = [{"path": path, "token": token} for path in unique
                        for token in scan["path_tokens"] if token.lower() in path.lower()]
    return {"opened_paths": unique, "allowlist": sorted(allowlist),
            "unexpected_opened_paths": unexpected, "forbidden_opened_paths": forbidden_opened,
            "disclosure": "path tokens are matched against opened paths, never against prose"}


def value_scan(report: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """Leaf-wise scan of the emitted report for leaderboard or probe constants.

    Comparing leaf by leaf keeps an ordinary measurement such as 0.0955 from self-hitting the
    leaderboard constant 0.0911 by substring.
    """
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, f"{path}/{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            for token in config["forbidden_source_scan"]["value_tokens"]:
                try:
                    if float(token) == float(node):
                        hits.append(f"{path}:{token}")
                except ValueError:
                    continue
        elif isinstance(node, str):
            for token in config["forbidden_source_scan"]["value_tokens"]:
                if re.fullmatch(re.escape(token), node.strip()):
                    hits.append(f"{path}:{token}")

    walk(report, "")
    return hits


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['preregistration']['title']}", ""]
    lines.append(f"- Audit: `{report['preregistration']['audit_id']}` "
                 f"(backlog item `{report['preregistration']['backlog_item']}`)")
    lines.append(f"- Preregistered configuration: `{report['preregistration']['config_path']}` "
                 f"(SHA-256 `{report['preregistration']['config_sha256'][:16]}...`)")
    verdict = "PASS" if report["gates"]["all_passed"] else "FAIL"
    lines.append(f"- Verdict: **{verdict}** — every inferential statement about the high-burn versus "
                 f"unburned difference carries the county-cluster result, and every quoted number "
                 f"agrees with the frozen reports")
    lines.append("")
    lines.append("## Verdict legend")
    lines.append("")
    lines.append("| Gate | Result |")
    lines.append("|---|---|")
    for gate, value in report["gates"].items():
        lines.append(f"| `{gate}` | {'pass' if value else 'FAIL'} |")
    lines.append("")
    lines.append("## Measured qualification per document")
    lines.append("")
    lines.append("| Document | Statements examined | Inferential statements | Unqualified |")
    lines.append("|---|---:|---:|---:|")
    for entry in report["documents"]:
        lines.append(f"| `{entry['path']}` | {entry['statements_examined']} | "
                     f"{entry['inferential_statements']} | {entry['unqualified_statements']} |")
    lines.append("")
    lines.append("## Frozen values the prose must agree with")
    lines.append("")
    for name, literal in report["frozen_inputs"]["derived_literals"].items():
        lines.append(f"- `{name}` = **{literal}**")
    lines.append("")
    lines.append("## Design integrity")
    lines.append("")
    drift = report["design_drift"]
    lines.append(f"- cutoff used: {drift['cutoff_used']} (permitted: {drift['cutoff_values_permitted']})")
    lines.append(f"- cluster design: {drift['cluster_replicates']} replicates, seed "
                 f"{drift['cluster_seed']}, {drift['cluster_count']} counties")
    lines.append(f"- tract reference: {drift['tract_replicates']} replicates, seed {drift['tract_seed']}")
    lines.append(f"- preregistered verdict: `{drift['verdict']}`, quantitative claim retained: "
                 f"{drift['quantitative_claim_retained']}")
    lines.append("")
    lines.append("## The sentence that distinguishes the two resampling designs")
    lines.append("")
    for entry in report["documents"]:
        lines.append(f"- `{entry['path']}`: {entry['design_contrast_sentence'] or 'MISSING'}")
    lines.append("")
    lines.append("## First mention of the difference")
    lines.append("")
    for entry in report["documents"]:
        lines.append(f"- `{entry['path']}`: {entry['first_mention'] or 'MISSING'}")
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out", default=None)
    parser.add_argument("--markdown", default=None)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--tamper", default=None, choices=list(TAMPER_MUTATIONS) + [None])
    parser.add_argument("--print-summary", action="store_true", default=True)
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    report = build_report(config, tamper=args.tamper)

    out_path = ROOT / (args.out or config["outputs"]["report"])
    markdown_path = ROOT / (args.markdown or config["outputs"]["markdown"])
    payload = canonical_bytes(report)

    if args.check_only:
        ok = out_path.is_file() and json.loads(out_path.read_text(encoding="utf-8")) is not None
        if ok:
            recorded = json.loads(out_path.read_text(encoding="utf-8"))
            recorded.get("summary", {}).pop("report_sha256", None)
            current = json.loads(payload.decode("utf-8"))
            current.get("summary", {}).pop("report_sha256", None)
            ok = normalized_bytes(recorded) == normalized_bytes(current)
        print(("unchanged " if ok else "CHANGED ") + str(out_path))
        return 0 if ok and report["gates"]["all_passed"] else 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    if args.print_summary:
        print(json.dumps(report["summary"], indent=2, sort_keys=True))
        failed = [gate for gate, value in report["gates"].items() if not value]
        if failed:
            print("failed gates: " + ", ".join(sorted(failed)))
    if report["gates"]["all_passed"]:
        print("PASS post-fire cluster qualification: every inferential statement about the "
              "high-burn versus unburned difference carries the county-cluster interval and the "
              "exploratory verdict at first mention, both writeups agree with the frozen reports, "
              "and the preregistered design is unchanged")
        return 0
    print("FAIL post-fire cluster qualification")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
