#!/usr/bin/env python3
"""Trace S2-specific writeup formulas and numbers to frozen, label-free reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHRINK_DOC = "docs/writeup/shrink-s2-assumption-bound.md"
FINAL_DOC = "docs/writeup/final-writeup.md"
COMPARISON = "reports/shrink-s2-assumption-bound-comparison.json"
MASK = "reports/s2-poi-facility-mask-endpoints.json"
BUILDING = "reports/s2-building-overlap-sensitivity.json"
FAMILY_S = "reports/bias60-family-s-summary.json"
REPORT_PATH = ROOT / "reports/s2-writeup-claim-evidence-audit.json"
EXPECTED_HASHES = {
    COMPARISON: "2328a34cae77509691fdd3a24608b925fc2b3d07e9a4d9e155bba71f1e507162",
    MASK: "2b17cec8db5b1289bb38e4c9aeac14dd7e466b7f897525b6262f8158fd45ec07",
    BUILDING: "6625dc305e8c29e70a0dcecca1efecda6ceb7d2deb36bcd015fb42fa327725f0",
    FAMILY_S: "ea1bdb11e7c358eb5ffc4b75a0a1a3c19d7f2b7bb84dce6453fb400643f68581",
}
REGIONS = {
    "maricopa-az": "Maricopa",
    "northern-ca": "Northern California",
    "eastern-ok": "Eastern Oklahoma",
    "south-central-tx": "South-Central Texas",
}
MODELS = {
    "S_crossmethod_reliability_shrink_v1": "S_crossmethod_reliability_shrink_v1",
    "S2_h1b_assumption_bound_v1": "S2_h1b_assumption_bound_v1",
    "C2_component_estimator_v0": "C2_component_estimator_v0",
}
TABLE_HEADER = "| Region | Model | Mean composite | Transport defined mean | Building defined mean | POI defined mean | Changed vs S | Changed vs C2 |"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def json_pointer(root: Any, pointer: str) -> Any:
    node = root
    if pointer in ("", "/"):
        return node
    for part in pointer.lstrip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(part)]
        else:
            node = node[part]
    return node


def read_json(root: Path, name: str) -> dict[str, Any]:
    return json.loads((root / name).read_text(encoding="utf-8"))


def fmt(value: float, digits: int) -> str:
    return f"{float(value):.{digits}f}"


def parse_markdown_table(text: str) -> list[list[str]]:
    rows = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == TABLE_HEADER:
            in_table = True
            continue
        if in_table:
            if not stripped.startswith("|"):
                break
            if re.fullmatch(r"\|[\s|:-]+\|", stripped):
                continue
            rows.append([cell.strip().strip("`") for cell in stripped.strip("|").split("|")])
    return rows


def compare_table_rows(text: str, report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    rows = parse_markdown_table(text)
    expected_count = len(REGIONS) * len(MODELS)
    if len(rows) != expected_count:
        errors.append(f"S2 comparison table has {len(rows)} data rows; expected {expected_count}")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if len(row) != 8:
            errors.append(f"malformed comparison table row: {row}")
            continue
        region, model, *values = row
        if region not in REGIONS or model not in MODELS:
            errors.append(f"unrecognized comparison row: {row}")
            continue
        key = (region, model)
        if key in seen:
            errors.append(f"duplicate comparison row: {key}")
        seen.add(key)
        source = report["side_by_side_by_region"][region]["models"][model]
        components = source["component_means"]
        expected = [fmt(source["mean_composite_level"], 6)]
        if components is None:
            expected.extend(["not identifiable"] * 3)
        else:
            expected.extend(fmt(components[name]["defined_mean"], 6)
                            for name in ("transport", "building", "poi"))
        expected.extend([fmt(source["share_tracts_changed_vs_S"], 3),
                         fmt(source["share_tracts_changed_vs_C2"], 3)])
        ok = values == expected
        if not ok:
            errors.append(f"table values differ from frozen report for {region}/{model}: {values} != {expected}")
        pointer = f"/side_by_side_by_region/{region}/models/{model}"
        entries.append({
            "claim_id": f"comparison-{region}-{model}",
            "document": SHRINK_DOC,
            "claim_quote": " | ".join(row),
            "claim_class": "quantitative_candidate_comparison",
            "numbers_verified": ok,
            "numeric_values_displayed": values,
            "evidence": {"path": COMPARISON, "sha256": EXPECTED_HASHES[COMPARISON], "json_pointer": pointer},
            "quoted_caveat": "These descriptive summaries are not validation against the organizer target.",
            "frozen_report_caveat": report["overall_interpretation"],
        })
    return entries, errors


def build_audit(root: Path = ROOT) -> dict[str, Any]:
    shrink_path, final_path = root / SHRINK_DOC, root / FINAL_DOC
    shrink_text = shrink_path.read_text(encoding="utf-8")
    final_text = final_path.read_text(encoding="utf-8")
    sources = {name: read_json(root, name) for name in EXPECTED_HASHES}
    source_hashes = {name: sha256_file(root / name) for name in EXPECTED_HASHES}
    errors: list[str] = []
    for name, expected in EXPECTED_HASHES.items():
        if source_hashes[name] != expected:
            errors.append(f"frozen source report hash mismatch: {name}")

    comparison = sources[COMPARISON]
    mask = sources[MASK]
    building = sources[BUILDING]
    family = sources[FAMILY_S]
    claims, table_errors = compare_table_rows(shrink_text, comparison)
    errors.extend(table_errors)

    # Verify that the frozen comparison binds the exact S2 artifact and refuses an accuracy claim.
    s2_sha = comparison["candidate"]["sha256"]
    if (s2_sha != "44ba76abcd9e1fb0aa7194e3c0fadf567debce40e29ed4bdf93ef02ae9514577"
            or comparison["candidate"]["target_accuracy_identifiable"] is not False
            or comparison["candidate"]["upload_authorized"] is not False):
        errors.append("comparison report does not bind the frozen unauthorized, accuracy-unidentified S2 artifact")
    candidate_claim_quote = next((line.strip() for line in shrink_text.splitlines()
                                  if "Frozen artifact:" in line), "")
    artifact_ok = s2_sha in candidate_claim_quote and COMPARISON in shrink_text and EXPECTED_HASHES[COMPARISON] in shrink_text
    if not artifact_ok:
        errors.append("frozen candidate hash lacks a direct frozen-report citation in the S2 writeup")
    claims.append({
        "claim_id": "frozen-s2-artifact-identity",
        "document": SHRINK_DOC,
        "claim_quote": candidate_claim_quote,
        "claim_class": "artifact_identity",
        "numbers_verified": artifact_ok,
        "numeric_values_displayed": [s2_sha],
        "evidence": {"path": COMPARISON, "sha256": EXPECTED_HASHES[COMPARISON], "json_pointer": "/candidate"},
        "quoted_caveat": "The S2 artifact is an assumption-bound construction; hidden-target accuracy is not identifiable.",
        "frozen_report_caveat": comparison["overall_interpretation"],
    })

    # Formula/assumption prose is checked against the frozen comparison's recorded recipe.
    recipe = comparison["recipe"]
    formula_checks = {
        "building share scaling stated": "one minus the per-tract source share" in shrink_text,
        "missing share behavior stated": "missing shares leave C2 unchanged" in shrink_text,
        "transport preserved from C2": "transport component remains unchanged from C2" in shrink_text,
        "facilities half uses mean of retained defined types": "facilities half is the arithmetic mean of retained defined facility types" in shrink_text,
        "establishments half stays feature-only proxy": "establishment half is the unchanged feature-only CBP proxy" in shrink_text,
        "POI averages whichever halves are defined": "POI is the mean of whichever halves are defined" in shrink_text,
        "overall score averages defined components": "final score is the mean of whichever transport, building, and POI components are defined" in shrink_text,
        "no fit or stacked S shrink stated": "There are no fitted coefficients and no S-style shrinkage is stacked on top" in shrink_text,
        "zero facility count premise disclosed": "zero Overture fire/EMS/school category count is treated as a zero USGS reference count" in shrink_text,
        "assumptions explicitly unverified": "Neither assumption is verified" in shrink_text,
        "target accuracy caveat explicit in S2 writeup": "neither these sensitivities nor agreement/differences among s2, s, and feature-only c2 establish target accuracy" in shrink_text.lower(),
        "target accuracy caveat explicit in final writeup": "neither sensitivity nor agreement among feature-only outputs establishes target accuracy" in final_text.lower(),
    }
    source_recipe_checks = {
        "frozen base is C2": "C2_component_estimator_v0" in recipe["base"],
        "transport unchanged": "preserve C2 transport_gap and transport_defined unchanged" == recipe["transport"],
        "facility averaging formula present": "arithmetic mean of retained defined type gaps" in recipe["poi_facilities"]["rule"],
        "zero-count facility assumption disclosed": "zero reference count" in recipe["poi_facilities"]["assumption"],
        "CBP half remains feature-only proxy": "internal feature-only proxy" in recipe["poi_establishments"],
        "POI averages defined halves": recipe["poi_composite"].startswith("mean of whichever facilities half and establishment half are defined"),
        "composite averages defined components": "arithmetic mean of the defined transport, adjusted building, and adjusted POI component values" in recipe["overall_composite"],
        "no fitted coefficients": recipe["coefficient_or_fit"].startswith("none; no fitted"),
    }
    formula_checks.update({f"frozen recipe {key}": value for key, value in source_recipe_checks.items()})
    if recipe["building"]["rule"] != "when share is finite and in [0,1], building_gap_S2 = building_gap_C2 * (1 - share); when share is missing, preserve building_gap_C2 unchanged":

        errors.append("frozen comparison building recipe changed unexpectedly")
    if not all(formula_checks.values()):
        errors.extend(f"missing or inconsistent formula/caveat statement: {key}" for key, ok in formula_checks.items() if not ok)
    claims.append({
        "claim_id": "formula-and-assumptions",
        "document": SHRINK_DOC,
        "claim_quote": shrink_text.splitlines()[2].strip(),
        "claim_class": "formula_and_assumptions",
        "numbers_verified": all(formula_checks.values()),
        "checks": formula_checks,
        "evidence": {"path": COMPARISON, "sha256": EXPECTED_HASHES[COMPARISON], "json_pointer": "/recipe"},
        "quoted_caveat": "Neither assumption is verified.",
        "frozen_report_caveat": comparison["overall_interpretation"],
    })

    # Validate and enumerate all four region-level zero-count mask claims.
    region_metric = {
        "maricopa-az": "delta_summary_vs_frozen_S2/tract_share_absolute_delta_gt_0_05",
        "northern-ca": "delta_summary_vs_frozen_S2/tract_share_absolute_delta_gt_0_05",
        "eastern-ok": "delta_summary_vs_frozen_S2/tract_share_absolute_delta_gt_0_05",
        "south-central-tx": "delta_summary_vs_frozen_S2/tract_share_absolute_delta_gt_0_05",
    }
    mask_line = next((line.strip() for line in shrink_text.splitlines()
                      if "fire/EMS/school components are restored" in line), "")
    region_order = ("eastern-ok", "maricopa-az", "northern-ca", "south-central-tx")
    mask_checks = {}
    for region in region_order:
        value = mask["per_region"][region]["delta_summary_vs_frozen_S2"]["tract_share_absolute_delta_gt_0_05"]
        token = f"{REGIONS[region]} {value:.1%}"
        mask_checks[region] = token in mask_line
        if not mask_checks[region]:
            errors.append(f"mask sensitivity value missing/mismatched in writeup: {region} {token}")
        claims.append({
            "claim_id": f"facility-mask-sensitivity-{region}",
            "document": SHRINK_DOC,
            "claim_quote": token,
            "claim_class": "quantitative_counterfactual_sensitivity",
            "numbers_verified": mask_checks[region],
            "numeric_values_displayed": [round(value * 100, 1)],
            "evidence": {"path": MASK, "sha256": EXPECTED_HASHES[MASK],
                         "json_pointer": f"/per_region/{region}/{region_metric[region]}"},
            "quoted_caveat": "These are mask-counterfactual sensitivities, not evidence that any score is closer to the hidden target.",
            "frozen_report_caveat": mask["interpretation"],
        })
    mask_caveat_ok = ("target_accuracy_identifiable" in mask["interpretation"]
                      and mask["interpretation"]["target_accuracy_identifiable"] is False
                      and "These are mask-counterfactual sensitivities, not evidence that any score is closer to the hidden target." in shrink_text)
    if not mask_caveat_ok:
        errors.append("facility-mask evidence lacks explicit sensitivity-not-accuracy caveat")

    # Building-share ±0.10 evidence: four regions, both offsets, and descriptive-only caveat.
    building_line = next((line.strip() for line in shrink_text.splitlines()
                          if "independent building-source-share sensitivity" in line), "")
    p95_max = 0.0
    max_abs = 0.0
    zero_over = True
    building_entries = []
    for region in region_order:
        for offset in ("+0.10", "-0.10"):
            summary = building["curve_by_region"][region]["scenarios"][offset]["summary"]
            p95_max = max(p95_max, summary["p95_abs_delta"])
            max_abs = max(max_abs, summary["max_abs_delta"])
            zero_over = zero_over and summary["share_abs_delta_gt_0_05"] == 0.0
            pointer = f"/curve_by_region/{region}/scenarios/{offset}/summary"
            ok = (summary["n"] == building["scope"]["region_counts"][region]
                  and summary["share_abs_delta_gt_0_05"] == 0.0)
            building_entries.append({
                "claim_id": f"building-share-offset-{region}-{offset.replace('+','plus').replace('-','minus').replace('.','p')}",
                "document": SHRINK_DOC,
                "claim_quote": f"{region} {offset} summary: p95={summary['p95_abs_delta']:.12g}; max={summary['max_abs_delta']:.12g}; share>0.05={summary['share_abs_delta_gt_0_05']}",
                "claim_class": "quantitative_counterfactual_sensitivity",
                "numbers_verified": ok,
                "numeric_values_displayed": [summary["p95_abs_delta"], summary["max_abs_delta"], summary["share_abs_delta_gt_0_05"]],
                "evidence": {"path": BUILDING, "sha256": EXPECTED_HASHES[BUILDING], "json_pointer": pointer},
                "quoted_caveat": "The source report labels this descriptive-only and says target accuracy is not identifiable.",
                "frozen_report_caveat": building["interpretation"],
            })
            if not ok:
                errors.append(f"building offset scope/threshold check failed: {region} {offset}")
    claims.extend(building_entries)
    building_text_checks = (
        f"p95 absolute score delta is at most {p95_max:.5f}" in building_line
        and f"maximum absolute delta at most {max_abs:.5f}" in building_line
        and "no tract exceeds the strict 0.05 delta threshold" in building_line
    )
    building_caveat_ok = (building["interpretation"]["descriptive_only"] is True
                          and building["interpretation"]["target_accuracy_identifiable"] is False
                          and "descriptive-only" in building_line
                          and "not identifiable" in building_line)
    if not building_text_checks or not building_caveat_ok or not zero_over:
        errors.append("building-share ±0.10 summary or descriptive-only caveat does not match frozen report")

    # The overloaded Family S S2 label and its counts must be defined and sourced in final writeup.
    channels = family["outputs"]["S2"]["channels"]
    family_rows = sum(channel["rows"] for channel in channels.values())
    family_channel_count = len(channels)
    family_defined_rows = {channel["defined_rows"] for channel in channels.values()}
    family_source_pointer = "/outputs/S2/channels"
    family_quote = next((line.strip() for line in final_text.splitlines()
                         if "S2 name clarification and target-accuracy caveat" in line), "")
    family_ok = (family_rows == 18758 and family_channel_count == 2 and family_defined_rows == {9379}
                 and "18,758 outputs, two channels, and 9,379 defined rows per channel" in family_quote
                 and FAMILY_S in family_quote and EXPECTED_HASHES[FAMILY_S] in family_quote)
    if not family_ok:
        errors.append("Family S S2 channel counts, label distinction, or report citation missing/mismatched")
    claims.append({
        "claim_id": "final-writeup-family-s-s2-channel",
        "document": FINAL_DOC,
        "claim_quote": family_quote,
        "claim_class": "quantitative_feature_channel_description",
        "numbers_verified": family_ok,
        "numeric_values_displayed": [family_rows, family_channel_count, *sorted(family_defined_rows)],
        "evidence": {"path": FAMILY_S, "sha256": EXPECTED_HASHES[FAMILY_S], "json_pointer": family_source_pointer},
        "quoted_caveat": "These are diagnostics and covariates, not a calibrated score or evidence about the hidden organiser target.",
        "frozen_report_caveat": "label-free summary of Family S per-tract artifacts and the orthogonality report",
    })

    # Ensure each source report and digest appears directly in the scoped writeup passages.
    for doc_name, text in ((SHRINK_DOC, shrink_text), (FINAL_DOC, final_text)):
        for source, digest in EXPECTED_HASHES.items():
            if source in text and digest not in text:
                errors.append(f"citation in {doc_name} lacks frozen digest for {source}")
    if (COMPARISON not in final_text or EXPECTED_HASHES[COMPARISON] not in final_text
            or MASK not in final_text or EXPECTED_HASHES[MASK] not in final_text
            or BUILDING not in final_text or EXPECTED_HASHES[BUILDING] not in final_text):
        errors.append("final writeup S2 clarification is missing direct report path/hash citations")

    for claim in claims:
        source_name = claim["evidence"]["path"]
        if source_hashes.get(source_name) != claim["evidence"]["sha256"]:
            claim["numbers_verified"] = False
            errors.append(f"claim source hash is not frozen/verified: {claim['claim_id']}")
    quantitative_records = [c for c in claims if c["claim_class"].startswith("quantitative")
                            or c["claim_class"] == "artifact_identity"]
    unsupported_quantitative = [c["claim_id"] for c in quantitative_records if not c["numbers_verified"]]
    unsupported = [*unsupported_quantitative, *errors]
    caveat_found = ("neither sensitivity nor agreement among feature-only outputs establishes target accuracy" in final_text.lower()
                    and "neither these sensitivities nor agreement/differences among s2, s, and feature-only c2 establish target accuracy" in shrink_text.lower()
                    and comparison["candidate"]["target_accuracy_identifiable"] is False
                    and mask["interpretation"]["target_accuracy_identifiable"] is False
                    and building["interpretation"]["target_accuracy_identifiable"] is False)
    if not caveat_found:
        errors.append("explicit target-accuracy caveat missing from writeups or frozen reports")
    return {
        "schema_version": 1,
        "analysis_id": "S2_WRITEUP_CLAIM_EVIDENCE_AUDIT_V1",
        "result": "PASS" if not errors else "FAIL",
        "scope": {"documents": [SHRINK_DOC, FINAL_DOC],
                  "focus": "S2 formulae, assumptions, numeric comparison table, measured mask/building sensitivities, and the Family S S2 feature-channel passage",
                  "outside_scope": "all non-S2 claims in final-writeup.md"},
        "measure_before": {"S2_specific_claim_matrix_present": False,
                           "S2 candidate is assumption-bound": True,
                           "target_accuracy_identifiable": False},
        "source_reports": {name: {"sha256": source_hashes[name], "expected_sha256": EXPECTED_HASHES[name],
                                   "hash_verified": source_hashes[name] == EXPECTED_HASHES[name]}
                           for name in EXPECTED_HASHES},
        "documents": {name: {"sha256": sha256_file(root / name)} for name in (SHRINK_DOC, FINAL_DOC)},
        "audit_script_sha256": sha256_file(Path(__file__).resolve()),
        "claim_matrix": claims,
        "acceptance": {
            "quantitative_claim_count": len(quantitative_records),
            "quantitative_claims_with_direct_report_path_hash_and_pointer": len(quantitative_records) - len(unsupported_quantitative),
            "unsupported_quantitative_or_accuracy_claim_count": len(unsupported),
            "unsupported_claims": unsupported,
            "comparison_table_rows_verified": len([c for c in claims if c["claim_class"] == "quantitative_candidate_comparison" and c["numbers_verified"]]),
            "formula_and_assumptions_cross_checked": all(formula_checks.values()),
            "all_four_facility_mask_region_claims_verified": all(mask_checks.values()) and mask_caveat_ok,
            "all_eight_building_offset_region_claims_verified": all(c["numbers_verified"] for c in building_entries) and building_text_checks,
            "family_s_s2_label_and_counts_verified": family_ok,
            "explicit_sensitivity_not_accuracy_statement": caveat_found,
            "all_frozen_source_report_hashes_verified": all(source_hashes[n] == EXPECTED_HASHES[n] for n in EXPECTED_HASHES),
            "candidate_unchanged": True,
            "upload_performed": False,
        },
        "safety": {"labels_or_removed_references_used": False, "external_data_used": False,
                   "leaderboard_or_scorecard_values_used": False, "candidate_edits": 0,
                   "uploads": 0, "target_accuracy_inferred": False},
        "reproduction": {"command": "uv run --with-requirements requirements.txt python scripts/audit_s2_writeup_claim_evidence.py",
                         "tests": "uv run --with-requirements requirements.txt --with pytest python -m pytest -q tests/test_s2_writeup_claim_evidence.py"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    report = build_audit()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "claim_records": len(report["claim_matrix"]),
                      "unsupported_claims": report["acceptance"]["unsupported_quantitative_or_accuracy_claim_count"],
                      "report": str(args.out), "errors": report["acceptance"]["unsupported_claims"]}, sort_keys=True))
    if report["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
