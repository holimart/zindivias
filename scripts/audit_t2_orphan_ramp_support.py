#!/usr/bin/env python3
"""Audit T2 coverage and attached non-link support under the registered 2 km rule.

Uses only the provided roads-unfiltered layer, official ACS tract masks, provided RUCA strata,
and registered label-free T2/T2-v2 artifacts. This is a support screen, not a model fit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.t import _road_layer  # noqa: E402
from bias60.ideas.t_exact import t2_exact  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
CONFIG_PATH = ROOT / "config/bias60-harness-preregistered.json"
REGISTRY_PATH = ROOT / "config/bias60-family-t-exact-registry.json"
T2_SOURCE = ROOT / "src/bias60/ideas/t.py"
T2_EXACT_SOURCE = ROOT / "src/bias60/ideas/t_exact.py"
INPUTS_SOURCE = ROOT / "src/bias60/inputs.py"
PRIOR_SCREEN = ROOT / "reports/t2-orphan-ramp-residual-screen.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def classify_ruca(code: Any, covered: Any) -> str:
    """Keep provided RUCA primary codes as strata; surface uncovered/invalid values explicitly."""
    if not bool(covered) or code is None:
        return "missing_or_uncovered"
    try:
        number = float(code)
    except (TypeError, ValueError):
        return "missing_or_uncovered"
    if not math.isfinite(number) or not number.is_integer():
        return "missing_or_uncovered"
    # Preserve the source code, including 99, rather than silently folding it into 1-10.
    return str(int(number))


def positive_attached_support(defined: Sequence[bool], attached_length_km: Sequence[float]) -> np.ndarray:
    """A tract is support-positive only when the exact 2 km recipe contributes positive length."""
    defined_arr = np.asarray(defined, dtype=bool)
    length_arr = np.asarray(attached_length_km, dtype=np.float64)
    if defined_arr.shape != length_arr.shape:
        raise ValueError("attached-length values and defined mask must have identical shape")
    return defined_arr & np.isfinite(length_arr) & (length_arr > 0.0)


def support_backed_gate(
    support_count: int,
    official_count: int,
    regional_support_fractions: dict[str, float],
    *, overall_threshold: float = 0.80,
    region_threshold: float = 0.50,
) -> dict[str, Any]:
    if official_count <= 0:
        raise ValueError("official_count must be positive")
    overall_fraction = support_count / official_count
    passes = overall_fraction >= overall_threshold and all(
        value >= region_threshold for value in regional_support_fractions.values())
    return {
        "criterion": f"at least {overall_threshold:.0%} of official tracts have positive eligible attached non-link support overall and at least {region_threshold:.0%} in every region",
        "overall_fraction": overall_fraction,
        "region_fractions": dict(regional_support_fractions),
        "passes": bool(passes),
        "decision": "go" if passes else "no_go",
        "reason": None if passes else "effective attached-road support is below the frozen coverage gate; do not fit a follow-on model",
    }


def support_table(
    strata: Sequence[str],
    defined: Sequence[bool],
    support: Sequence[bool],
    orphan_length_positive: Sequence[bool],
) -> dict[str, dict[str, int | float]]:
    if not (len(strata) == len(defined) == len(support) == len(orphan_length_positive)):
        raise ValueError("RUCA and T2 support arrays must have equal length")
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {
        "official_tracts": 0, "T2_defined": 0, "attached_support_positive": 0,
        "T2_orphan_link_length_positive": 0,
    })
    for key, is_defined, has_support, has_orphan_length in zip(
            strata, defined, support, orphan_length_positive):
        row = counts[str(key)]
        row["official_tracts"] += 1
        row["T2_defined"] += int(bool(is_defined))
        row["attached_support_positive"] += int(bool(has_support))
        row["T2_orphan_link_length_positive"] += int(bool(has_orphan_length))
    result: dict[str, dict[str, int | float]] = {}
    def order_key(item: tuple[str, dict[str, int]]) -> tuple[int, int, str]:
        key = item[0]
        if key == "missing_or_uncovered":
            return (1, 0, key)
        try:
            return (0, int(key), key)
        except ValueError:
            return (0, 1000, key)

    for key, row in sorted(counts.items(), key=order_key):
        n = row["official_tracts"]
        result[key] = {
            **row,
            "T2_defined_fraction": row["T2_defined"] / n if n else 0.0,
            "attached_support_fraction": row["attached_support_positive"] / n if n else 0.0,
            "orphan_link_length_positive_fraction": row["T2_orphan_link_length_positive"] / n if n else 0.0,
        }
    return result


def _prior_measure_before() -> dict[str, Any]:
    report = json.loads(PRIOR_SCREEN.read_text(encoding="utf-8"))
    regional = report["measure_after"]["regional"]
    if set(regional) != set(REGIONS):
        raise ValueError("prior T2 residual report does not cover the four frozen regions")
    deltas = {region: float(regional[region]["delta_r2"]) for region in REGIONS}
    if not all(value < 0.0 for value in deltas.values()):
        raise ValueError("the cited prior T2 county-held-out screen is not negative in every region")
    return {
        "source_report": str(PRIOR_SCREEN.relative_to(ROOT)),
        "source_report_sha256": sha256_file(PRIOR_SCREEN),
        "metric_context": "prior county-held-out ΔR² on frozen S1 estimator-disagreement residual; not target accuracy",
        "T2_v1_covariate_defined_ids": report["scope"]["T2_feature_defined_ids"],
        "regional_delta_r2": deltas,
        "all_regions_negative": True,
        "prior_artifact_sha256": report["measure_before"]["artifact_sha256"],
    }


def audit_region(workspace: Path, region: str) -> dict[str, Any]:
    import pyarrow.parquet as pq

    config = load_config(CONFIG_PATH)
    pairs = [(str(geoid), str(name)) for geoid, name in official_scored_pairs(workspace)]
    expected_total = int(config["inputs"]["official_scored_rows"])
    if len(pairs) != expected_total or len({geoid for geoid, _ in pairs}) != expected_total:
        raise ValueError(f"official scored GEOIDs must be {expected_total} unique IDs")
    if {name for _, name in pairs} != set(REGIONS):
        raise ValueError("official sample regions differ from frozen four-region set")
    geoids = [geoid for geoid, name in pairs if name == region]
    expected_region_n = int(config["inputs"]["official_rows_by_region"][region])
    if len(geoids) != expected_region_n:
        raise ValueError(f"{region}: official GEOID count {len(geoids)} != {expected_region_n}")

    inputs = ProvidedInputs(config, workspace, region, geoids)
    ruca_data = inputs.strata(["ruca_primary", "ruca_covered", "ruca_primary_desc"])
    strata_keys = [classify_ruca(code, covered) for code, covered in zip(
        ruca_data["ruca_primary"], ruca_data["ruca_covered"])]
    descriptions: dict[str, set[str]] = defaultdict(set)
    for key, description in zip(strata_keys, ruca_data["ruca_primary_desc"]):
        if description is not None and str(description).strip():
            descriptions[key].add(str(description).strip())

    # Footer/schema fingerprint is collected from the same supplied road object used by T2-v2.
    seed = config["seeds"]["T2"]
    columns = ["class", "subclass", "connectors", "geometry"]
    parquet = _road_layer(inputs, columns, seed["unfiltered_layer"])
    schema_fingerprint = hashlib.sha256("\n".join(
        f"{column}:{parquet.schema_arrow.field(column).type}"
        for column in columns).encode()).hexdigest()
    road_metadata = {
        "rows": int(parquet.metadata.num_rows),
        "row_groups": int(parquet.metadata.num_row_groups),
        "selected_schema_sha256": schema_fingerprint,
        "columns_read": columns,
    }
    road_object = config["inputs"]["package_layer_path_template"].format(
        bucket=config["inputs"]["package_bucket"], region=region,
        layer=seed["unfiltered_layer"])
    file_info = inputs.filesystem.get_file_info(road_object)
    if file_info.size is not None and file_info.size >= 0:
        road_metadata["object_bytes"] = int(file_info.size)

    # Run the exact 2 km reader from the provided roads/masks, then compare to its frozen artifact.
    recomputed = t2_exact(inputs)
    emissions = {emission.channel: emission for emission in recomputed.emissions}
    required_v2 = {"covariate:orphan_link_segments", "covariate:attached_carriageway_length_km"}
    if set(emissions) != required_v2:
        raise ValueError(f"T2-v2 reader returned unexpected channels {sorted(emissions)}")
    t2_artifacts = inputs.idea_emissions(["T2", "T2-v2"])
    t2_v1_value, t2_v1_defined = t2_artifacts["T2"][("R", "covariate:orphan_link_length_km")]
    t2_v1_segments, t2_v1_segments_defined = t2_artifacts["T2"][("R", "covariate:orphan_link_segments")]
    v2_segments_value, v2_segments_defined = t2_artifacts["T2-v2"][("R", "covariate:orphan_link_segments")]
    v2_length_value, v2_length_defined = t2_artifacts["T2-v2"][("R", "covariate:attached_carriageway_length_km")]

    def compare(name: str, values: np.ndarray, defined: np.ndarray,
                frozen_values: np.ndarray, frozen_defined: np.ndarray) -> dict[str, Any]:
        values = np.asarray(values, dtype=np.float64)
        defined = np.asarray(defined, dtype=bool)
        frozen_values = np.asarray(frozen_values, dtype=np.float64)
        frozen_defined = np.asarray(frozen_defined, dtype=bool)
        if not (values.shape == defined.shape == frozen_values.shape == frozen_defined.shape):
            raise ValueError(f"{name}: exact output shape differs from registered artifact")
        max_abs = float(np.max(np.abs(values - frozen_values), initial=0.0))
        matches = bool(np.array_equal(defined, frozen_defined) and max_abs <= 1e-12)
        if not matches:
            raise ValueError(f"{region}: {name} recomputation differs from registered artifact; max abs={max_abs}")
        return {"matches_registered_artifact": True, "maximum_absolute_difference": max_abs,
                "defined_rows": int(defined.sum())}

    recomputed_segments = emissions["covariate:orphan_link_segments"]
    recomputed_length = emissions["covariate:attached_carriageway_length_km"]
    v2_segment_check = compare("T2-v2 orphan link segments", recomputed_segments.values,
                               recomputed_segments.defined, v2_segments_value, v2_segments_defined)
    v2_length_check = compare("T2-v2 attached carriageway length", recomputed_length.values,
                              recomputed_length.defined, v2_length_value, v2_length_defined)
    # The registered v1 quantity is link-segment length, not the attached road-support length.
    if not (np.isfinite(t2_v1_value).all() and np.isfinite(t2_v1_segments).all()
            and np.array_equal(t2_v1_defined, t2_v1_segments_defined)):
        raise ValueError(f"{region}: invalid or misaligned registered T2 values/masks")
    if geoids != recomputed.geoids:
        raise ValueError(f"{region}: recomputed T2-v2 output GEOID order differs from official list")

    t2_defined = np.asarray(t2_v1_defined, dtype=bool)
    t2_v1_nonzero = np.asarray(t2_v1_value, dtype=np.float64) > 0.0
    orphan_segments = np.asarray(v2_segments_value, dtype=np.float64)
    attached_km = np.asarray(v2_length_value, dtype=np.float64)
    support = positive_attached_support(v2_length_defined, attached_km)
    if (np.any(~np.isfinite(t2_v1_value)) or np.any(t2_v1_value < 0.0)
            or np.any(~np.isfinite(orphan_segments)) or np.any(orphan_segments < 0.0)
            or np.any(~np.isfinite(attached_km)) or np.any(attached_km < 0.0)):
        raise ValueError(f"{region}: T2 features contain non-finite or negative values")
    ruca_counts = support_table(strata_keys, t2_defined, support, t2_v1_nonzero)
    for key, cell in ruca_counts.items():
        desc = sorted(descriptions.get(key, set()))
        cell["ruca_primary_desc_values"] = desc

    strata_file = workspace / config["inputs"]["strata_cache_template"].format(region=region)
    if not strata_file.is_file():
        raise FileNotFoundError(f"provided RUCA strata cache is absent: {strata_file}")
    # Hash only the declared GEOID/RUCA fields, not unrequested columns in the wide strata table.
    ruca_digest = sha256_json([
        [geoid, strata_keys[index], bool(ruca_data["ruca_covered"][index]),
         (str(ruca_data["ruca_primary_desc"][index])
          if ruca_data["ruca_primary_desc"][index] is not None else None)]
        for index, geoid in enumerate(geoids)
    ])
    strata_schema = pq.ParquetFile(strata_file).schema_arrow
    strata_field_fingerprint = hashlib.sha256("\n".join(
        f"{column}:{strata_schema.field(column).type}"
        for column in ("GEOID", "ruca_primary", "ruca_covered", "ruca_primary_desc")
    ).encode()).hexdigest()

    support_count = int(support.sum())
    defined_count = int(t2_defined.sum())
    valid_code_mask = np.asarray([key != "missing_or_uncovered" for key in strata_keys], dtype=bool)
    missing_ruca_ids = [geoids[i] for i in range(len(geoids)) if not valid_code_mask[i]]
    missing_t2_ids = [geoids[i] for i in range(len(geoids)) if not t2_defined[i]]
    missing_v2_ids = [geoids[i] for i in range(len(geoids)) if not v2_length_defined[i]]
    per_tract = []
    for i, geoid in enumerate(geoids):
        raw_code = ruca_data["ruca_primary"][i]
        numeric_code = None
        if raw_code is not None and np.isfinite(float(raw_code)):
            numeric_code = int(raw_code) if float(raw_code).is_integer() else float(raw_code)
        description = ruca_data["ruca_primary_desc"][i]
        per_tract.append({
            "GEOID": geoid,
            "ruca_primary": numeric_code,
            "ruca_covered": bool(ruca_data["ruca_covered"][i]),
            "ruca_stratum": strata_keys[i],
            "ruca_primary_desc": str(description) if description is not None else None,
            "T2_defined": bool(t2_defined[i]),
            "T2_orphan_link_length_km": float(t2_v1_value[i]),
            "T2_orphan_link_length_positive": bool(t2_v1_nonzero[i]),
            "T2_v2_attached_length_defined": bool(v2_length_defined[i]),
            "T2_v2_attached_carriageway_length_km": float(attached_km[i]),
            "eligible_attached_support": bool(support[i]),
            "T2_v2_orphan_link_segments": float(orphan_segments[i]),
        })

    seed_length = float(seed["attached_length_limit_m"])
    if seed_length != 2000.0:
        raise ValueError(f"registered T2 support radius is unexpectedly {seed_length} m")
    return {
        "schema_version": 1,
        "audit": "T2 orphan-link definedness and attached non-link road support; diagnostic only",
        "scope": {
            "region": region,
            "official_geoids": geoids,
            "official_tracts": len(geoids),
            "official_geoid_order_sha256": hashlib.sha256("\n".join(geoids).encode()).hexdigest(),
            "labels_or_targets_read": False,
            "sample_score_values_read": False,
            "removed_references_read": False,
            "external_data_or_roads_read": False,
            "leaderboard_feedback_used": False,
            "fit_performed": False,
            "uploads": 0,
            "candidate_artifacts_changed": False,
        },
        "method": {
            "T2_v1_feature": "registered covariate:orphan_link_length_km; in-tract length of qualifying orphan link segments, not attached carriageways",
            "support_definition": "at least one qualifying endpoint-attached non-named non-link road contributes positive in-tract length after clipping to the 2,000 m buffer around an orphan link",
            "registered_2km_limit_m": seed_length,
            "orphan_rule": "link-class/subclass segment with both endpoint connectors attached to other roads and no named-class neighbor at either endpoint",
            "RUCA_strata": "provided ruca_primary codes with ruca_covered; code 99 is preserved as its own provided category; uncovered/null/nonintegral values are explicit missing_or_uncovered",
            "support_is_not_the_T2_v1_defined_mask": True,
        },
        "region_result": {
            "official_tracts": len(geoids),
            "T2_v1_defined_count": defined_count,
            "T2_v1_defined_fraction": defined_count / len(geoids),
            "T2_v1_missing_count": len(missing_t2_ids),
            "T2_v1_missing_geoids": missing_t2_ids,
            "T2_v2_attached_length_undefined_count": len(missing_v2_ids),
            "T2_v2_attached_length_undefined_geoids": missing_v2_ids,
            "RUCA_missing_or_uncovered_count": len(missing_ruca_ids),
            "RUCA_missing_or_uncovered_geoids": missing_ruca_ids,
            "any_T2_v1_orphan_link_length_positive_count": int(t2_v1_nonzero.sum()),
            "any_T2_v1_orphan_link_length_positive_fraction": float(t2_v1_nonzero.mean()),
            "eligible_attached_support_count": support_count,
            "eligible_attached_support_fraction": support_count / len(geoids),
            "eligible_attached_support_fraction_of_T2_defined": (
                float(np.sum(support & t2_defined) / defined_count) if defined_count else None),
            "T2_v1_registered_artifact": {
                "artifact_sha256": sha256_file(workspace / "ideas/T2.parquet"),
                "native_defined_rows": defined_count,
            },
            "T2_v2_registered_artifact_reproduction": {
                "artifact_sha256": sha256_file(workspace / "ideas/T2-v2.parquet"),
                "attached_length": v2_length_check,
                "orphan_link_segments": v2_segment_check,
            },
            "RUCA_support": ruca_counts,
            "t2_exact_diagnostics": recomputed.diagnostics,
            "per_tract": per_tract,
        },
        "inputs": {
            "provided_road_object": road_object,
            "provided_road_metadata": road_metadata,
            "provided_official_mask_layer": "census-acs-housing:GEOID,geometry",
            "provided_RUCA_cache": str(strata_file),
            "provided_RUCA_cache_columns_read": ["GEOID", "ruca_primary", "ruca_covered", "ruca_primary_desc"],
            "provided_RUCA_selected_fields_sha256": ruca_digest,
            "provided_RUCA_selected_schema_sha256": strata_field_fingerprint,
            "accessed_layers": sorted(set(inputs.accessed["layers"])),
            "accessed_columns": sorted(set(inputs.accessed["parquet_columns"])),
            "accessed_idea_artifacts": sorted(set(inputs.accessed.get("idea_artifacts", []))),
        },
        "provenance": {
            "audit_script_sha256": sha256_file(Path(__file__).resolve()),
            "t2_reader_sha256": sha256_file(T2_SOURCE),
            "t2_exact_reader_sha256": sha256_file(T2_EXACT_SOURCE),
            "provided_input_reader_sha256": sha256_file(INPUTS_SOURCE),
            "harness_config_sha256": sha256_file(CONFIG_PATH),
            "family_t_exact_registry_sha256": sha256_file(REGISTRY_PATH),
        },
    }


def combine_reports(paths: Sequence[Path], out: Path) -> dict[str, Any]:
    by_region: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in paths:
        part = json.loads(path.read_text(encoding="utf-8"))
        region = part["scope"]["region"]
        if region in by_region:
            raise ValueError(f"duplicate region report {region}")
        by_region[region] = (part, path)
    if set(by_region) != set(REGIONS):
        raise ValueError(f"need one region report for each official region; found {sorted(by_region)}")
    reports = {region: by_region[region][0] for region in REGIONS}
    stats = {region: reports[region]["region_result"] for region in REGIONS}
    total = sum(stats[region]["official_tracts"] for region in REGIONS)
    defined = sum(stats[region]["T2_v1_defined_count"] for region in REGIONS)
    supported = sum(stats[region]["eligible_attached_support_count"] for region in REGIONS)
    region_support = {region: stats[region]["eligible_attached_support_fraction"] for region in REGIONS}
    native_region_defined = {region: stats[region]["T2_v1_defined_fraction"] for region in REGIONS}
    support_gate = support_backed_gate(supported, total, region_support)
    native_gate = bool(defined / total >= 0.80 and all(value >= 0.50 for value in native_region_defined.values()))
    all_geoids = [geoid for region in REGIONS for geoid in reports[region]["scope"]["official_geoids"]]
    if total != 9379 or len(set(all_geoids)) != total:
        raise ValueError("combined reports do not form the exact unique 9,379-GEOID partition")
    for region in REGIONS:
        part = reports[region]
        ids = part["scope"]["official_geoids"]
        rows = part["region_result"]["per_tract"]
        if [row["GEOID"] for row in rows] != ids:
            raise ValueError(f"{region}: per-tract rows do not exactly match official GEOID order")
    report = {
        "schema_version": 1,
        "audit": "T2 orphan-link definedness and attached non-link road support; diagnostic only",
        "scope": {
            "official_regions": list(REGIONS),
            "official_tracts": total,
            "labels_or_targets_read": False,
            "sample_score_values_read": False,
            "removed_references_read": False,
            "external_data_or_roads_read": False,
            "leaderboard_feedback_used": False,
            "fit_performed": False,
            "uploads": 0,
            "candidate_artifacts_changed": False,
        },
        "measure_before": _prior_measure_before(),
        "regional_summary": {
            region: {
                "official_tracts": stats[region]["official_tracts"],
                "T2_v1_defined_count": stats[region]["T2_v1_defined_count"],
                "T2_v1_defined_fraction": stats[region]["T2_v1_defined_fraction"],
                "T2_v1_missing_count": stats[region]["T2_v1_missing_count"],
                "attached_support_count": stats[region]["eligible_attached_support_count"],
                "attached_support_fraction": stats[region]["eligible_attached_support_fraction"],
                "RUCA_missing_count": stats[region]["RUCA_missing_or_uncovered_count"],
            } for region in REGIONS
        },
        "overall": {
            "official_tracts": total,
            "T2_v1_defined_count": defined,
            "T2_v1_defined_fraction": defined / total,
            "T2_v1_missing_count": total - defined,
            "attached_support_count": supported,
            "attached_support_fraction": supported / total,
            "native_definedness_gate": {
                "criterion": "at least 80% defined overall and at least 50% defined in every region",
                "passes": native_gate,
                "region_fractions": native_region_defined,
            },
            "support_backed_follow_on_gate": support_gate,
            "follow_on_model_decision": {
                "decision": "go" if native_gate and support_gate["passes"] else "no_go",
                "native_definedness_passes": native_gate,
                "attached_support_passes": support_gate["passes"],
                "reason": "native T2 masks pass, but the support-backed coverage requirement fails; no follow-on model was fit" if native_gate and not support_gate["passes"] else support_gate["reason"],
            },
            "RUCA_missing_or_uncovered_count": sum(stats[r]["RUCA_missing_or_uncovered_count"] for r in REGIONS),
        },
        "interpretation": {
            "registered_T2_defined_mask_is_not_support": "T2-v1's emitted mask may define zero-valued tracts; it does not imply any attached non-link road exists. Report native definedness and positive attached support separately.",
            "no_fit": "The measured support-backed gate fails; no follow-on model was fit.",
            "target_limit": "No target accuracy or causal interpretation is claimed.",
        },
        "regions": reports,
        "reproduction": {
            "per_region": "uv run --with-requirements requirements.txt python scripts/audit_t2_orphan_ramp_support.py --workspace $COMPETITION_WORKSPACE --region <REGION> --out $COMPETITION_WORKSPACE/t2-orphan-ramp-support-checkpoints/<REGION>.json",
            "combine": "uv run --with-requirements requirements.txt python scripts/audit_t2_orphan_ramp_support.py --merge-region-reports <FOUR_REGION_JSON_PATHS> --out reports/t2-orphan-ramp-support.json",
            "run_policy": "Run one region per invocation; combine after all four regional JSON files are present.",
        },
        "provenance": {
            "audit_script_sha256": sha256_file(Path(__file__).resolve()),
            "region_reports": {
                region: {"path": str(by_region[region][1]), "sha256": sha256_file(by_region[region][1])}
                for region in REGIONS
            },
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    workspace_default = __import__("os").environ.get("COMPETITION_WORKSPACE")
    parser.add_argument("--workspace", type=Path,
                        default=Path(workspace_default) if workspace_default else None)
    parser.add_argument("--region", choices=REGIONS)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/t2-orphan-ramp-support.json")
    parser.add_argument("--merge-region-reports", nargs="+", type=Path,
                        help="combine four per-region audit JSON files into --out")
    args = parser.parse_args()
    if args.merge_region_reports:
        report = combine_reports(args.merge_region_reports, args.out)
        print(json.dumps({"official_tracts": report["scope"]["official_tracts"],
                          "T2_v1_defined_fraction": report["overall"]["T2_v1_defined_fraction"],
                          "attached_support_fraction": report["overall"]["attached_support_fraction"],
                          "decision": report["overall"]["support_backed_follow_on_gate"]["decision"],
                          "output": str(args.out)}, sort_keys=True))
        return 0
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    if args.region is None:
        parser.error("--region is required; audit regions separately and combine afterward")
    report = audit_region(args.workspace, args.region)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    result = report["region_result"]
    print(json.dumps({"region": args.region, "official_tracts": result["official_tracts"],
                      "T2_defined_fraction": result["T2_v1_defined_fraction"],
                      "attached_support_fraction": result["eligible_attached_support_fraction"],
                      "RUCA_missing": result["RUCA_missing_or_uncovered_count"],
                      "output": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
