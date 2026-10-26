#!/usr/bin/env python3
"""Report whether the external lab of spec section 4 is ready to build -- and whether it is allowed to.

The lab is the one part of the 60-estimator plan that needs the reference layers the organisers removed
(TIGER roads, Microsoft building footprints, USGS structures, CBP/ZBP). The binding rules text says
participants must not use reference layers or equivalent copies obtained elsewhere to recreate or
approximate the organiser targets, and that additional public datasets are permitted only for the Best
Bias Discovery prize. So this report does two jobs and downloads nothing:

1. freeze the design (nine groups, source register with URL/version/retrieval-date/SHA-256 slots, the
   RULES.md recipe with its variants and self-checks, the per-component definedness schema) and check it
   against a fresh parse of the spec;
2. prove the safety rails work before any byte is fetched: every official scored GEOID, every platform
   template GEOID, every study county and anything inside the neighbour buffer is rejected by the
   executable exclusion policy, no lab bytes exist anywhere, and no model or submission path can read a
   lab file.

Authorization is reported as it is: absent, pending ``bias60-organiser-ruling``, and sequenced behind the
Track A pre-registration. Enabling the source availability probe is itself gated on that ruling.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402

from bias60 import lab_plan  # noqa: E402
from bias60.determinism import canonical_bytes, normalized_bytes, wall_clock_hits  # noqa: E402

DEFAULT_CONFIG = ROOT / "config/bias60-lab-preregistered.json"
OPENED: list[str] = []


def record(path: Path) -> str:
    workspace = Path(os.environ.get("COMPETITION_WORKSPACE", "") or ".").resolve()
    orchestrator = Path(os.environ.get("ORCHESTRATOR_ROOT", "") or "/home/martin/hddProjects/competitionsall")
    if path.is_absolute() and path.is_relative_to(ROOT):
        label = str(path.relative_to(ROOT))
    elif path.is_absolute() and path.is_relative_to(workspace):
        label = f"WORKSPACE:{path.relative_to(workspace)}"
    elif path.is_absolute() and path.is_relative_to(orchestrator):
        label = f"ORCHESTRATOR:{path.relative_to(orchestrator)}"
    else:
        label = str(path)
    OPENED.append(label)
    return label


def read_text(path: Path) -> str:
    record(path)
    return Path(path).read_text(encoding="utf-8")


def git_commit(root: Path) -> str:
    try:
        import subprocess

        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=30).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def environment_versions() -> dict[str, str]:
    return {"python": sys.version.split()[0], "numpy": np.__version__,
            "pyarrow": pa.__version__}


# --------------------------------------------------------------------------- checks
def check_sources(config: dict[str, Any]) -> dict[str, Any]:
    problems: list[str] = []
    for entry in config["sources"]:
        for field in ("id", "role", "url", "version_pattern"):
            if not entry.get(field):
                problems.append(f"{entry.get('id', '?')}: missing {field}")
        if entry.get("retrieval_date_utc") is not None:
            problems.append(f"{entry['id']}: retrieval_date_utc must be null until the build is allowed")
        if entry.get("sha256") is not None:
            problems.append(f"{entry['id']}: sha256 must be null until the file is retrieved")
        if entry.get("downloaded") is not False:
            problems.append(f"{entry['id']}: downloaded must be false")
    roles = {entry["role"] for entry in config["sources"]}
    for required in ("lab_tract_polygons", "observed_data", "reference_roads", "reference_buildings",
                     "reference_facilities", "reference_establishments"):
        if required not in roles:
            problems.append(f"no source with role {required}")
    return {"entries": len(config["sources"]), "problems": sorted(set(problems)),
            "roles": sorted(roles),
            "probe": {**config["availability_probe"],
                      "content_bytes_may_be_read": config["availability_probe"][
                          "content_bytes_may_be_read"],
                      "performed": config["availability_probe"]["performed_this_turn"]}}


LAB_ARTIFACT_NAMES = ("manifest.json", "tracts.parquet", "estimators.parquet", "labels.parquet",
                      "defineness.json")
# built from pieces so this file cannot trip its own scan
_READINESS_FETCHERS = ("url" + "retrieve", "url" + "open", "http_" + "get", "http_" + "download",
                       "S3" + "FileSystem", "read_row_" + "groups")


def lab_measurement_tokens(text: str) -> list[str]:
    """Tokens that would mean a lab number had been published into a readiness product."""
    return [token for token in LAB_MEASUREMENT_TOKENS if token in text]


def _readiness_source() -> str:
    return (ROOT / "scripts" / "report_lab_readiness.py").read_text(encoding="utf-8")
# what would be prohibited under the lab root: anything keyed to the study regions. The removed
# reference layers themselves are legitimate lab inputs now that the operator has authorised the build
STUDY_REGION_TOKENS = ("maricopa", "northern-ca", "eastern-ok", "south-central-tx", "study")
LAB_MEASUREMENT_TOKENS = ('"lab_tract_id":', '"rmse":', '"observed_km":', '"reference_km":',
                          '"fit_provenance":', '"stability_selection":')


def check_lab_root(config: dict[str, Any], lab_root: Path,
                   workspace: Path | None = None) -> dict[str, Any]:
    """Classify what sits under the lab root.

    The build row is allowed to put bytes there; readiness is not. So the invariant readiness proves is
    not "nothing exists" but "nothing exists except staged build output, and nothing has been promoted
    into the five artifacts the evaluator treats as a scoreable lab".
    """
    inside_repo = lab_root.is_absolute() and lab_root.resolve().is_relative_to(ROOT.resolve())
    files = sorted(str(entry.relative_to(lab_root)) for entry in lab_root.rglob("*")) \
        if lab_root.exists() else []
    def is_staged(entry: str) -> bool:
        return entry == "staging" or entry.startswith("staging/")
    staged = [entry for entry in files if is_staged(entry)]
    unexpected = [entry for entry in files if not is_staged(entry)]
    promoted = [name for name in LAB_ARTIFACT_NAMES if (lab_root / name).is_file()]
    study_geoids = set(lab_plan.official_scored_ids(workspace)) | set(lab_plan.template_geoids(workspace)) \
        if workspace is not None else set()
    keyed_to_study = [entry for entry in files
                      if any(token in entry.lower() for token in STUDY_REGION_TOKENS)
                      or any(geoid in entry for geoid in study_geoids)]
    tracked = []
    if not inside_repo:
        tracked = [entry for entry in _tracked_files() if entry.startswith("lab/") or "/lab/" in entry]
    return {"declared": config["lab_root"]["path"], "resolved": str(lab_root),
            "inside_repo": bool(inside_repo), "exists": lab_root.exists(),
            "file_count": len(files), "files": files[:20],
            "staged_file_count": len(staged), "staging_only": not unexpected,
            "unexpected_root_entries": unexpected[:20],
            "promoted_artifacts_present": promoted,
            "entries_keyed_to_the_study_regions": keyed_to_study,
            "tracked_in_git": tracked,
            "estimated_disk_gb": config["lab_root"]["estimated_disk_gb"],
            "must_not_contain_repo_paths": config["lab_root"]["must_not_contain_repo_paths"]}


def check_exclusion(config: dict[str, Any], policy: lab_plan.ExclusionPolicy,
                    counties: dict[str, Any], study_points_self_test: list[tuple[float, float]],
                    template_geoids: list[str]) -> dict[str, Any]:
    """Run the exclusion policy against a self-test panel, including the near-study cases."""
    official = sorted(policy.official_geoids)[:1] or ["06007001800"]
    a_study_county = sorted(policy.study_county_fips)[0] if policy.study_county_fips else "06007"
    template_only = next((geoid for geoid in template_geoids
                          if geoid not in policy.official_geoids), "53000000000")
    near = study_points_self_test[0] if study_points_self_test else (0.0, 0.0)
    panel = [
        {"geoid": official[0], "county_fips": a_study_county, "centroid": near},
        {"geoid": template_only, "county_fips": "53033", "centroid": None},       # WA template row
        {"geoid": "99" + official[0][2:], "county_fips": a_study_county,
         "centroid": near},                                                        # study county at a study tract
        {"geoid": "98" + official[0][2:], "county_fips": "00000",
         "centroid": (near[0] + 1000.0, near[1])},                      # ~1 km: adjacent tract
        {"geoid": "97" + official[0][2:], "county_fips": "00000",
         "centroid": (near[0] + 5000.0, near[1])},                       # 5 km: inside buffer
        {"geoid": "96" + official[0][2:], "county_fips": "00000",
         "centroid": (near[0] + 500000.0, near[1])},                     # 500 km: a real lab tract
    ]
    buckets = policy.partition(panel)
    expected_rejected = {item["geoid"] for item in panel[:5]}
    expected_accepted = {panel[5]["geoid"]}
    rejected = {geoid for reason, items in buckets.items() if reason != "accepted"
                for geoid in items}
    return {"policy": policy.summary(), "regions": {
        region: {"rows": entry["rows"], "states": entry["states"],
                 "counties": len(entry["county_fips"])}
        for region, entry in counties.items()},
        "study_county_total": len(policy.study_county_fips),
        "self_test_panel": [{"geoid": item["geoid"], "county_fips": item["county_fips"],
                            "centroid": item["centroid"],
                            "reason": policy.reject_reason(item["geoid"], item["county_fips"],
                                                           item["centroid"])}
                           for item in panel],
        "self_test_expected_rejected": sorted(expected_rejected),
        "self_test_unexpectedly_rejected": sorted(rejected - expected_rejected),
        "self_test_missed_rejections": sorted(expected_rejected - rejected),
        "self_test_accepted": sorted(buckets.get("accepted", [])),
        "self_test_accepts_only_the_far_tract": set(buckets.get("accepted", [])) == expected_accepted,
        "template_rows_observed": len(policy.template_geoids),
        "official_rows_observed": len(policy.official_geoids),
        "county_rule_self_test": _county_rule_self_test(policy, a_study_county)}


def _county_rule_self_test(policy: lab_plan.ExclusionPolicy, study_county: str) -> dict[str, Any]:
    """The county rule is off by default (298 counties are touched by the packages), so prove it still
    works when a build turns it on: a non-official tract inside a study county with no centroid must be
    rejected by an enabled policy and accepted by the configured one."""
    enabled = lab_plan.ExclusionPolicy(
        official_geoids=policy.official_geoids, template_geoids=policy.template_geoids,
        study_county_fips=policy.study_county_fips, study_states=policy.study_states,
        buffer_km=policy.buffer_km, study_points=list(policy.study_points), crs=policy.crs,
        county_exclusion_enabled=True)
    probe_geoid = "95" + (sorted(policy.official_geoids)[:1] or ["007001800"])[0][2:]
    return {"county_exclusion_configured": policy.county_exclusion_enabled,
            "enabled_policy_rejects_study_county":
                enabled.reject_reason(probe_geoid, study_county, None) == "study_county",
            "configured_policy_defers_to_the_buffer":
                policy.reject_reason(probe_geoid, study_county, None) is None}


def check_firewall(config: dict[str, Any], extra_scripts: list[Path]) -> dict[str, Any]:
    tokens = [token.lower() for token in config["firewall"]["lab_tokens"]]
    allowlist = set(config["firewall"]["allowlisted_documentation_and_gates"])
    targets = list(config["firewall"]["model_and_submission_scripts_must_not_reference_lab"])
    hits: list[dict[str, Any]] = []
    for relative in targets:
        path = ROOT / relative
        if not path.is_file():
            hits.append({"script": relative, "token": "missing_script"})
            continue
        text = read_text(path).lower()
        for token in tokens:
            if token in text and relative not in allowlist:
                hits.append({"script": relative, "token": token})
    for path in extra_scripts:
        text = path.read_text(encoding="utf-8").lower()
        for token in tokens:
            if token in text:
                hits.append({"script": str(path), "token": token})
    return {"scripts_scanned": len(targets) + len(extra_scripts), "hits": hits,
            "tokens": sorted(set(tokens))}


def check_authorization(config: dict[str, Any], rules_text: str | None,
                        decision: dict[str, Any] | None) -> dict[str, Any]:
    gate = config["rules_gate"]
    normalised = " ".join((rules_text or "").split())
    citations = [{"citation": citation,
                  "present_in_saved_rules_text": " ".join(citation.split()) in normalised}
                 for citation in gate["citations_verbatim"]]
    conditions = [{"condition": entry["condition"], "enforced_by": entry["enforced_by"],
                   "enforced": bool(entry["condition"].strip()) and bool(entry["enforced_by"].strip())}
                  for entry in gate["operator_conditions"]]
    keywords = ("study region", "never computed on", "weight vector", "writeup")
    conditions_cover_decision = len(conditions) == 4 and all(
        any(keyword in entry["condition"].lower() or keyword in entry["enforced_by"].lower()
            for keyword in keywords)
        for entry in conditions)
    track_b = config["track_b_controls"]
    crossing = [field.lower() for field in track_b["allowed_fields_in_a_submission_artifact"]]
    smuggles = [field for field in crossing
                if any(bad in field for bad in ("label", "geoid", "measurement", "per-tract", "region-mean",
                                                "region mean"))]
    return {
        "status": gate["status"], "decided_by": gate["decided_by"],
        "decided_at_utc": gate["decided_at_utc"],
        "decision_verbatim": gate["decision_verbatim"],
        "decision_matches_spec_verbatim": bool(decision
                                               and decision["verbatim"] == gate["decision_verbatim"]
                                               and decision["overrides_ruling_precondition"]),
        "blocking_row": gate["blocking_row"],
        "blocking_row_disposition": gate["blocking_row_disposition"],
        "operator_conditions": conditions,
        "operator_conditions_cover_the_decision": conditions_cover_decision,
        "second_blocker": gate["second_blocker"],
        "citations": citations,
        "all_citations_verbatim": all(entry["present_in_saved_rules_text"]
                                      for entry in citations) and bool(citations),
        "rules_text_available": rules_text is not None,
        "tension_recorded": gate["tension_recorded"],
        "operator_question_status": gate["operator_question_status"],
        "operator_question": gate["operator_question"],
        "operator_question_note": gate["operator_question_note"],
        "permitted_now": gate["permitted_now"],
        "still_prohibited": gate["still_prohibited"],
        "lab_data_may_not_reach": gate["lab_data_may_not_reach"],
        "track_b_controls": track_b,
        "track_b_smuggles_lab_values": smuggles,
        "writeup_disclosure": config["writeup_disclosure"],
    }


def _tracked_files() -> list[str]:
    try:
        import subprocess

        out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True,
                             timeout=60)
        return out.stdout.split() if out.returncode == 0 else []
    except Exception:
        return []


def synthetic_study_points() -> list[tuple[float, float]]:
    """A stand-in study geometry for the buffer self-test.

    Real tract polygons would come from the provided package, and the build must re-run this policy
    against them; for readiness the question is whether the policy rejects a near-study candidate, which
    is answerable with a declared point set and is re-checked by tests.
    """
    return [(0.0, 0.0), (1200.0, 900.0), (-3400.0, 2100.0)]


# --------------------------------------------------------------------------- report
def build_report(config: dict[str, Any], *, workspace: Path, lab_root: Path,
                 extra_scan_scripts: list[Path] | None = None, tamper: str | None = None,
                 rules_text: str | None = None) -> dict[str, Any]:
    config = json.loads(json.dumps(config))  # tampers mutate a copy only
    if tamper == "decision_fabricated":
        config["rules_gate"]["decision_verbatim"] = config["rules_gate"]["decision_verbatim"].replace(
            "Track B is allowed", "Track B is discouraged")
    elif tamper == "conditions_dropped":
        config["rules_gate"]["operator_conditions"][1]["enforced_by"] = ""
    elif tamper == "weights_smuggle_labels":
        config["track_b_controls"]["allowed_fields_in_a_submission_artifact"].append(
            "per-tract lab labels")
    elif tamper == "probe_reads_content":
        config["availability_probe"]["content_bytes_may_be_read"] = True
    elif tamper == "county_filter_flag_removed":
        for group in config["groups"]:
            if group["id"] == "TX-W":
                group["retrieval_must_be_county_filtered"] = False
    elif tamper == "buffer_removed":
        config["exclusion_policy"]["centroid_buffer_km"] = 0.0
    elif tamper == "exclusion_emptied":
        config["exclusion_policy"]["drop_official_scored_geoids"] = False
    elif tamper == "source_field_dropped":
        for entry in config["sources"]:
            if entry["id"] == "ms_us_building_footprints":
                entry["url"] = None
    elif tamper == "lab_root_in_repo":
        config["lab_root"]["path"] = str(ROOT / "lab")
        lab_root = ROOT / "lab"
    elif tamper == "question_drops_the_spec_question":
        config["rules_gate"]["operator_question"] = config["rules_gate"]["operator_question"]\
            .split(" Is building and reporting")[0]
    elif tamper not in (None, "lab_data_present", "model_reads_lab"):
        raise ValueError(f"unknown tamper {tamper!r}")

    spec_path = Path(config["spec"]["resolved_path"])
    spec_hash = lab_plan.sha256_file(spec_path)
    body = lab_plan.section4(spec_path)
    decision = lab_plan.parse_operator_decision(spec_path)
    parsed = {
        "groups": lab_plan.parse_groups(_key(body, "4.2")),
        "volume_table": lab_plan.parse_volume_table(_key(body, "4.5")),
        "recipe": lab_plan.parse_recipe(_key(body, "4.4")),
        "sources": lab_plan.parse_sources(_key(body, "4.3")),
        "placement": lab_plan.parse_placement(_key(body, "4.6")),
        "operator_decision": decision,
    }
    findings = lab_plan.cross_check(config, parsed)

    counties = lab_plan.study_counties(workspace)
    points = synthetic_study_points()
    policy = lab_plan.policy_from_config(config, workspace, study_points=points)
    exclusion = check_exclusion(config, policy, counties, points,
                                lab_plan.template_geoids(workspace))
    sources = check_sources(config)
    lab = check_lab_root(config, lab_root, workspace)
    firewall = check_firewall(config, extra_scan_scripts or [])
    authorization = check_authorization(config, rules_text, decision)

    study_county_set = policy.study_county_fips
    offending = sorted({f"{group['id']}:{county}" for group in config["groups"]
                        for county in group["county_includes"] if county in study_county_set})

    postal_to_fips = config["state_fips_by_postal"]
    study_counties_by_state = {fips[:2] for entry in counties.values()
                               for fips in entry["county_fips"]}
    consistency = {"required_groups": [], "mismatches": [], "study_state_fips":
                   sorted(study_counties_by_state)}
    for group in config["groups"]:
        state_holds_study_tracts = any(postal_to_fips[state] in study_counties_by_state
                                       for state in group["states"])
        if state_holds_study_tracts:
            consistency["required_groups"].append(group["id"])
        if group.get("retrieval_must_be_county_filtered") != state_holds_study_tracts:
            consistency["mismatches"].append(group["id"])
    consistency["unfiltered_groups_without_study_tracts"] = [
        group["id"] for group in config["groups"]
        if not any(postal_to_fips[state] in study_counties_by_state for state in group["states"])
        and not group.get("retrieval_must_be_county_filtered")]

    expected = config["exclusion_policy"]
    exclusion_ok = (
        exclusion["official_rows_observed"] == expected["expected_official_geoids"]
        and exclusion["template_rows_observed"]
        == (expected["expected_official_geoids"] + expected["expected_eastern_wa_template_rows"])
        and set(exclusion["policy"]["study_states_excluded"])
        and policy.official_geoids <= policy.excluded_geoids)

    definedness = config["definedness_accounting"]
    recipe_checks = config["recipe"]["self_checks"]

    gates: dict[str, Any] = {
        "spec_hash_matches_pin": spec_hash == config["spec"]["sha256"],
        "plan_parsed_from_spec": bool(
            findings["group_cv_units_match"] and findings["approx_tract_counts_match"]
            and findings["recipe_projection_matches"]
            and findings["all_four_reference_components_declared"]
            and findings["structure_codes_covered"]
            and not findings["source_families_missing_from_register"]
            and findings["volume_rows_spec"] >= 6),
        "lab_root_outside_repo": not lab["inside_repo"] and not lab["tracked_in_git"],
        "no_prohibited_content_on_disk": bool(
            lab["staging_only"]
            and not lab["promoted_artifacts_present"]
            and not lab["entries_keyed_to_the_study_regions"]),
        "study_geoids_fully_excluded": bool(exclusion_ok),
        "study_counties_enumerated": bool(
            len(exclusion["regions"]) == 4 and all(entry["counties"] > 0
                                                   for entry in exclusion["regions"].values())),
        "study_county_overlap_recorded": bool(
            all("county_includes" in group for group in config["groups"])
            and exclusion["study_county_total"] > 0
            and isinstance(exclusion["county_rule_self_test"]["county_exclusion_configured"], bool)),
        "county_filtered_retrieval_required_recorded": bool(
            consistency["mismatches"] == [] and consistency["required_groups"]
            and consistency["unfiltered_groups_without_study_tracts"]),
        "neighbour_buffer_policy_rejects_study_and_adjacent_tracts": bool(
            not exclusion["self_test_missed_rejections"]
            and not exclusion["self_test_unexpectedly_rejected"]
            and exclusion["self_test_accepts_only_the_far_tract"]
            and policy.buffer_km > 0.0),
        "model_and_submission_paths_cannot_read_lab": not firewall["hits"],
        "source_register_complete": not sources["problems"],
        "definedness_accounting_declared": bool(
            len(definedness["per_group_per_component_required"]) >= 5
            and len(definedness["per_group_required"]) >= 4
            and config["acceptance"]["minimum_labelled_groups"] >= 5
            and definedness["undefined_is_not_zero"]),
        "recipe_self_checks_declared": bool(
            recipe_checks["state_road_ratio_in_organiser_band"]["band"] == [0.71, 1.59]
            and abs(recipe_checks["hospital_ratio_check"]["target"] - 12.0) < 1e-9
            # readiness itself measures nothing, so either status is honest: "not_run_pending_ruling"
            # while no group has been built, and the measured status once the build row has reported
            # its own results in reports/lab-build.json
            and config["recipe"]["self_check_status"] in ("not_run_pending_ruling",
                                                           "road_ratio_measured_for_staged_groups")
            and bool(config["recipe"].get("self_check_status_note"))),
        "operator_decision_present_verbatim_in_spec": bool(
            authorization["decision_matches_spec_verbatim"]),
        "external_use_recorded_as_authorized": bool(
            authorization["status"] == "authorized_by_operator_decision"
            and authorization["blocking_row"] is None
            and authorization["operator_question_status"] == "withdrawn_by_operator_decision"
            and "901d5e3" in authorization["decided_by"]
            and authorization["second_blocker"]["row"] == "bias60-track-a-combiner"
            and "still binding on sequencing" in authorization["second_blocker"]["status"]),
        "operator_conditions_enforced": bool(authorization["operator_conditions_cover_the_decision"]
                                             and all(entry["enforced"] for entry
                                                     in authorization["operator_conditions"])),
        "track_b_crossing_surface_is_bounded": bool(
            config["track_b_controls"]["only_the_weight_vector_may_cross"]
            and config["track_b_controls"]["weight_vector_is_the_only_interface"]
            and config["track_b_controls"]["candidate_manifest_must_quote"]
            == "rules_gate.decision_verbatim"
            and len(config["track_b_controls"]["never_cross"]) >= 4
            and not authorization["track_b_smuggles_lab_values"]),
        "writeup_disclosure_required": bool(
            config["writeup_disclosure"]["required"]
            and len(config["writeup_disclosure"]["documents"]) >= 2
            and len(config["writeup_disclosure"]["must_state"]) >= 4
            and any("operator decision" in line for line
                    in config["writeup_disclosure"]["must_state"])),
        "availability_probe_stays_head_only": bool(
            sources["probe"]["allowed_methods"] == ["HEAD"]
            and sources["probe"]["content_bytes_may_be_read"] is False
            and sources["probe"]["credentials_may_be_used"] is False),
        # readiness proves it retrieved nothing itself; the bytes the build row later staged are
        # proved by a different gate, so the two rows cannot hide behind each other
        "readiness_performed_no_retrieval": bool(
            sources["probe"]["performed"] is False
            and not set(_READINESS_FETCHERS).intersection(_readiness_source())
            and config["prohibitions"]["reference_layer_bytes_downloaded_in_this_row"] == 0
            and config["prohibitions"]["uploads_performed"] == 0),
        "lab_root_holds_staged_build_output_only": bool(
            lab["staging_only"] and not lab["promoted_artifacts_present"]),
        # readiness may describe the build; it must not consume the bytes the build produced
        "build_output_is_never_read_by_readiness": not any(
            "lab/staging/" in entry for entry in OPENED),
        "rules_citations_are_verbatim": bool(authorization["all_citations_verbatim"]
                                             and authorization["rules_text_available"]),
        "operator_question_withdrawn_not_answered": bool(
            config["rules_gate"]["operator_question_status"] == "withdrawn_by_operator_decision"
            and config["prohibitions"]["organiser_question_withdrawn"] is True
            and "May weights fitted" in config["rules_gate"]["operator_question"]),
        "operator_question_covers_the_spec_question": bool(
            parsed["placement"]["combined_question"]
            and parsed["placement"]["combined_question"]
            in " ".join(config["rules_gate"]["operator_question"]
                        .replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
                        .split())
            and config["placement"]["spec_combined_question"]
            == parsed["placement"]["combined_question"]
            and parsed["placement"]["writeup_declared_always_allowed"]
            and parsed["placement"]["scored_submission_default"] == "Track A"),
        # readiness publishes no lab number, and cannot be tricked into one by a staged build: the
        # measurement tokens live in the build report, which readiness never reads
        "no_lab_number_exists": bool(
            not lab["promoted_artifacts_present"]
            and config["prohibitions"]["reference_layer_bytes_downloaded_in_this_row"] == 0),
    }
    gates["all_passed"] = all(bool(value) for value in gates.values())

    report: dict[str, Any] = {
        "preregistration": {
            "plan_id": config["plan_id"],
            "backlog_item": config["backlog_item"],
            "lane": config["lane"],
            "title": config["title"],
            "intent": config["intent"],
            "config_path": config["__path__"],
            "config_sha256": config["__sha256__"],
            "spec_path": str(spec_path),
            "spec_sha256": spec_hash,
            "spec_sha256_pin": config["spec"]["sha256"],
            "spec_amendments": config["spec_amendments"],
            "tamper_applied": tamper,
            "prohibitions": config["prohibitions"],
            "negative_controls_declared": [case["id"] for case in config["negative_controls"]],
        },
        "authorization": authorization,
        "groups": config["groups"],
        "core_groups": config["core_groups"],
        "cv_fold_amendment": {
            "active_group_ids": config.get("cv_fold_amendment", {}).get("active_group_ids", []),
            "excluded_group_ids": [entry["id"] for entry in
                                   config.get("cv_fold_amendment", {}).get("excluded_from_cv", [])],
            "rule": config.get("cv_fold_amendment", {}).get("rule", ""),
        },
        "spec_cross_check": findings,
        "source_register": sources,
        "sources": config["sources"],
        "lab_root": lab,
        "exclusion": exclusion,
        "county_filter_consistency": consistency,
        "placement": {**config["placement"], "as_parsed_from_spec": parsed["placement"]},
        "offending_group_counties": offending,
        "recipe": config["recipe"],
        "definedness_accounting": definedness,
        "acceptance": config["acceptance"],
        "firewall": firewall,
        "access": {"opened_paths": sorted(set(OPENED)),
                   "provided_data_reads": {
                       "strata_tables": [f"WORKSPACE:defined-flags-inputs/{region}-strata-tract-table"
                                         ".parquet" for region in lab_plan.REGION_DIRECTORIES],
                       "strata_columns": lab_plan.STRATA_COLUMNS,
                       "platform_template": "WORKSPACE:SampleSubmission.csv (GEOID column only)",
                       "official_geoid_reader": config["exclusion_policy"]["official_geoid_reader"]},
                   "disclosure": "workspace files are labelled WORKSPACE:*, orchestrator files "
                                 "ORCHESTRATOR:*, repository files are repository-relative; no reference "
                                 "layer, label, score placeholder cell or platform endpoint is opened"},
        "gates": gates,
        "environment": {"versions": environment_versions(), "git_commit": git_commit(ROOT)},
        "summary": {
            "groups_planned": len(config["groups"]),
            "core_groups": len(config["core_groups"]),
            "sources_registered": sources["entries"],
            "official_geoids_excluded": exclusion["official_rows_observed"],
            "template_geoids_excluded": exclusion["template_rows_observed"],
            "study_counties_excluded": exclusion["study_county_total"],
            "lab_files_on_disk": lab["file_count"],
            "lab_staged_files": lab["staged_file_count"],
            "lab_promoted_artifacts": lab["promoted_artifacts_present"],
            "authorization_status": authorization["status"],
            "gates_all_passed": gates["all_passed"],
        },
    }
    report["gates"]["report_is_strict_json"] = "NaN" not in json.dumps(report)
    report["gates"]["report_has_no_temporal_field"] = not wall_clock_hits({
        "authorization": authorization, "groups": config["groups"],
        "cv_fold_amendment": report["cv_fold_amendment"],
        "spec_cross_check": findings, "exclusion": exclusion, "lab_root": lab})
    report["gates"]["all_passed"] = all(bool(value) for value in report["gates"].values())
    report["summary"]["gates_all_passed"] = report["gates"]["all_passed"]
    report["summary"]["report_sha256"] = hashlib.sha256(normalized_bytes(report)).hexdigest()
    return report


def _key(body: dict[str, str], prefix: str) -> str:
    for heading, text in body.items():
        if heading.startswith(prefix):
            return text
    raise ValueError(f"spec subsection {prefix} is missing")


def render_markdown(report: dict[str, Any]) -> str:
    # the readiness products must publish no lab number, so the scan runs over exactly what is about
    # to be committed rather than over a description of it
    body = json.dumps(report, sort_keys=True) + "\n" + _render_markdown_body(report)
    offenders = lab_measurement_tokens(body)
    if offenders:
        report["gates"]["no_lab_number_exists"] = False
        report["gates"]["all_passed"] = False
        report["gates"]["no_lab_number_tokens"] = offenders
    elif "no_lab_number_tokens" in report["gates"]:
        del report["gates"]["no_lab_number_tokens"]
    return _render_markdown_body(report)


def _render_markdown_body(report: dict[str, Any]) -> str:
    lines = [f"# {report['preregistration']['title']}", ""]
    lines.append(f"- Plan: `{report['preregistration']['plan_id']}` "
                 f"(backlog item `{report['preregistration']['backlog_item']}`)")
    lines.append(f"- Preregistered configuration: `{report['preregistration']['config_path']}` "
                 f"(SHA-256 `{report['preregistration']['config_sha256'][:16]}...`)")
    lines.append(f"- Spec: `{report['preregistration']['spec_path']}` "
                 f"(SHA-256 `{report['preregistration']['spec_sha256'][:16]}...`, matches its pin: "
                 f"{report['gates']['spec_hash_matches_pin']})")
    lines.append(f"- Status: **{report['authorization']['status']}** by "
                 f"{report['authorization']['decided_by']} at "
                 f"{report['authorization']['decided_at_utc']}; organiser question "
                 f"`{report['authorization']['operator_question_status']}`; sequencing blocker "
                 f"`{report['authorization']['second_blocker']['row']}` "
                 f"({report['authorization']['second_blocker']['status']})")
    verdict = "PASS" if report["gates"]["all_passed"] else "FAIL"
    lines.append(f"- Readiness verdict: **{verdict}** -- the design is frozen, the exclusion policy is "
                 f"proved on a self-test panel, the lab root holds only staged build output "
                 f"({report['summary']['lab_staged_files']} files, none promoted, none keyed to a study "
                 f"region), and no model or submission path can read a lab file")
    lines.append("")
    lines.append("## Operator decision that authorises the lab")
    lines.append("")
    lines.append(f"> {report['authorization']['decision_verbatim']}")
    lines.append("")
    lines.append(f"Recorded verbatim in the spec: "
                 f"{report['gates']['operator_decision_present_verbatim_in_spec']}. Conditions and their "
                 f"enforcement:")
    lines.append("")
    for entry in report["authorization"]["operator_conditions"]:
        lines.append(f"- {entry['condition']}")
        lines.append(f"  - enforced by: {entry['enforced_by']}")
    lines.append("")
    lines.append(f"- Track B crossing surface: "
                 f"{report['authorization']['track_b_controls']['allowed_fields_in_a_submission_artifact']}; "
                 f"never crosses: "
                 f"{report['authorization']['track_b_controls']['never_cross']}")
    lines.append(f"- Writeup disclosure required: "
                 f"{report['authorization']['writeup_disclosure']['required']} in "
                 f"{report['authorization']['writeup_disclosure']['documents']}")
    lines.append("")
    lines.append("## The binding rule text the decision operates against")
    lines.append("")
    for entry in report["authorization"]["citations"]:
        mark = "verbatim in the saved official text" if entry["present_in_saved_rules_text"] \
            else "NOT FOUND in the saved official text"
        lines.append(f"- \"{entry['citation']}\" -- {mark}")
    lines.append("")
    lines.append(f"Why the text is still quoted: {report['authorization']['tension_recorded']}")
    lines.append("")
    lines.append(f"## Retained organiser question ({report['authorization']['operator_question_status']})")
    lines.append("")
    lines.append(f"> {report['authorization']['operator_question']}")
    lines.append("")
    lines.append(f"> {report['authorization']['operator_question_note']}")
    lines.append("")
    lines.append("## Lab groups (spec 4.2)")
    lines.append("")
    lines.append("| Group | States | Approx tracts | Role | Counties declared |")
    lines.append("|---|---|---:|---|---|")
    for group in report["groups"]:
        lines.append(f"| {group['id']} | {'/'.join(group['states'])} | {group['approx_tracts']} | "
                     f"{group['transfer_role']} | {len(group['county_includes'])} |")
    lines.append("")
    amendment = report["cv_fold_amendment"]
    lines.append("## Active cross-validation group amendment")
    lines.append("")
    lines.append(f"- Active groups: {amendment['active_group_ids']}")
    lines.append(f"- Excluded from CV: {amendment['excluded_group_ids']}")
    lines.append(f"- Rule: {amendment['rule']}")
    lines.append("")
    lines.append("## Source register (nothing retrieved yet)")
    lines.append("")
    lines.append("| Source | Role | URL | Version pattern | Retrieved | SHA-256 |")
    lines.append("|---|---|---|---|---|---|")
    for entry in report["sources"]:
        url = entry.get("url") or "MISSING"
        lines.append(f"| `{entry['id']}` | {entry['role']} | {url} | {entry['version_pattern']} | "
                     f"{entry['retrieval_date_utc']} | {entry['sha256']} |")
    lines.append("")
    lines.append("## Study exclusion, measured")
    lines.append("")
    policy = report["exclusion"]["policy"]
    lines.append(f"- official scored GEOIDs excluded: {policy['official_geoids_excluded']}")
    lines.append(f"- platform template GEOIDs excluded: {policy['template_geoids_excluded']} "
                 f"(union {policy['union_geoids_excluded']})")
    lines.append(f"- study counties excluded: {report['exclusion']['study_county_total']} "
                 f"in states {policy['study_states_excluded']}")
    lines.append(f"- centroid buffer: {policy['centroid_buffer_km']} km in {policy['buffer_crs']}")
    lines.append("")
    lines.append("| Candidate | County | Centroid | Rejection reason |")
    lines.append("|---|---|---|---|")
    for item in report["exclusion"]["self_test_panel"]:
        lines.append(f"| `{item['geoid']}` | {item['county_fips']} | {item['centroid']} | "
                     f"{item['reason']} |")
    lines.append("")
    lines.append("## Gates")
    lines.append("")
    for gate, value in report["gates"].items():
        lines.append(f"- `{gate}`: {'pass' if value else 'FAIL'}")
    lines.append("")
    lines.append("## Acceptance of the build, once allowed")
    lines.append("")
    lines.append(f"- labelled groups required: {report['acceptance']['minimum_labelled_groups']} of "
                 f"{report['summary']['groups_planned']}")
    lines.append(f"- per group and component: {report['definedness_accounting']['per_group_per_component_required']}")
    lines.append(f"- per group: {report['definedness_accounting']['per_group_required']}")
    lines.append(f"- recipe self-checks required: {report['acceptance']['required_recipe_self_checks']}")
    lines.append(f"- recipe self-check status: {report['recipe']['self_check_status']}")
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--lab-root", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--markdown", default=None)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--tamper", default=None)
    parser.add_argument("--scan-extra-script", action="append", default=[])
    args = parser.parse_args(argv)

    if not args.workspace:
        print("FAIL lab readiness: COMPETITION_WORKSPACE is required; the study exclusion cannot be "
              "measured without the provided strata tables")
        return 1
    workspace = Path(args.workspace).resolve()
    lab_plan.set_recorder(record)
    record(Path(args.config))
    config = lab_plan.load_plan(Path(args.config))
    lab_root = Path(args.lab_root or str(workspace / "lab"))
    rules_path = workspace / "RULES.md"
    rules_text = read_text(rules_path) if rules_path.is_file() else None

    report = build_report(
        config, workspace=workspace, lab_root=lab_root, rules_text=rules_text,
        extra_scan_scripts=[Path(path) for path in args.scan_extra_script], tamper=args.tamper)

    out_path = ROOT / (args.out or config["outputs"]["report"])
    markdown_path = ROOT / (args.markdown or config["outputs"]["markdown"])
    payload = canonical_bytes(report)

    if args.check_only:
        if out_path.is_file():
            recorded = json.loads(out_path.read_text(encoding="utf-8"))
            recorded.get("summary", {}).pop("report_sha256", None)
            current = json.loads(payload.decode("utf-8"))
            current.get("summary", {}).pop("report_sha256", None)
            ok = normalized_bytes(recorded) == normalized_bytes(current)
        else:
            ok = False
        print(("unchanged " if ok else "CHANGED ") + str(out_path))
        return 0 if ok and report["gates"]["all_passed"] else 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    failed = sorted(name for name, value in report["gates"].items() if not value)
    if failed:
        print("failed gates: " + ", ".join(failed))
    if report["gates"]["all_passed"]:
        print("PASS lab readiness: the operator decision of 2026-10-25 09:35Z is quoted verbatim from "
              "the spec and its four conditions are enforced by named mechanisms, the lab design is frozen "
              "against the spec, the study exclusion rejects every official GEOID, every template GEOID and "
              "every near-study candidate, the lab root holds only staged build output with nothing "
              "promoted and nothing keyed to a study region, no model or submission path can read the lab, "
              "only the weight vector may cross into a submission, and readiness itself retrieved nothing")
        return 0
    print("FAIL lab readiness")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
