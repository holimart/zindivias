#!/usr/bin/env python3
"""Infer public-slice residual moments from pre-registered subset probes.

For a frozen candidate p=base+delta*1_S, this computes
E[(y-base)1_S] = (MSE(base)-MSE(p)+delta**2*P(S))/(2*delta).
P(S) is proxied by the authoritative full official-sample share; the report
makes the associated relative-composition sensitivity explicit. This reads
no labels, sample score placeholders, hidden references, or external layers.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
SCORE_COLUMN = "coverage_gap_score"
BASE_PHASE1 = ("C0", "C1", "C2", "C3")
IDENTIFIER_NAMES = {"geoid", "id", "tract_id", "geography_id"}
TARGET_OR_SCORE_NAMES = {
    "coverage_gap_score", "transport_gap", "building_gap", "poi_gap", "target", "label", "y"
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def mean(values) -> float:
    values = list(values)
    if not values:
        raise ValueError("cannot average an empty sequence")
    return sum(values) / len(values)


def inferred_cross_moment(mse_zero: float, candidate_rmse: float, p2: float) -> float:
    """Infer E[p*y] from the zero-candidate MSE and candidate RMSE."""
    if not all(math.isfinite(x) for x in (mse_zero, candidate_rmse, p2)):
        raise ValueError("score algebra inputs must be finite")
    if mse_zero < 0 or candidate_rmse < 0 or p2 < 0:
        raise ValueError("MSE, RMSE, and E[p^2] must be nonnegative")
    return (mse_zero + p2 - candidate_rmse * candidate_rmse) / 2.0


def inferred_subset_moment(mse_base: float, mse_probe: float, delta: float, p_share: float) -> float:
    """Return E[(y-base)1_S] for p=base+delta*1_S."""
    if not all(math.isfinite(x) for x in (mse_base, mse_probe, delta, p_share)):
        raise ValueError("subset algebra inputs must be finite")
    if mse_base < 0 or mse_probe < 0:
        raise ValueError("MSE values must be nonnegative")
    if delta == 0:
        raise ValueError("delta must be nonzero")
    if not 0 <= p_share <= 1:
        raise ValueError("P(S) must be in [0,1]")
    return (mse_base - mse_probe + delta * delta * p_share) / (2.0 * delta)


def validate_cell_size(cell_id: str, official_rows: int, minimum: int) -> None:
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 250:
        raise ValueError(f"{cell_id}: min_official_rows must be at least 250")
    if official_rows < minimum:
        raise ValueError(f"{cell_id}: only {official_rows} official rows; minimum is {minimum}")


def composition_error_bound(delta: float, p_share: float, eps: float) -> float:
    """Bound moment error when the public share differs relatively by <= eps."""
    if not all(math.isfinite(x) for x in (delta, p_share, eps)):
        raise ValueError("composition-bound inputs must be finite")
    if not 0 <= p_share <= 1 or eps < 0:
        raise ValueError("P(S) must be in [0,1] and eps must be nonnegative")
    return abs(delta) * p_share * eps / 2.0


def read_geoid_ids(path: Path) -> list[str]:
    # The per-region sample files contain score placeholders; access GEOID only.
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        headers = next(reader, [])
        if "GEOID" not in headers:
            raise ValueError(f"sample lacks GEOID column: {path}")
        geoid_i = headers.index("GEOID")
        ids = [row[geoid_i] for row in reader]
    if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"sample has empty or duplicate GEOIDs: {path}")
    return ids


def read_template_projection(path: Path, requested_fields: set[str]) -> tuple[list[str], dict[str, dict[str, str]], str]:
    """Read only GEOID and requested metadata fields; never inspect score placeholders."""
    if any(field.lower() in IDENTIFIER_NAMES for field in requested_fields):
        raise ValueError("cell selectors cannot use tract identifiers")
    if requested_fields - {"region"}:
        raise ValueError("the legacy template may supply only GEOID/region metadata; score placeholders are forbidden")
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        headers = next(reader, [])
        required = {"GEOID", *requested_fields}
        if required - set(headers):
            raise ValueError(f"template lacks requested metadata columns: {sorted(required - set(headers))}")
        geoid_i = headers.index("GEOID")
        field_indices = {field: headers.index(field) for field in requested_fields}
        ids: list[str] = []
        projection: dict[str, dict[str, str]] = {}
        for row in reader:
            geoid = row[geoid_i]
            if not geoid or geoid in projection:
                raise ValueError(f"template has empty or duplicate GEOID: {geoid!r}")
            ids.append(geoid)
            projection[geoid] = {field: row[index] for field, index in field_indices.items()}
    digest_rows = [[geoid, *(projection[geoid][f] for f in sorted(requested_fields))] for geoid in ids]
    projection_hash = sha256_bytes(canonical_json_bytes(digest_rows))
    return ids, projection, projection_hash


def read_metadata_projection(
    path: Path, requested_fields: set[str], official_ids: set[str]
) -> tuple[dict[str, dict[str, str]], str]:
    """Read only GEOID and selector fields from a caller-declared provided-data table."""
    if not requested_fields or any(field.lower() in IDENTIFIER_NAMES for field in requested_fields):
        raise ValueError("metadata selectors must use non-ID categorical fields")
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        headers = set(reader.fieldnames or [])
        required = {"GEOID", *requested_fields}
        if required - headers:
            raise ValueError(f"metadata file lacks requested columns: {sorted(required - headers)}")
        projection: dict[str, dict[str, str]] = {}
        for row in reader:
            geoid = row["GEOID"]
            if geoid not in official_ids:
                continue
            if geoid in projection:
                raise ValueError(f"metadata has duplicate official GEOID: {geoid}")
            projection[geoid] = {field: row[field] for field in requested_fields}
    if set(projection) != official_ids:
        missing = sorted(official_ids - set(projection))[:5]
        raise ValueError(f"metadata must classify all official IDs; missing examples: {missing}")
    rows = [[geoid, *(projection[geoid][f] for f in sorted(requested_fields))]
            for geoid in sorted(official_ids)]
    return projection, sha256_bytes(canonical_json_bytes(rows))


def read_candidate(
    path: Path, expected_ids: list[str], expected_sha256: str
) -> dict[str, float]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"candidate hash does not match frozen manifest: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        headers = list(reader.fieldnames or [])
        if "GEOID" not in headers or SCORE_COLUMN not in headers:
            raise ValueError(f"candidate needs GEOID and {SCORE_COLUMN}: {path}")
        values: dict[str, float] = {}
        row_ids = []
        for row in reader:
            geoid = row["GEOID"]
            value = float(row[SCORE_COLUMN])
            if not geoid or geoid in values or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"invalid or duplicate candidate row in {path}: {geoid}")
            values[geoid] = value
            row_ids.append(geoid)
    if row_ids != expected_ids:
        raise ValueError(f"candidate IDs/order do not match the frozen platform template: {path}")
    return values


def load_score_records(ledger_path: Path, entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for line_no, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid submissions ledger JSON on line {line_no}") from exc
        cid = record.get("candidate_id")
        if cid in entries and record.get("event") == "score_refresh":
            latest[cid] = record
    scores: dict[str, dict[str, Any]] = {}
    for cid, record in latest.items():
        if (record.get("status") == "successful"
                and record.get("score_source") == "zindi_api_status"
                and isinstance(record.get("our_score"), (int, float))
                and math.isfinite(float(record["our_score"]))):
            if record.get("sha256") != entries[cid].get("sha256"):
                raise ValueError(f"ledger/candidate hash mismatch for scored candidate {cid}")
            if record.get("split", record.get("score_split", "public")) != "public":
                raise ValueError(f"non-public score found for candidate {cid}")
            scores[cid] = record
    return scores


def phase1_moments(
    official_ids: list[str], candidates: dict[str, dict[str, float]], scores: dict[str, dict[str, Any]]
) -> tuple[float, dict[str, dict[str, Any]]]:
    if set(BASE_PHASE1) - set(scores):
        raise ValueError("ledger needs successful public score refreshes for C0-C3")
    rmse = {cid: float(scores[cid]["our_score"]) for cid in BASE_PHASE1}
    mse0 = rmse["C0"] ** 2
    moments: dict[str, dict[str, Any]] = {}
    for cid in BASE_PHASE1:
        p = [candidates[cid][geoid] for geoid in official_ids]
        p2 = mean(x * x for x in p)
        py = (mse0 + p2 - rmse[cid] * rmse[cid]) / 2.0
        corr = py / math.sqrt(mse0 * p2) if p2 > 0 and mse0 > 0 else None
        alpha = py / p2 if p2 > 0 else None
        projected = math.sqrt(max(0.0, mse0 - py * py / p2)) if p2 > 0 else math.sqrt(mse0)
        moments[cid] = {
            "public_rmse": rmse[cid],
            "public_mse": rmse[cid] ** 2,
            "E_p2_all_official_rows": p2,
            "inferred_E_py": py,
            "uncentred_correlation_proxy": corr,
            "unconstrained_optimal_global_scale": alpha,
            "predicted_rmse_at_unconstrained_scale": projected,
        }
    return mse0, moments


def phase1_residual_moments(
    official_ids: list[str], candidates: dict[str, dict[str, float]], moments: dict[str, dict[str, Any]]
) -> dict[str, float]:
    q = {g: candidates["C1"][g] for g in official_ids}
    c2 = {g: candidates["C2"][g] for g in official_ids}
    q2 = mean(q[g] * q[g] for g in official_ids)
    d = {g: c2[g] - q[g] for g in official_ids}
    d2 = mean(d[g] * d[g] for g in official_ids)
    qd = mean(q[g] * d[g] for g in official_ids)
    qy = moments["C1"]["inferred_E_py"]
    c2y = moments["C2"]["inferred_E_py"]
    dy = c2y - qy
    return {"E_q2": q2, "E_d2": d2, "E_qd": qd,
            "inferred_E_qy": qy, "inferred_E_dy": dy}


def build_report(
    *, workspace: Path, manifest_path: Path, ledger_path: Path, cell_path: Path,
    reference_path: Path, official_ids: list[str], template_ids: list[str],
    official_region: dict[str, str], metadata_projections: dict[str, dict[str, dict[str, str]]],
    metadata_hashes: dict[str, str], metadata_paths: dict[str, Path],
    manifest: dict[str, Any], entries: dict[str, dict[str, Any]],
    candidate_values: dict[str, dict[str, float]], scores: dict[str, dict[str, Any]], cells: dict[str, Any]
) -> dict[str, Any]:
    if "C0" not in candidate_values or "C0" not in scores:
        raise ValueError("C0 must be present as the scored zero baseline")
    mse0, phase1 = phase1_moments(official_ids, candidate_values, scores)
    residual = phase1_residual_moments(official_ids, candidate_values, phase1)
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    expected_phase1 = reference["phase1"]
    expected_residual = reference["P5_residual_moments"]
    phase1_equal = phase1 == expected_phase1["candidates"] and mse0 == expected_phase1["C0_target_second_moment_proxy"]
    residual_equal = residual == expected_residual
    if not phase1_equal or not residual_equal:
        raise ValueError("recomputed legacy phase-1/2 moments differ from reports/lb-score-algebra.json")
    legacy_phase1 = {
        "C0_target_second_moment_proxy": mse0,
        "rmse0": float(scores["C0"]["our_score"]),
        "candidates": phase1,
    }
    legacy_bundle = {"phase1": legacy_phase1, "P5_residual_moments": residual}
    expected_bundle = {"phase1": expected_phase1, "P5_residual_moments": expected_residual}

    scored_candidates: dict[str, dict[str, Any]] = {}
    for cid, record in scores.items():
        p2 = mean(candidate_values[cid][g] * candidate_values[cid][g] for g in official_ids)
        py = inferred_cross_moment(mse0, float(record["our_score"]), p2)
        scored_candidates[cid] = {
            "public_rmse": float(record["our_score"]),
            "submission_id": record.get("platform_submission_id"),
            "sha256": entries[cid]["sha256"],
            "E_p2_all_official_rows": p2,
            "inferred_E_py": py,
            "score_source": record.get("score_source"),
        }

    cell_results = []
    for cell in cells["cells"]:
        if not isinstance(cell, dict):
            raise ValueError("each cell definition must be an object")
        forbidden = {"geoids", "ids", "geoid_list", "tract_ids"} & set(cell)
        if forbidden:
            raise ValueError(f"per-GEOID cells are forbidden: {sorted(forbidden)}")
        cell_id = cell.get("cell_id")
        if not isinstance(cell_id, str) or not cell_id:
            raise ValueError("cell_id must be a nonempty string")
        selector = cell.get("selector")
        if not isinstance(selector, dict) or set(selector) != {"source", "field", "equals"}:
            raise ValueError(f"{cell_id}: selector must be source/field/equals categorical metadata")
        field = selector["field"]
        if not isinstance(field, str) or field.lower() in IDENTIFIER_NAMES:
            raise ValueError(f"{cell_id}: cell selector cannot be a GEOID or identifier")
        source = selector["source"]
        if source not in metadata_projections:
            raise ValueError(f"{cell_id}: unknown metadata source {source!r}")
        if field not in metadata_projections[source].get("__fields__", {}):
            raise ValueError(f"{cell_id}: field {field!r} was not loaded from source {source!r}")
        eq = selector["equals"]
        if not isinstance(eq, str):
            raise ValueError(f"{cell_id}: selector equals value must be a string")
        members = [g for g in official_ids
                   if metadata_projections[source][g][field] == eq]
        minimum = cell.get("min_official_rows", 250)
        validate_cell_size(cell_id, len(members), minimum)
        delta = float(cell["delta"])
        if not math.isfinite(delta) or delta == 0:
            raise ValueError(f"{cell_id}: delta must be finite and nonzero")
        eps = float(cell.get("public_share_relative_error_eps", cells.get("public_share_relative_error_eps", -1)))
        if not math.isfinite(eps) or eps < 0:
            raise ValueError(f"{cell_id}: public share relative-error eps must be nonnegative")
        base_id = cell.get("base_candidate_id")
        probe_id = cell.get("probe_candidate_id")
        if base_id not in entries or probe_id not in entries:
            raise ValueError(f"{cell_id}: base/probe candidate is absent from the manifest")
        if base_id not in candidate_values or probe_id not in candidate_values:
            raise ValueError(f"{cell_id}: base/probe candidate CSV was not loaded")
        member_set = set(members)
        base_values = candidate_values[base_id]
        probe_values = candidate_values[probe_id]
        for geoid in official_ids:
            expected = base_values[geoid] + (delta if geoid in member_set else 0.0)
            if abs(probe_values[geoid] - expected) > 1e-12:
                raise ValueError(f"{cell_id}: candidate is not base+delta*indicator at {geoid}")
        share = len(members) / len(official_ids)
        error_bound = composition_error_bound(delta, share, eps)
        base_score = scores.get(base_id)
        probe_score = scores.get(probe_id)
        if not base_score:
            raise ValueError(f"{cell_id}: base candidate {base_id} must already have a public score")
        status = "scored" if probe_score else "awaiting_public_score"
        moment = None
        if base_score and probe_score:
            moment = inferred_subset_moment(
                float(base_score["our_score"]) ** 2,
                float(probe_score["our_score"]) ** 2,
                delta,
                share,
            )
        cell_results.append({
            "cell_id": cell_id,
            "status": status,
            "selector": selector,
            "official_rows": len(members),
            "official_total_rows": len(official_ids),
            "official_share_proxy": share,
            "min_official_rows": minimum,
            "base_candidate_id": base_id,
            "base_rmse": float(base_score["our_score"]) if base_score else None,
            "base_submission_id": base_score.get("platform_submission_id") if base_score else None,
            "probe_candidate_id": probe_id,
            "probe_sha256": entries[probe_id]["sha256"],
            "probe_predicted_rmse": entries[probe_id].get("parameters", {}).get("predicted_public_rmse"),
            "probe_rmse": float(probe_score["our_score"]) if probe_score else None,
            "probe_submission_id": probe_score.get("platform_submission_id") if probe_score else None,
            "delta": delta,
            "E[(y-base)1_S]_public_estimate": moment,
            "public_share_relative_error_eps_assumption": eps,
            "composition_error_bound_abs": error_bound,
            "composition_error_bound_interpretation": "Conditional on the public cell share differing from the official share by at most eps in relative terms.",
            "metadata_projection_sha256": metadata_hashes[source],
        })

    return {
        "method": "public score-algebra subset moments for preregistered probe-as-candidate pairs",
        "formula": "E[(y-base)1_S] = (MSE(base)-MSE(probe)+delta^2*P(S))/(2*delta), for probe=base+delta*1_S",
        "score_identity": "MSE(p)=E[y^2]-2E[p*y]+E[p^2]",
        "no_labels_or_removed_references_used": True,
        "sample_score_placeholders_read": False,
        "official_scored_rows": len(official_ids),
        "platform_template_rows": len(template_ids),
        "eastern_wa_template_rows_excluded_from_cells": len(template_ids) - len(official_ids),
        "composition_assumption": "P(S) is proxied by the full authoritative official-row share. Public-slice moments are estimates under representative composition; every cell reports a conditional bound for its configured relative-share error eps.",
        "input_lineage": {
            "candidate_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "submissions_ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
            "cell_definitions_sha256": hashlib.sha256(cell_path.read_bytes()).hexdigest(),
            "official_geoid_sequence_sha256": sha256_bytes(canonical_json_bytes(official_ids)),
            "metadata_projection_sha256": metadata_hashes,
            "metadata_source_paths": {
                source: ("$COMPETITION_WORKSPACE/SampleSubmission.csv" if source == "template"
                         else str(path))
                for source, path in metadata_paths.items() if source in metadata_hashes
            },
        },
        "legacy_reproduction": {
            "source_report": str(reference_path.relative_to(ROOT)),
            "phase1_and_phase2_inferred_moments_equal": phase1_equal and residual_equal,
            "canonical_phase1_sha256": canonical_json_sha256(legacy_phase1),
            "reference_phase1_sha256": canonical_json_sha256(expected_phase1),
            "canonical_P5_residual_sha256": canonical_json_sha256(residual),
            "reference_P5_residual_sha256": canonical_json_sha256(expected_residual),
            "moments": legacy_bundle,
        },
        "scored_candidate_moments": scored_candidates,
        "subset_probe_moments": cell_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, default=ROOT / "reports/submissions.jsonl")
    parser.add_argument("--manifest", type=Path, default=ROOT / "reports/candidates.json")
    parser.add_argument("--cells", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/lb-subset-moments.json")
    parser.add_argument("--reference", type=Path, default=ROOT / "reports/lb-score-algebra.json")
    parser.add_argument("--template", type=Path)
    parser.add_argument("--metadata", action="append", default=[], metavar="NAME=PATH",
                        help="additional provided-data metadata table; selectors may reference NAME")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    manifest_path = args.manifest.resolve()
    ledger_path = args.ledger.resolve()
    cell_path = args.cells.resolve()
    reference_path = args.reference.resolve()
    template_path = (args.template or workspace / "SampleSubmission.csv").resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cell_definitions = json.loads(cell_path.read_text(encoding="utf-8"))
    entries = {item["candidate_id"]: item for item in manifest.get("candidate_set", [])}
    if len(entries) != len(manifest.get("candidate_set", [])):
        raise ValueError("candidate manifest has duplicate candidate IDs")
    if not isinstance(cell_definitions.get("cells"), list) or not cell_definitions["cells"]:
        raise ValueError("cell-definition JSON must contain a nonempty cells array")
    cell_ids = [cell.get("cell_id") for cell in cell_definitions["cells"] if isinstance(cell, dict)]
    if len(cell_ids) != len(cell_definitions["cells"]) or len(cell_ids) != len(set(cell_ids)):
        raise ValueError("cell definitions must have unique cell_id values")
    for key in ("geoids", "ids", "geoid_list", "tract_ids"):
        if key in cell_definitions:
            raise ValueError("per-GEOID cell definitions are forbidden")

    metadata_paths = {"template": template_path}
    for item in args.metadata:
        if "=" not in item:
            raise ValueError("--metadata must be NAME=PATH")
        name, raw_path = item.split("=", 1)
        if not name or name == "template" or name in metadata_paths:
            raise ValueError(f"invalid or duplicate metadata source alias: {name!r}")
        source_path = Path(raw_path).resolve()
        if not any(source_path.is_relative_to(root) for root in (workspace, ROOT)):
            raise ValueError("cell metadata must be challenge-provided in the workspace or a derived repo artifact")
        metadata_paths[name] = source_path

    official_ids: list[str] = []
    official_region: dict[str, str] = {}
    for region in REGIONS:
        ids = read_geoid_ids(workspace / "samples" / f"{region}-sample-submission.csv")
        official_ids.extend(ids)
        for geoid in ids:
            if geoid in official_region:
                raise ValueError(f"duplicate official GEOID: {geoid}")
            official_region[geoid] = region
    if len(official_ids) != 9379:
        raise ValueError(f"expected 9,379 official IDs, found {len(official_ids)}")
    official_set = set(official_ids)
    template_ids, _, _ = read_template_projection(template_path, set())
    if len(template_ids) != 9794 or not official_set.issubset(template_ids):
        raise ValueError("template must contain the 9,379 official IDs within 9,794 rows")

    requested_fields: dict[str, set[str]] = {}
    for cell in cell_definitions["cells"]:
        selector = cell.get("selector", {})
        source, field = selector.get("source"), selector.get("field")
        if not isinstance(source, str) or source not in metadata_paths:
            raise ValueError(f"unknown cell metadata source: {source!r}")
        if not isinstance(field, str) or field.lower() in IDENTIFIER_NAMES:
            raise ValueError("selectors must use provided-data categorical fields, never IDs")
        if field.lower() in TARGET_OR_SCORE_NAMES:
            raise ValueError("target and score columns cannot define cells")
        if source == "template" and field != "region":
            raise ValueError("template selectors are restricted to provided region metadata; score placeholders are forbidden")
        requested_fields.setdefault(source, set()).add(field)

    metadata_projections: dict[str, dict[str, dict[str, str]]] = {}
    metadata_hashes: dict[str, str] = {}
    for source, fields in requested_fields.items():
        if source == "template":
            template_ids, projection, projection_hash = read_template_projection(metadata_paths[source], fields)
            if len(template_ids) != 9794 or not official_set.issubset(template_ids):
                raise ValueError("template must contain the 9,379 official IDs within 9,794 rows")
            if any(projection[g].get("region") != official_region[g]
                   for g in official_ids if "region" in fields):
                raise ValueError("template region metadata disagrees with official sample regions")
        else:
            projection, projection_hash = read_metadata_projection(metadata_paths[source], fields, official_set)
        projection["__fields__"] = {field: {} for field in fields}
        metadata_projections[source] = projection
        metadata_hashes[source] = projection_hash
    # Candidate IDs/order always follow the steward-required full template;
    # the 415 eastern-WA rows are never included in official-cell shares.
    scores = load_score_records(ledger_path, entries)
    candidates_needed = set(BASE_PHASE1) | set(scores)
    for cell in cell_definitions["cells"]:
        candidates_needed.add(cell["base_candidate_id"])
        candidates_needed.add(cell["probe_candidate_id"])
    candidates: dict[str, dict[str, float]] = {}
    for cid in sorted(candidates_needed):
        if cid not in entries:
            raise ValueError(f"candidate absent from manifest: {cid}")
        item = entries[cid]
        path = (ROOT / item["artifact"]).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError(f"candidate artifact path escapes repo or does not exist: {item['artifact']}")
        candidates[cid] = read_candidate(path, template_ids, item["sha256"])
    report = build_report(
        workspace=workspace, manifest_path=manifest_path, ledger_path=ledger_path,
        cell_path=cell_path, reference_path=reference_path, official_ids=official_ids,
        template_ids=template_ids, official_region=official_region,
        metadata_projections=metadata_projections, metadata_hashes=metadata_hashes,
        metadata_paths=metadata_paths,
        manifest=manifest, entries=entries, candidate_values=candidates, scores=scores,
        cells=cell_definitions,
    )
    out_path = args.out.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(f"PASS subset algebra: legacy phase-1/2 moments exact; {len(report['subset_probe_moments'])} cells checked; no uploads")
    for cell in report["subset_probe_moments"]:
        print(f"{cell['cell_id']}: {cell['status']}; n={cell['official_rows']}; "
              f"composition-bound={cell['composition_error_bound_abs']:.12g}")
    print(f"Report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
