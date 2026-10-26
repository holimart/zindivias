#!/usr/bin/env python3
"""Label-free leave-one-witness-out sensitivity audit for the frozen Track A A3 model.

Reads only the frozen Track A config/report, the four registered witness Parquets, the
official GEOID lists, and their harness hashes. It never reads labels, sample score values,
candidate score rows, removed reference layers, or leaderboard data.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from build_track_a import dawid_skene, official_ids  # noqa: E402

WITNESS_ORDER = ("X1", "Sc2", "P3", "X5")
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _distribution(values: np.ndarray) -> dict[str, float | int]:
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0 or not np.isfinite(values).all():
        raise ValueError("distribution requires a nonempty finite vector")
    return {
        "n": int(values.size),
        "mean": float(values.mean()),
        "median": float(np.quantile(values, 0.5)),
        "q95": float(np.quantile(values, 0.95)),
        "max": float(values.max()),
    }


def _rows_to_witness_column(path: Path, idea_id: str, ids: list[str],
                            region_of: dict[str, str], binary_threshold: float
                            ) -> tuple[np.ndarray, dict[str, Any]]:
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "component", "channel", "value", "defined"])
    selected: dict[str, tuple[float, bool, bool, str]] = {}
    for row in table.to_pylist():
        geoid = str(row["GEOID"])
        if geoid not in region_of or row["idea"] != idea_id or row["region"] != region_of[geoid]:
            raise ValueError(f"{idea_id}: unexpected GEOID, idea, or region in {path}")
        # Match build_track_a.candidate_components: prefer the first F emission, else first emission.
        current = selected.get(geoid)
        is_f = row["component"] == "F"
        candidate = (float(row["value"]), bool(row["defined"]), is_f, str(row["component"]))
        if current is None or (is_f and not current[2]):
            selected[geoid] = candidate
        elif is_f and current[2]:
            raise ValueError(f"{idea_id}: multiple F emissions for {geoid}")
    if set(selected) != set(ids):
        raise ValueError(f"{idea_id}: GEOID coverage mismatch ({len(set(ids)-set(selected))} missing, "
                         f"{len(set(selected)-set(ids))} extra)")
    bits = np.full(len(ids), np.nan, dtype=np.float64)
    for i, geoid in enumerate(ids):
        value, defined, _is_f, _component = selected[geoid]
        if defined:
            if not np.isfinite(value):
                raise ValueError(f"{idea_id}: nonfinite defined witness for {geoid}")
            bits[i] = float(value >= binary_threshold)
    selected_components: dict[str, int] = {}
    for _value, _defined, _is_f, component in selected.values():
        selected_components[component] = selected_components.get(component, 0) + 1
    return bits, {"path": str(path), "rows": int(table.num_rows),
                  "sha256": sha256_bytes(path.read_bytes()),
                  "defined_ids": int(np.isfinite(bits).sum()),
                  "positive_bits": int(np.nansum(bits)),
                  "selected_components": selected_components,
                  "binary_threshold": float(binary_threshold)}


def compute_sensitivity(ids: list[str], region_of: dict[str, str], observations: np.ndarray,
                        witness_ids: tuple[str, ...] = WITNESS_ORDER
                        ) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Fit full and four three-witness models; return summaries and per-omission vectors."""
    bits = np.asarray(observations, dtype=np.float64)
    if bits.shape != (len(ids), len(witness_ids)) or len(witness_ids) != 4:
        raise ValueError("expected one four-column witness matrix aligned to official GEOIDs")
    full, full_meta = dawid_skene(bits)
    if not np.isfinite(full).all() or np.any((full < 0) | (full > 1)):
        raise ValueError("full-model posterior is not finite and bounded")
    full_repeat, full_meta_repeat = dawid_skene(bits)
    if not np.array_equal(full, full_repeat) or full_meta != full_meta_repeat:
        raise ValueError("full Dawid-Skene fit is not deterministic")

    outputs: dict[str, np.ndarray] = {}
    omission_rows: dict[str, Any] = {}
    for omitted_index, omitted in enumerate(witness_ids):
        keep = [j for j in range(len(witness_ids)) if j != omitted_index]
        loo, loo_meta = dawid_skene(bits[:, keep])
        loo_repeat, loo_meta_repeat = dawid_skene(bits[:, keep])
        if not np.array_equal(loo, loo_repeat) or loo_meta != loo_meta_repeat:
            raise ValueError(f"leave-one-out fit omitting {omitted} is not deterministic")
        if not np.isfinite(loo).all() or np.any((loo < 0) | (loo > 1)):
            raise ValueError(f"leave-one-out posterior omitting {omitted} is not bounded")
        shift = np.abs(loo - full)
        per_region = {}
        for region in REGIONS:
            mask = np.asarray([region_of[g] == region for g in ids], dtype=bool)
            if not mask.any():
                raise ValueError(f"official IDs have no rows for {region}")
            per_region[region] = {
                "posterior_shift": _distribution(shift[mask]),
                "full_posterior": _distribution(full[mask]),
                "loo_posterior": _distribution(loo[mask]),
                "official_ids": int(mask.sum()),
            }
        overall = _distribution(shift)
        omission_rows[omitted] = {
            "witnesses_retained": [witness_ids[j] for j in keep],
            "posterior_shift": overall,
            "regions": per_region,
            "refit": loo_meta,
        }
        outputs[omitted] = loo
    robust = all(omission_rows[omitted]["posterior_shift"]["q95"] <= 0.05
                 for omitted in witness_ids) and all(
        omission_rows[omitted]["regions"][region]["posterior_shift"]["q95"] <= 0.10
        for omitted in witness_ids for region in REGIONS)
    max_shift_by_omission = {w: omission_rows[w]["posterior_shift"]["max"] for w in witness_ids}
    max_shift_witness = max(max_shift_by_omission, key=max_shift_by_omission.get)
    result = {
        "full_model": full_meta,
        "full_posterior": {region: _distribution(full[[region_of[g] == region for g in ids]])
                            for region in REGIONS},
        "omissions": omission_rows,
        "acceptance": {
            "overall_q95_maximum": 0.05,
            "per_region_q95_maximum": 0.10,
            "robust": robust,
            "sensitivity_limited": not robust,
            "all_overall_q95_pass": all(omission_rows[w]["posterior_shift"]["q95"] <= 0.05
                                         for w in witness_ids),
            "all_region_q95_pass": all(omission_rows[w]["regions"][r]["posterior_shift"]["q95"] <= 0.10
                                        for w in witness_ids for r in REGIONS),
            "max_absolute_shift_by_omission": max_shift_by_omission,
            "largest_max_shift_witness": max_shift_witness,
        },
    }
    return result, outputs


def _parquet_bytes(ids: list[str], region_of: dict[str, str], witness_ids: tuple[str, ...],
                   full: np.ndarray, loo: np.ndarray, omitted: str) -> bytes:
    shift = np.abs(loo - full)
    table = pa.table({
        "GEOID": pa.array(ids, type=pa.string()),
        "region": pa.array([region_of[g] for g in ids], type=pa.string()),
        "omitted_witness": pa.array([omitted] * len(ids), type=pa.string()),
        "full_facility_miss_posterior": pa.array(full, type=pa.float64()),
        "leave_one_out_facility_miss_posterior": pa.array(loo, type=pa.float64()),
        "absolute_posterior_change": pa.array(shift, type=pa.float64()),
    })
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue()


def run(workspace: Path, out_path: Path, artifact_dir: Path) -> dict[str, Any]:
    config_path = ROOT / "config/track-a-preregistered.json"
    track_path = ROOT / "reports/track-a-build.json"
    harness_path = ROOT / "reports/ideas-harness.json"
    registry_path = ROOT / "config/ideas.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    track = json.loads(track_path.read_text(encoding="utf-8"))
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    config_hash = sha256_bytes(config_path.read_bytes())
    candidate = track["candidate_set"]["A3_dawid_skene"]
    frozen = candidate["diagnostics"]["dawid_skene"]
    witness_ids = tuple(frozen["witnesses"])
    if witness_ids != WITNESS_ORDER:
        raise ValueError(f"frozen A3 witness order changed: {witness_ids}")
    if track["preregistered_config_sha256"] != config_hash:
        raise ValueError("Track A report does not match the frozen preregistered config")
    if not set(witness_ids) <= set(config["selection"]["a3_witnesses"]):
        raise ValueError("frozen A3 witnesses are absent from the configured witness set")
    if not harness.get("gates", {}).get("all_passed"):
        raise ValueError("registered idea harness gates are not all passed")
    registry_ids = {entry["id"] for entry in registry["ideas"]}
    if not set(witness_ids) <= registry_ids:
        raise ValueError("a frozen A3 witness is absent from the idea registry")
    ids, region_of = official_ids()
    if len(ids) != 9379 or set(region_of.values()) != set(REGIONS):
        raise ValueError("official ID list does not match frozen A3 four-region scope")

    bits_by_witness = []
    witness_inputs = {}
    for idea_id in witness_ids:
        entry_hash = candidate["idea_input_sha256"].get(idea_id)
        harness_entry = harness["per_idea"].get(idea_id)
        if not entry_hash or not harness_entry or harness_entry["sha256"] != entry_hash:
            raise ValueError(f"{idea_id}: frozen Track A and harness input hashes disagree")
        path = workspace / "ideas" / f"{idea_id}.parquet"
        if not path.is_file() or sha256_bytes(path.read_bytes()) != entry_hash:
            raise ValueError(f"{idea_id}: existing registered witness artifact hash mismatch")
        item = next((row for row in registry["ideas"] if row["id"] == idea_id), None)
        if item is None or item["status"] != "implemented":
            raise ValueError(f"{idea_id}: witness is no longer registered as implemented")
        binary_rule = config["selection"]["a3_binary_witness"]
        if ">= 0.5" not in binary_rule:
            raise ValueError("unexpected frozen A3 binary threshold recipe")
        column, meta = _rows_to_witness_column(path, idea_id, ids, region_of, 0.5)
        bits_by_witness.append(column)
        witness_inputs[idea_id] = {**meta, "track_a_sha256": entry_hash,
                                   "family": item["family"], "component": item["component"]}
    observations = np.column_stack(bits_by_witness)
    summaries, loo_posteriors = compute_sensitivity(ids, region_of, observations, witness_ids)
    expected = {"prevalence": float(frozen["prevalence"]),
                "sensitivity": np.asarray(frozen["sensitivity"], dtype=float),
                "specificity": np.asarray(frozen["specificity"], dtype=float)}
    actual = summaries["full_model"]
    if (not np.isclose(actual["prevalence"], expected["prevalence"], rtol=1e-8, atol=1e-10)
            or not np.allclose(actual["sensitivity"], expected["sensitivity"], rtol=1e-8, atol=1e-10)
            or not np.allclose(actual["specificity"], expected["specificity"], rtol=1e-8, atol=1e-10)):
        raise ValueError("full refit does not reproduce frozen Track A Dawid-Skene diagnostics")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_manifest = {}
    full, _ = dawid_skene(observations)
    for omitted in witness_ids:
        first = _parquet_bytes(ids, region_of, witness_ids, full, loo_posteriors[omitted], omitted)
        repeated = _parquet_bytes(ids, region_of, witness_ids, full, loo_posteriors[omitted], omitted)
        digest = sha256_bytes(first)
        if first != repeated:
            raise ValueError(f"non-deterministic serialized artifact for omission {omitted}")
        path = artifact_dir / f"leave-one-out-omit-{omitted}.parquet"
        path.write_bytes(first)
        artifact_manifest[omitted] = {"path": str(path), "sha256": digest,
                                      "rows": len(ids), "deterministic_bytes": True}

    report = {
        "schema_version": 1,
        "audit": "A3 Dawid-Skene leave-one-witness-out sensitivity; measurement only, no promotion",
        "scope": {"official_ids": len(ids), "geoids_exactly_covered": len(ids),
                  "regions": {region: sum(region_of[g] == region for g in ids) for region in REGIONS},
                  "sample_score_values_read": False, "target_labels_read": False,
                  "candidate_modified": False, "uploads": 0},
        "frozen_inputs": {
            "track_a_report": {"path": "reports/track-a-build.json",
                                "sha256": sha256_bytes(track_path.read_bytes()),
                                "candidate_artifact_sha256_reference_only": candidate["sha256"],
                                "witness_order": list(witness_ids)},
            "config": {"path": "config/track-a-preregistered.json", "sha256": config_hash},
            "frozen_idea_registry_sha256": track["idea_registry_sha256"],
            "current_idea_registry_sha256": sha256_bytes(registry_path.read_bytes()),
            "frozen_harness_report_sha256": track["input_harness_sha256"],
            "current_harness_report_sha256": sha256_bytes(harness_path.read_bytes()),
            "registry_and_harness_changed_since_A3_build": (
                track["idea_registry_sha256"] != sha256_bytes(registry_path.read_bytes())
                or track["input_harness_sha256"] != sha256_bytes(harness_path.read_bytes())),
            "witness_artifacts": witness_inputs,
            "binary_rule": config["selection"]["a3_binary_witness"],
            "dawid_skene_recipe": config["selection"]["a3_dawid_skene"],
        },
        "full_model_baseline": {"reported": frozen, "recomputed": summaries["full_model"],
                                "matches_frozen_report": True,
                                "facility_miss_posterior_distribution_by_region": summaries["full_posterior"]},
        "metric": "95th percentile absolute per-tract change in A3 facility-miss posterior, compared with full four-witness fit",
        "omissions": summaries["omissions"],
        "acceptance": summaries["acceptance"],
        "per_tract_artifacts": artifact_manifest,
        "determinism": {"full_fit_repeated_exactly": True,
                        "all_leave_one_out_fits_repeated_exactly": True,
                        "serialized_parquet_bytes_repeated_exactly": True},
        "forbidden_data_used": False,
        "result_interpretation": (
            "A3 is sensitivity-limited by the frozen q95 thresholds; do not promote on this evidence."
            if not summaries["acceptance"]["robust"] else
            ("Frozen q95 robustness thresholds pass, but rare per-tract maxima remain large "
             f"(largest absolute shift {summaries['acceptance']['max_absolute_shift_by_omission'][summaries['acceptance']['largest_max_shift_witness']]:.6g} when omitting "
             f"{summaries['acceptance']['largest_max_shift_witness']}); treat as quantile-robust, not pointwise invariant. "
             "This sensitivity screen alone does not promote A3.")),

    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                        encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/a3-witness-sensitivity.json")
    parser.add_argument("--artifact-dir", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else workspace / "a3-witness-sensitivity"
    report = run(workspace, report_path, artifact_dir)
    output = {"out": str(report_path.relative_to(ROOT)),
              "robust": report["acceptance"]["robust"],
              "max_shift_by_omission": report["acceptance"]["max_absolute_shift_by_omission"],
              "overall_q95_by_omission": {w: d["posterior_shift"]["q95"]
                                          for w, d in report["omissions"].items()},
              "regional_q95_by_omission": {w: {r: d["posterior_shift"]["q95"]
                                                  for r, d in item["regions"].items()}
                                             for w, item in report["omissions"].items()},
              "report_sha256": sha256_bytes(report_path.read_bytes()),
              "artifacts": report["per_tract_artifacts"]}
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
