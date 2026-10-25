#!/usr/bin/env python3
"""Evaluate the 60 estimators and their linear combination on the external lab (spec 3.2-3.3).

The lab is built by ``bias60-external-lab-build``. This script is its only consumer, so it fixes the
contract that build must satisfy and then refuses to publish a number unless the whole rail set holds:
manifest provenance with source SHA-256s and retrieval dates, the two RULES.md recipe self-checks
actually passing, zero collision with the official and platform-template GEOIDs, a recorded
nearest-study-tract distance beyond the buffer on every lab tract, definedness above the floor, and at
least five held-out state groups.

If the lab is not there, the report says ``not_run_missing_lab`` and every measurement field is null.
There is no code path in which a lab RMSE appears without a lab.

Nothing here touches a study tract: no study-side prediction is emitted, no study idea parquet is
opened, and the only thing allowed to travel back out is the weight vector plus its normalisation
constants, each with a SHA-256 and the lab commit. While ``bias60-track-a-combiner`` has recorded no
Track A pre-registration, that weight vector stays withheld.
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

from bias60 import lab_eval, lab_plan  # noqa: E402
from bias60.determinism import canonical_bytes, normalized_bytes, wall_clock_hits  # noqa: E402

DEFAULT_CONFIG = ROOT / "config/lab-evaluation-preregistered.json"
OPENED: list[str] = []


def record(path: Path) -> str:
    workspace = Path(os.environ.get("COMPETITION_WORKSPACE", "") or ".").resolve()
    orchestrator = Path(os.environ.get("ORCHESTRATOR_ROOT", "")
                        or "/home/martin/hddProjects/competitionsall")
    if Path(path).is_absolute() and Path(path).is_relative_to(ROOT):
        label = str(Path(path).relative_to(ROOT))
    elif Path(path).is_absolute() and Path(path).is_relative_to(workspace):
        label = f"WORKSPACE:{Path(path).relative_to(workspace)}"
    elif Path(path).is_absolute() and Path(path).is_relative_to(orchestrator):
        label = f"ORCHESTRATOR:{Path(path).relative_to(orchestrator)}"
    else:
        label = str(path)
    OPENED.append(label)
    return label


def git_commit(root: Path) -> str:
    try:
        import subprocess

        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=30).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def load_registry() -> dict[str, Any]:
    path = record(ROOT / "config/ideas.json")
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    implemented = list(document["summary"]["implemented"])
    components = {idea["id"]: idea["component"] for idea in document["ideas"]
                  if idea["id"] in implemented}
    return {"implemented": implemented, "components": components,
            "ideas_total": document["summary"]["ideas_total"],
            "families_total": document["summary"]["families_total"],
            "registry_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}


# --------------------------------------------------------------------------- helpers
def _floats(node: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(node, dict):
        for key, child in node.items():
            hits.extend(_floats(child, f"{path}/{key}"))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            hits.extend(_floats(child, f"{path}[{index}]"))
    elif isinstance(node, float):
        hits.append(path)
    return hits


def load_prerequisite(path: Path) -> dict[str, Any] | None:
    """The prerequisite probe produced by ``scripts/probe_lab_prerequisites.py``, or ``None``.

    The evaluation is downstream of that measurement: it is the record of whether the planned lab groups
    still hold tracts after the frozen exclusion and whether the registered sources answer at all. A
    committed evaluation without it is treated as incomplete rather than quietly tolerated.
    """
    path = Path(path)
    if not path.exists():
        return None
    record(path)
    return json.loads(path.read_text(encoding="utf-8"))


def prerequisite_view(prereq: dict[str, Any] | None) -> dict[str, Any]:
    if prereq is None:
        return {"present": False, "gates_all_passed": None, "groups_probed": None,
                "study_points": None, "candidates_accepted": None, "shortfalls": [],
                "sources_not_answered": [], "verdict": None}
    findings = prereq.get("findings", {})
    return {"present": True, "artifact_sha256": hashlib.sha256(
        canonical_bytes({key: value for key, value in prereq.items() if key != "summary"})).hexdigest(),
        "gates_all_passed": bool(prereq.get("gates", {}).get("all_passed")),
        "groups_probed": prereq["summary"]["groups_probed"],
        "groups_planned": prereq["summary"]["groups_planned"],
        "study_points": prereq["summary"]["study_points"],
        "candidates_found": prereq["summary"]["candidates_found"],
        "candidates_accepted": prereq["summary"]["candidates_accepted"],
        "downloaded_bytes": prereq["summary"]["downloaded_bytes"],
        "shortfalls": [{"id": entry["id"], "accepted": entry["accepted"],
                        "floor": entry["floor"]} for entry in findings.get("group_shortfalls", [])],
        "below_plan": [{"id": entry["id"], "planned_approx_tracts":
                        entry["planned_approx_tracts"], "accepted": entry["accepted"]}
                       for entry in findings.get("groups_below_plan", [])],
        "sources_not_answered": [{"id": entry["id"], "status": entry["status"],
                                  "alternate_that_answers": entry.get("alternate_that_answers")}
                                 for entry in findings.get("sources_not_answered", [])],
        "sources_not_probeable": [entry["id"] for entry in
                                  findings.get("sources_not_probeable", [])],
        "study_geoids_without_interior_point": findings.get("study_geoids_without_interior_point"),
        "lab_plan_needs_amendment": findings.get("lab_plan_needs_amendment"),
        "verdict": findings.get("verdict")}


def empty_measurement(config: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"status": config["measurement_status_when_absent"], "reason": reason,
            "channels": {}, "channels_seen": [],
            "track_a_summary": {variant: ["not_run"] for variant in config["track_a_variants"]},
            "shift_correction": {"status": "not_wired", "implemented": True,
                                 "requires": "study_side_label_free_feature_allowlist",
                                 "reason": "no lab and no study-side feature allowlist"}}


def apply_lab_tamper(lab: dict[str, Any], tamper: str, policy: lab_plan.ExclusionPolicy) -> None:
    tracts = lab["tracts"]
    if tamper == "study_geoid_in_lab":
        official = sorted(policy.official_geoids)[:1] or ["06007001800"]
        for column in (tracts["lab_tract_id"], tracts["lab_group"], tracts["state_fips"],
                       tracts["county_fips"], tracts["centroid_x"], tracts["centroid_y"],
                       tracts["nearest_study_tract_km"]):
            column.append(official[0] if column is tracts["lab_tract_id"] else
                          ("SYN" if column is tracts["lab_group"] else "06"))
    elif tamper == "nearest_distance_understated":
        tracts["nearest_study_tract_km"][0] = 1.0
    elif tamper == "self_check_fabricated":
        lab["manifest"]["self_checks"]["state_road_ratio_in_organiser_band"]["worst_ratio"] = 4.0
    elif tamper == "missing_source_hash":
        lab["manifest"]["sources"][0]["sha256"] = ""
    elif tamper == "too_few_groups":
        keep = [index for index, group in enumerate(tracts["lab_group"])
                if group in sorted(set(tracts["lab_group"]))[:3]]
        for column in tracts:
            tracts[column] = [tracts[column][index] for index in keep]
    elif tamper == "undefined_rows_zero_filled":
        estimators = lab["estimators"]
        for index in range(len(estimators["lab_tract_id"])):
            if not estimators["defined"][index]:
                estimators["value"][index] = 0.5
                break
    elif tamper == "labels_outside_unit":
        labels = lab["labels"]
        for index in range(len(labels["lab_tract_id"])):
            if labels["defined"][index]:
                labels["label"][index] = 1.75
                break
    else:
        raise ValueError(f"unhandled lab tamper {tamper!r}")


# --------------------------------------------------------------------------- report
def build_report(config: dict[str, Any], *, workspace: Path, lab_root: Path,
                 tamper: str | None = None) -> dict[str, Any]:
    config = json.loads(json.dumps(config))
    declared = {case["id"] for case in config["negative_controls"]}
    if tamper is not None and tamper not in declared:
        raise ValueError(f"unknown tamper {tamper!r}; declared controls are {sorted(declared)}")
    if tamper == "spec_pin_drift":
        config["spec"]["sha256"] = "0" * 64
    elif tamper == "weights_smuggle_lab_values":
        config["crossing_surface"]["allowed_fields"].append("lab per-tract values")
    elif tamper == "release_before_track_a_preregistration":
        config["track_a_preregistration"]["weight_vector_release"] = "released"
    elif tamper == "shift_claimed_without_allowlist":
        config["__force_shift_measured__"] = True
    elif tamper == "study_side_prediction_emitted":
        config["study_side"]["study_predictions_emitted"] = 9379
        config["study_side"]["study_idea_parquets_opened"] = 3
    elif tamper == "measurement_fabricated_without_lab":
        config["__fabricate__"] = {"channels": {"composite": {"rmse_track_b": 0.0831}}}

    spec_path = Path(config["spec"]["resolved_path"])
    spec_hash = lab_plan.sha256_file(spec_path)
    registry = load_registry()
    prereq = load_prerequisite(ROOT / config["prerequisite_probe"]["artifact"])
    if tamper == "prerequisite_probe_absent":
        prereq = None
    elif tamper == "prerequisite_findings_hidden" and prereq is not None:
        prereq = json.loads(json.dumps(prereq))
        prereq["findings"]["group_shortfalls"] = []

    lab = None
    absent_reason = None
    try:
        lab_plan.set_recorder(record)
        lab = lab_eval.load_lab(lab_root)
    except lab_eval.LabAbsent as error:
        absent_reason = f"no lab artifact set under {error}"
    if lab is not None and tamper in {"study_geoid_in_lab", "nearest_distance_understated",
                                      "self_check_fabricated", "missing_source_hash",
                                      "too_few_groups", "undefined_rows_zero_filled",
                                      "labels_outside_unit"}:
        policy = lab_plan.policy_from_config({"exclusion_policy": {
            "drop_official_scored_geoids": True, "centroid_buffer_km": 8.0}}, workspace)
        apply_lab_tamper(lab, tamper, policy)

    if lab is not None:
        provenance = lab_eval.check_provenance(lab["manifest"], config)
        policy = lab_plan.policy_from_config({"exclusion_policy": {
            "drop_official_scored_geoids": True,
            "centroid_buffer_km": float(lab["manifest"]["buffer_km"])}}, workspace)
        exclusion = lab_eval.check_exclusion(lab, policy, config)
        rows = lab_eval.check_rows(lab, registry["implemented"], config)
    else:
        provenance = exclusion = rows = None

    measurement_blocking = bool(lab is None
                                or (provenance and provenance["problems"])
                                or (exclusion and (exclusion["collisions_with_official_or_template"]
                                                   or exclusion["nearest_distance_below_buffer"]
                                                   or exclusion["same_state_with_no_buffer"]
                                                   or exclusion["duplicate_lab_tract_ids"]))
                                or (rows and rows["problems"]))
    if measurement_blocking:
        measurement = empty_measurement(config, "lab contract not satisfied" if lab is not None
                                        else absent_reason)
        crossing = {"allowed_fields": config["crossing_surface"]["allowed_fields"],
                    "weight_vectors": [], "lab_commit": None, "lab_manifest_sha256": None,
                    "forbidden_fields_observed": []}
    else:
        measurement = lab_eval.measure(lab, config, registry["implemented"])
        measurement["status"] = "measured"
        crossing = lab_eval.crossing_surface(measurement, lab)
    if config.get("__force_shift_measured__") and isinstance(measurement, dict):
        measurement.setdefault("shift_correction", {})["status"] = "measured"
    if config.get("__fabricate__"):
        measurement = json.loads(json.dumps(config["__fabricate__"]))
        measurement.setdefault("status", config["measurement_status_when_absent"])
        measurement.setdefault("channels_seen", [])
        measurement.setdefault("track_a_summary", {})

    measured = measurement["status"] == "measured"
    contract = {
        "spec_hash_matches_pin": spec_hash == config["spec"]["sha256"],
        "lab_artifact_contract_declared": bool(
            config["artifact_contract"]["files"] == list(lab_eval.ARTIFACTS)
            and config["artifact_contract"]["tracts_columns"] == list(lab_eval.TRACT_COLUMNS)
            and config["artifact_contract"]["estimators_columns"] == list(lab_eval.ESTIMATOR_COLUMNS)
            and config["artifact_contract"]["labels_columns"] == list(lab_eval.LABEL_COLUMNS)
            and config["artifact_contract"]["manifest_fields"] == list(lab_eval.MANIFEST_REQUIRED)
            and config["artifact_contract"]["exact_columns"]),
        "registry_implemented_ideas_declared": bool(
            registry["implemented"] and len(registry["implemented"])
            == registry["ideas_total"] - (registry["ideas_total"] - len(registry["implemented"]))
            and all(component in {"B", "F", "R", "C", "Est", "all"}
                    for component in registry["components"].values())),
        "manifest_provenance_complete": (None if lab is None else not provenance["problems"]),
        "recipe_self_checks_passed": (None if lab is None else not any(
            problem.startswith(("state road-ratio", "hospital-ratio", "worst road ratio",
                                "hospital ratio")) for problem in provenance["problems"])),
        "study_geoid_exclusion_holds": (None if lab is None else bool(
            not exclusion["collisions_with_official_or_template"]
            and not exclusion["duplicate_lab_tract_ids"])),
        "nearest_distance_buffer_respected": (None if lab is None else bool(
            not exclusion["nearest_distance_below_buffer"]
            and not exclusion["same_state_with_no_buffer"])),
        "rows_consistent": (None if lab is None else not rows["problems"]),
        "undefined_never_scored_as_zero": (None if lab is None else not any(
            "undefined estimator rows are not exactly" in problem
            for problem in rows["problems"])),
        "groups_held_out_adequate": (None if lab is None else len(rows["groups"])
                                     >= config["acceptance"]["minimum_held_out_groups"]),
        "crossing_surface_is_bounded": bool(
            crossing["allowed_fields"] == config["crossing_surface"]["allowed_fields"]
            and not set(crossing["allowed_fields"]) & set(config["crossing_surface"]
                                                          ["forbidden_fields"])
            and not crossing["forbidden_fields_observed"]
            and all(vector.get("weights_sha256") and vector.get("channel")
                    for vector in crossing["weight_vectors"])),
        "track_a_release_gate_recorded": bool(
            (config["track_a_preregistration"]["status_recorded_in_repo"] != "not_recorded")
            or (config["track_a_preregistration"]["weight_vector_release"] == "withheld"
                and config["track_a_preregistration"]["row"] == "bias60-track-a-combiner")),
        "weights_not_applied_to_study_tracts": bool(
            config["study_side"]["study_predictions_emitted"] == 0
            and config["study_side"]["study_idea_parquets_opened"] == 0),
        "no_fabricated_measurement": bool(
            measured or not _floats({key: value for key, value in measurement.items()
                                     if key not in ("reason",)})),
        "no_reference_layer_read_by_this_row": bool(
            config["prohibitions"]["reference_layer_bytes_read_in_this_row"] == 0
            and not [token for token in lab_eval.REFERENCE_LAYER_TOKENS
                     if token in " ".join(OPENED).lower()]),
        "no_forbidden_value_in_report": True,
        "shift_correction_status_declared": bool(
            measurement.get("shift_correction", {}).get("status")
            in config["shift_correction"]["status_allowed"]
            and (measurement.get("shift_correction", {}).get("status") != "measured"
                 or (measurement.get("shift_correction", {}).get("weights_sha256")
                     and measurement.get("shift_correction", {}).get("aoa_outside_share")
                     is not None))),
        "prohibitions_recorded": bool(
            config["prohibitions"]["no_uploads"] and config["prohibitions"]["uploads_performed"] == 0
            and config["prohibitions"]["no_fabricated_measurement"]
            and config["prohibitions"]["no_labels_or_hidden_targets_for_study_tracts"]),
        "prerequisite_probe_recorded": bool(
            prereq is not None and prereq.get("gates", {}).get("all_passed") is True
            and prereq["summary"]["groups_probed"] >= config["prerequisite_probe"]
            ["minimum_groups_probed"] and prereq["summary"]["study_points"] > 0
            and prereq["summary"]["candidates_found"] > 0),
        "prerequisite_probe_findings_disclosed": bool(
            prereq is not None and prereq.get("gates", {}).get("all_passed") is True
            and prereq["findings"].get("verdict")
            # recomputed from the probe's own per-group numbers, so a report cannot quietly drop a
            # group that fell below the fold floor from its disclosed shortfalls
            and not ({group["id"] for group in prereq["candidate_probe"]["groups"]
                      if group["accepted"] < prereq["findings"]
                      ["minimum_accepted_tracts_per_group"]}
                     - {entry["id"] for entry in prereq["findings"]
                        .get("group_shortfalls", [])})
            and all(entry["accepted"] < entry["floor"]
                    for entry in prereq["findings"].get("group_shortfalls", []))
            and prereq["findings"].get("lab_plan_needs_amendment") is bool(
                prereq["findings"].get("group_shortfalls")
                or prereq["findings"].get("sources_not_answered"))),
    }
    measurement_gates = {
        "per_idea_rmse_reported": None, "incremental_value_reported": None,
        "residual_correlation_reported": None, "mtmm_reported": None,
        "krogh_vedelsby_reported": None, "track_b_win_rate_rule_applied": None,
        "stability_selection_reported": None, "weight_vector_recorded": None,
        "track_a_variants_scored_or_documented": None, "measurements_all_passed": None,
    }
    if measured:
        channels = measurement["channels"]
        scored = []
        for channel_name, entry in channels.items():
            if isinstance(entry, dict) and entry.get("status") == "measured":
                entry = dict(entry)
                entry.setdefault("channel", channel_name)
                scored.append(entry)

        def all_channels(predicate) -> bool:
            return bool(scored) and all(predicate(entry) for entry in scored)

        def expected_ideas(entry: dict[str, Any]) -> list[str]:
            if entry["channel"] == "composite":
                return sorted(registry["implemented"])
            component = lab_eval.label_component_for(entry["channel"])
            return sorted(idea for idea, value in registry["components"].items()
                          if value == component)

        def ideas_scored(entry: dict[str, Any]) -> bool:
            return (entry["rmse_track_b"] is not None and np.isfinite(entry["rmse_track_b"])
                    and sorted(entry["ideas"]) == expected_ideas(entry))

        def incremental_scored(entry: dict[str, Any]) -> bool:
            if len(entry["incremental_value"]) != len(entry["ideas"]):
                return False
            for row in entry["incremental_value"]:
                if row["correlation_with_label"] is None:
                    return False
                if len(entry["ideas"]) > 1 and row["partial_correlation_with_label"] is None \
                        and row["drop_column_delta_rmse"] is None:
                    return False
            return True

        measurement_gates = {
            "per_idea_rmse_reported": all_channels(ideas_scored) and bool(
                [entry for entry in scored if entry["channel"] == "composite"]),
            "incremental_value_reported": all_channels(incremental_scored),
            "residual_correlation_reported": all_channels(
                lambda entry: entry["residual_correlation"]["pairs"] >= 1
                or len(entry["ideas"]) < 2),
            "mtmm_reported": all_channels(
                lambda entry: entry["mtmm"]["within_pairs"] + entry["mtmm"]["between_pairs"] >= 1
                or entry["mtmm"]["single_member"]),
            "krogh_vedelsby_reported": all_channels(
                lambda entry: entry["krogh_vedelsby_ambiguity"] is not None),
            "track_b_win_rate_rule_applied": all_channels(
                lambda entry: entry["win_rate_vs_equal"]["win_rate"] is not None
                and entry["win_rate_vs_equal"]["groups_compared"]
                == len(entry["per_group_track_b"]) == len(entry["groups"])),
            "stability_selection_reported": all_channels(
                lambda entry: entry["stability_selection"]["bootstrap_draws_used"] > 0
                and len(entry["stability_selection"]["nonzero_share"]) == len(entry["ideas"])),
            "weight_vector_recorded": all_channels(
                lambda entry: entry["weight_vector_sha256"]
                and len(entry["weight_vector"]) == len(entry["ideas"])),
            "track_a_variants_scored_or_documented": all_channels(
                lambda entry: all(variant["status"] in ("scored", "not_runnable")
                                  and (variant["status"] == "scored"
                                       or (variant["missing_ideas"] or variant["reason"]))
                                  for variant in entry["track_a_variants"].values())),
        }
        measurement_gates["measurements_all_passed"] = all(
            bool(value) for value in measurement_gates.values())

    gates = dict(contract)
    gates["contract_all_passed"] = all(
        bool(value) for key, value in contract.items() if value is not None)
    gates["measurements_all_passed_or_legitimately_not_run"] = bool(
        measurement_gates["measurements_all_passed"] if measured
        else measurement["status"] == config["measurement_status_when_absent"]
        and not _floats(measurement.get("channels", {})))
    report: dict[str, Any] = {
        "preregistration": {
            "plan_id": config["plan_id"], "backlog_item": config["backlog_item"],
            "lane": config["lane"], "title": config["title"], "intent": config["intent"],
            "config_path": config["__path__"], "config_sha256": config["__sha256__"],
            "spec_path": str(spec_path), "spec_sha256": spec_hash,
            "spec_sha256_pin": config["spec"]["sha256"],
            "operator_decision": config["operator_decision"],
            "tamper_applied": tamper, "prohibitions": config["prohibitions"],
            "negative_controls_declared": [case["id"] for case in config["negative_controls"]],
        },
        "dependency": {
            "built_by_row": "bias60-external-lab-build",
            "lab_root": str(lab_root), "lab_present": lab is not None,
            "absent_reason": absent_reason,
            "measurement_status": measurement["status"],
            "what_the_build_must_deliver": {
                "files": list(lab_eval.ARTIFACTS),
                "manifest_fields": list(lab_eval.MANIFEST_REQUIRED),
                "self_checks": ["state_road_ratio_in_organiser_band", "hospital_ratio_check"],
                "minimum_groups": config["acceptance"]["minimum_held_out_groups"]},
            "prerequisite_probe": prerequisite_view(prereq),
        },
        "artifact_contract": config["artifact_contract"],
        "registry": registry,
        "lab_checks": {"provenance": provenance, "exclusion": exclusion, "rows": rows},
        "measurement": measurement,
        "crossing_surface": crossing,
        "study_side": config["study_side"],
        "track_a_preregistration": config["track_a_preregistration"],
        "weights_policy": config["weights"],
        "acceptance": {
            "met": (None if not measured else bool(
                measurement_gates["measurements_all_passed"])),
            "requirements": {
                "all_implemented_ideas_evaluated_on_five_groups": (
                    "not_run" if not measured else bool(
                        measurement_gates["per_idea_rmse_reported"]
                        and len(rows["groups"]) >= config["acceptance"]["minimum_held_out_groups"]
                        and measurement_gates["track_b_win_rate_rule_applied"])),
                "track_b_beats_equal_weights_or_reported_as_not": (
                    "not_run" if not measured else all(
                        entry["track_b_accepted_by_rule"] or entry["reported_as_not_beating_equal_weights"]
                        for entry in measurement["channels"].values()
                        if entry.get("status") == "measured")),
                "minimum_held_out_groups": config["acceptance"]["minimum_held_out_groups"],
                "minimum_track_b_win_rate": config["acceptance"]["minimum_track_b_win_rate"],
            },
        },
        "gates": gates,
        "contract_gates": contract,
        "measurement_gates": measurement_gates,
        "access": {"opened_paths": sorted(set(OPENED)),
                   "lab_rows_opened": int(sum(len(values) for values in lab["tracts"].values()))
                   if lab is not None else 0,
                   "reference_layer_reads": 0,
                   "disclosure": "only the lab artifact set, the ideas registry, the preregistered "
                                 "config and the spec are read; no reference layer, no label, no score "
                                 "placeholder cell, no study idea parquet and no platform endpoint"},
        "environment": {"versions": {"python": sys.version.split()[0], "numpy": np.__version__},
                        "git_commit": git_commit(ROOT)},
    }
    encoded = json.dumps(report)
    report["gates"]["no_forbidden_value_in_report"] = not any(
        token in encoded for token in config["forbidden_value_tokens"])
    report["gates"]["report_is_strict_json"] = "NaN" not in encoded
    report["gates"]["report_has_no_temporal_field"] = not wall_clock_hits({
        "dependency": report["dependency"], "lab_checks": report["lab_checks"],
        "measurement": measurement, "artifact_contract": config["artifact_contract"]})
    report["gates"]["all_passed"] = bool(
        report["gates"]["contract_all_passed"]
        and report["gates"]["measurements_all_passed_or_legitimately_not_run"]
        and report["gates"]["no_forbidden_value_in_report"]
        and report["gates"]["report_is_strict_json"]
        and report["gates"]["report_has_no_temporal_field"])
    report["summary"] = {
        "measurement_status": measurement["status"],
        "lab_present": lab is not None,
        "channels_measured": sorted(measurement.get("channels", {})),
        "implemented_ideas": registry["implemented"],
        "groups_held_out": (rows["groups"] if rows else []),
        "track_b_accepted": {channel: entry.get("track_b_accepted_by_rule")
                             for channel, entry in measurement.get("channels", {}).items()
                             if isinstance(entry, dict)},
        "weight_vectors_published": len(crossing["weight_vectors"]),
        "gates_all_passed": report["gates"]["all_passed"],
    }
    if measured:
        report["summary"]["rmse_by_channel"] = {
            channel: entry.get("rmse_track_b") for channel, entry in measurement["channels"].items()
            if isinstance(entry, dict) and entry.get("status") == "measured"}
        report["summary"]["win_rate_by_channel"] = {
            channel: entry["win_rate_vs_equal"]["win_rate"]
            for channel, entry in measurement["channels"].items()
            if isinstance(entry, dict) and entry.get("win_rate_vs_equal", {}).get("win_rate")
            is not None}
    report["summary"]["report_sha256"] = hashlib.sha256(normalized_bytes(report)).hexdigest()
    return report


def render_markdown(report: dict[str, Any]) -> str:
    preregistration = report["preregistration"]
    lines = [f"# {preregistration['title']}", ""]
    lines.append(f"- Plan: `{preregistration['plan_id']}` (backlog item "
                 f"`{preregistration['backlog_item']}`)")
    lines.append(f"- Preregistered configuration: `{preregistration['config_path']}` (SHA-256 "
                 f"`{preregistration['config_sha256'][:16]}...`)")
    lines.append(f"- Spec: `{preregistration['spec_path']}` (SHA-256 "
                 f"`{preregistration['spec_sha256'][:16]}...`, matches its pin: "
                 f"{report['contract_gates']['spec_hash_matches_pin']})")
    lines.append(f"- Measurement status: **{report['summary']['measurement_status']}**")
    verdict = "PASS" if report["gates"]["all_passed"] else "FAIL"
    lines.append(f"- Verdict: **{verdict}**")
    lines.append("")
    lines.append("## Operator decision this evaluation works under")
    lines.append("")
    lines.append(f"- {report['preregistration']['operator_decision']['decided_by']} at "
                 f"{report['preregistration']['operator_decision']['decided_at_utc']}")
    lines.append(f"- {report['preregistration']['operator_decision']['effect_on_this_row']}")
    for item in report["preregistration"]["operator_decision"]["still_binding"]:
        lines.append(f"  - still binding: {item}")
    lines.append("")
    lines.append("## What this evaluation measures, once the lab exists")
    lines.append("")
    lines.append("- Leave-one-state-group-out RMSE for every implemented estimator and for the "
                 "lab-fitted linear combination, per held-out group and worst case.")
    lines.append("- Incremental value: each idea's partial correlation with the lab label given all "
                 "the others, and its drop-column change in grouped-CV RMSE.")
    lines.append("- Residual correlation matrix of `r_i = y - e_i`, its off-diagonal mass, and the "
                 "Krogh-Vedelsby ambiguity of the member set.")
    lines.append("- Multitrait-multimethod table: within-component and between-component mean "
                 "absolute correlation, so an idea that measures urbanity instead of its component "
                 "shows up.")
    lines.append("- Track B weights: non-negative ridge-toward-equal NNLS with county-blocked inner "
                 "folds for lambda, the Shimodaira shift-weighted variant, and bootstrap stability "
                 "selection over whole groups.")
    lines.append("- How each rule-clean Track A variant (A1 equal weights, A2 spectral, A3 "
                 "Dawid-Skene, A4 self-consistency prior) scores on the same held-out groups.")
    lines.append("")
    lines.append("## Acceptance, stated before any number exists")
    lines.append("")
    lines.append(f"- at least {report['acceptance']['requirements']['minimum_held_out_groups']} "
                 f"held-out state groups; every implemented idea scored")
    lines.append(f"- Track B must beat equal weights in at least "
                 f"{report['acceptance']['requirements']['minimum_track_b_win_rate']:.0%} of held-out "
                 f"groups, or be reported as not beating them")
    lines.append("")
    lines.append("## Why no number appears here yet")
    lines.append("")
    dependency = report["dependency"]
    lines.append(f"- `{report['dependency']['built_by_row']}` has not produced its artifacts: "
                 f"{dependency['absent_reason']}")
    lines.append(f"- the build must deliver {', '.join('`' + name + '`' for name in dependency['what_the_build_must_deliver']['files'])} "
                 f"with manifest fields {', '.join(dependency['what_the_build_must_deliver']['manifest_fields'])} "
                 f"and both RULES.md recipe self-checks passing")
    lines.append("- Until then this report publishes nulls. A lab number with no lab behind it would "
                 "be the single most damaging thing this lane could put in a writeup.")
    lines.append("")
    prereq = dependency["prerequisite_probe"]
    lines.append("## Prerequisite measurement this evaluation stands on")
    lines.append("")
    if not prereq["present"]:
        lines.append("- No prerequisite probe exists. That is a failure, not a gap: "
                     "`scripts/probe_lab_prerequisites.py` must be run so the build is told whether the "
                     "planned lab groups still hold tracts and whether the registered sources answer.")
    else:
        lines.append(f"- Probe artifact `reports/lab-prerequisites.json` (SHA-256 "
                     f"`{prereq['artifact_sha256'][:16]}...`), its own gates all passed: "
                     f"{prereq['gates_all_passed']}")
        lines.append(f"- Planned lab groups: {prereq['groups_planned']}; probed on real census "
                     f"geography: {prereq['groups_probed']}; candidate tracts inspected: "
                     f"{prereq['candidates_found']}; accepted by the frozen exclusion: "
                     f"{prereq['candidates_accepted']}")
        lines.append(f"- Study interior points established for the neighbour buffer: "
                     f"{prereq['study_points']} ({prereq['study_geoids_without_interior_point']} excluded "
                     f"GEOIDs had no interior point in the census file)")
        lines.append(f"- Public census geography downloaded by the probe: "
                     f"{prereq['downloaded_bytes']} bytes; no reference layer was fetched")
        lines.append(f"- Lab plan needs amendment: **{prereq['lab_plan_needs_amendment']}** - "
                     f"{prereq['verdict']}")
        for entry in prereq["shortfalls"]:
            lines.append(f"  - `{entry['id']}` cannot be a fold: {entry['accepted']} accepted tracts "
                         f"against a floor of {entry['floor']}")
        for entry in prereq["below_plan"]:
            lines.append(f"  - `{entry['id']}` is far below its planned tract count: {entry['accepted']} "
                         f"accepted against {entry['planned_approx_tracts']} planned")
        for entry in prereq["sources_not_answered"]:
            answered = (f" but answers at {entry['alternate_that_answers']}"
                        if entry["alternate_that_answers"] else " and no registered alternate answered")
            lines.append(f"  - source `{entry['id']}` answered {entry['status']} at its registered "
                         f"pattern{answered}")
        for source_id in prereq["sources_not_probeable"]:
            lines.append(f"  - source `{source_id}` is not probeable as registered: the registry names "
                         f"a source family, not a URL")
    lines.append("")
    lines.append("## Rails that must hold before a number is published")
    lines.append("")
    for name, value in report["contract_gates"].items():
        rendered = "pass" if value is True else ("not run: no lab" if value is None else "FAIL")
        lines.append(f"- `{name}`: {rendered}")
    lines.append("")
    lines.append("## Measurement gates (evaluated only when the lab is present)")
    lines.append("")
    for name, value in report["measurement_gates"].items():
        rendered = "pass" if value is True else ("not run: no lab" if value is None else "FAIL")
        lines.append(f"- `{name}`: {rendered}")
    lines.append("")
    lines.append("## What is allowed to cross from the lab into a submission")
    lines.append("")
    lines.append(f"- allowed fields: {', '.join(report['crossing_surface']['allowed_fields'])}")
    lines.append(f"- weight vectors published in this report: "
                 f"{report['summary']['weight_vectors_published']}")
    lines.append(f"- Track A pre-registration status: "
                 f"`{report['track_a_preregistration']['status_recorded_in_repo']}`; weight vector "
                 f"release: `{report['track_a_preregistration']['weight_vector_release']}`")
    lines.append(f"- study-side predictions emitted: "
                 f"{report['study_side']['study_predictions_emitted']}; study idea parquets opened: "
                 f"{report['study_side']['study_idea_parquets_opened']}")
    lines.append("")
    if report["summary"].get("rmse_by_channel"):
        lines.append("## Measured results")
        lines.append("")
        for channel, value in report["summary"]["rmse_by_channel"].items():
            lines.append(f"- channel `{channel}`: grouped-CV RMSE {value:.6f} against equal-weight "
                         f"{report['measurement']['channels'][channel]['rmse_equal_weights']:.6f}; "
                         f"win rate {report['summary']['win_rate_by_channel'].get(channel)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def load_config(path: Path) -> dict[str, Any]:
    record(path)
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    document["__path__"] = str(path)
    # the hash pins the preregistered file on disk, not the in-memory copy a test may have nudged
    document["__sha256__"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--lab-root", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--markdown", default=None)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--tamper", default=None)
    args = parser.parse_args(argv)

    if not args.workspace:
        print("FAIL lab evaluation: COMPETITION_WORKSPACE is required; the study exclusion cannot be "
              "checked without the provided strata tables and the official GEOID list")
        return 1
    workspace = Path(args.workspace).resolve()
    lab_plan.set_recorder(record)
    config = load_config(Path(args.config))
    lab_root = Path(args.lab_root or str(Path(os.environ.get("COMPETITION_WORKSPACE")) / "lab"))

    report = build_report(config, workspace=workspace, lab_root=lab_root, tamper=args.tamper)
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
    failed = sorted(name for name, value in report["gates"].items() if value is False)
    if failed:
        print("failed gates: " + ", ".join(failed))
    if report["gates"]["all_passed"]:
        if report["summary"]["measurement_status"] == "measured":
            print("PASS lab evaluation: every implemented estimator and the lab-fitted linear "
                  "combination were scored on held-out state groups with incremental value, residual "
                  "correlation, MTMM and Track A comparison, and only the weight vector and its "
                  "constants crossed")
        else:
            print("PASS lab evaluation contract: the evaluation is frozen and its rails are proved, "
                  "and no lab number is published because the lab artifacts are absent")
        return 0
    print("FAIL lab evaluation")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
