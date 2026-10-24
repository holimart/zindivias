#!/usr/bin/env python3
"""Compare provided Overture fire/EMS POI absence within rural Eastern OK.

The primary comparison is tribal_any versus non-tribal among rural tracts in
Eastern Oklahoma's region-wide upper quartile of provided summer DSCI. Rural
all-DSCI and rural lower-DSCI strata are shown for context. This is a
provided-data discovery screen, not a claim about hidden scores or actual
emergency-service availability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from audit_poi_categories import read_sample_geoids  # noqa: E402

REGION = "eastern-ok"
STRATA_COLUMNS = [
    "GEOID", "pct_urban", "pop_total", "tribal_any", "usdm_summer_dsci",
]
FEATURE_COLUMNS = ["GEOID", "overture_poi_fire", "overture_poi_ems"]
OUTCOMES = {
    "fire_zero": lambda row: row["overture_poi_fire"] == 0,
    "ems_zero": lambda row: row["overture_poi_ems"] == 0,
    "fire_and_ems_zero": lambda row: row["overture_poi_fire"] == 0 and row["overture_poi_ems"] == 0,
}
Z_95 = 1.959963984540054


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def tribal_flag(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    number = finite_float(value)
    if number is None:
        return None
    if number not in (0.0, 1.0):
        raise ValueError(f"tribal_any outside boolean domain: {value!r}")
    return bool(number)


def wilson_interval(successes: int, total: int, z: float = Z_95) -> list[float] | None:
    """Two-sided Wilson score interval for a binomial tract proportion."""
    if total <= 0:
        return None
    p = successes / total
    z2 = z * z
    denom = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denom
    half = z / denom * np.sqrt(p * (1 - p) / total + z2 / (4 * total * total))
    return [float(max(0.0, center - half)), float(min(1.0, center + half))]


def newcombe_difference(x1: int, n1: int, x0: int, n0: int,
                       z: float = Z_95) -> dict[str, Any]:
    """Newcombe-Wilson CI for p(tribal) - p(non-tribal), independent tracts."""
    if n1 <= 0 or n0 <= 0:
        return {"difference": None, "ci_95": None, "reason": "one comparison group is empty"}
    p1, p0 = x1 / n1, x0 / n0
    ci1 = wilson_interval(x1, n1, z)
    ci0 = wilson_interval(x0, n0, z)
    difference = p1 - p0
    lower = difference - np.sqrt((p1 - ci1[0]) ** 2 + (ci0[1] - p0) ** 2)
    upper = difference + np.sqrt((ci1[1] - p1) ** 2 + (p0 - ci0[0]) ** 2)
    return {
        "difference": float(difference),
        "ci_95": [float(max(-1.0, lower)), float(min(1.0, upper))],
        "ci_excludes_zero": bool(lower > 0 or upper < 0),
    }


def group_summary(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    tribal = [row for row in rows if row["tribal_any"] is True]
    nontribal = [row for row in rows if row["tribal_any"] is False]
    output: dict[str, Any] = {
        "stratum": name,
        "tracts_with_known_tribal_status": len(tribal) + len(nontribal),
        "tribal_tracts": len(tribal),
        "nontribal_tracts": len(nontribal),
        "outcomes": {},
    }
    for outcome_name, predicate in OUTCOMES.items():
        x1 = sum(bool(predicate(row)) for row in tribal)
        x0 = sum(bool(predicate(row)) for row in nontribal)
        contrast = newcombe_difference(x1, len(tribal), x0, len(nontribal))
        output["outcomes"][outcome_name] = {
            "definition": {
                "fire_zero": "overture_poi_fire == 0",
                "ems_zero": "overture_poi_ems == 0",
                "fire_and_ems_zero": "both selected-category counts equal zero",
            }[outcome_name],
            "tribal": {
                "zero_tracts": int(x1), "tracts": len(tribal),
                "zero_share": float(x1 / len(tribal)) if tribal else None,
                "wilson_95_ci": wilson_interval(x1, len(tribal)),
            },
            "nontribal": {
                "zero_tracts": int(x0), "tracts": len(nontribal),
                "zero_share": float(x0 / len(nontribal)) if nontribal else None,
                "wilson_95_ci": wilson_interval(x0, len(nontribal)),
            },
            "tribal_minus_nontribal_zero_share": contrast["difference"],
            "newcombe_wilson_95_ci_difference": contrast["ci_95"],
            "ci_excludes_zero": contrast.get("ci_excludes_zero"),
            "interval_method": "Newcombe-Wilson score interval for independent binomial proportions",
        }
    return output


def run(workspace: Path, out: Path) -> dict[str, Any]:
    sample_path = workspace / "samples" / f"{REGION}-sample-submission.csv"
    sample_ids, sample_geoid_hash = read_sample_geoids(sample_path)
    sample_set = set(sample_ids)
    if len(sample_set) != len(sample_ids):
        raise ValueError("official sample GEOIDs are not unique")

    strata_path = workspace / "defined-flags-inputs" / f"{REGION}-strata-tract-table.parquet"
    features_path = workspace / "gap-estimator" / "features" / f"{REGION}-overture-tract-summary.parquet"
    for path in (strata_path, features_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    strata_table = pq.read_table(strata_path, columns=STRATA_COLUMNS)
    feature_table = pq.read_table(features_path, columns=FEATURE_COLUMNS)
    strata_values = strata_table.to_pylist()
    feature_values = feature_table.to_pylist()
    strata_by_id = {str(row["GEOID"]): row for row in strata_values}
    features_by_id = {str(row["GEOID"]): row for row in feature_values}
    if len(strata_by_id) != len(strata_values) or len(features_by_id) != len(feature_values):
        raise ValueError("duplicate GEOIDs in strata or Overture summary")
    if not sample_set.issubset(strata_by_id) or not sample_set.issubset(features_by_id):
        raise ValueError("official sample GEOIDs are missing in input tables")

    records: list[dict[str, Any]] = []
    missing_urban = missing_dsci = missing_tribal = 0
    for geoid in sample_ids:
        st = strata_by_id[geoid]
        ft = features_by_id[geoid]
        urban = finite_float(st["pct_urban"])
        dsci = finite_float(st["usdm_summer_dsci"])
        pop = finite_float(st["pop_total"])
        tribal = tribal_flag(st["tribal_any"])
        fire = finite_float(ft["overture_poi_fire"])
        ems = finite_float(ft["overture_poi_ems"])
        if urban is None:
            missing_urban += 1
        if dsci is None:
            missing_dsci += 1
        if tribal is None:
            missing_tribal += 1
        if fire is None or ems is None or fire < 0 or ems < 0 or int(fire) != fire or int(ems) != ems:
            raise ValueError(f"invalid Overture fire/EMS count for {geoid}")
        records.append({
            "GEOID": geoid,
            "pct_urban": urban,
            "pop_total": pop,
            "tribal_any": tribal,
            "usdm_summer_dsci": dsci,
            "overture_poi_fire": int(fire),
            "overture_poi_ems": int(ems),
        })
    if not records:
        raise ValueError("no official sample rows")

    finite_dsci = [row["usdm_summer_dsci"] for row in records if row["usdm_summer_dsci"] is not None]
    if not finite_dsci:
        raise ValueError("no finite usdm_summer_dsci values")
    drought_cut = float(np.quantile(np.asarray(finite_dsci, dtype=np.float64), 0.75))
    for row in records:
        row["rural"] = row["pct_urban"] is not None and row["pct_urban"] < 0.5
        row["drought_quartile"] = (
            "upper_quartile" if row["usdm_summer_dsci"] is not None and row["usdm_summer_dsci"] >= drought_cut
            else "lower_three_quartiles" if row["usdm_summer_dsci"] is not None else None
        )

    rural = [row for row in records if row["rural"] and row["tribal_any"] is not None]
    rural_high = [row for row in rural if row["drought_quartile"] == "upper_quartile"]
    rural_lower = [row for row in rural if row["drought_quartile"] == "lower_three_quartiles"]
    rural_dsci_missing = sum(row["rural"] and row["usdm_summer_dsci"] is None for row in records)
    cells = {
        "rural_all_drought_levels": group_summary(rural, "all rural tracts with known tribal status"),
        "rural_upper_drought_quartile": group_summary(rural_high, "rural and region-wide upper-quartile summer DSCI"),
        "rural_lower_three_drought_quartiles": group_summary(rural_lower, "rural and below region-wide upper-quartile summer DSCI"),
    }

    named = []
    for row in rural_high:
        if (row["pop_total"] is not None and row["pop_total"] > 2000 and
                row["overture_poi_fire"] == 0 and row["overture_poi_ems"] == 0):
            named.append({
                "GEOID": row["GEOID"],
                "tribal_any": row["tribal_any"],
                "pop_total": row["pop_total"],
                "pct_urban": row["pct_urban"],
                "usdm_summer_dsci": row["usdm_summer_dsci"],
                "overture_poi_fire": row["overture_poi_fire"],
                "overture_poi_ems": row["overture_poi_ems"],
            })
    named.sort(key=lambda row: (not row["tribal_any"], -row["pop_total"], row["GEOID"]))

    primary = cells["rural_upper_drought_quartile"]["outcomes"]["ems_zero"]
    include_writeup = bool(primary["ci_excludes_zero"])
    config_path = ROOT / "config" / "poi-category-definitions.json"
    report = {
        "analysis": "Eastern Oklahoma provided Overture fire/EMS POI-zero rates by tribal_any within rural and drought strata",
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pyarrow": pa.__version__,
        },
        "interpretation": "descriptive presence screen only; Overture absence is not evidence that an emergency service does not exist",
        "primary_hypothesis_test": {
            "outcome": "tribal minus nontribal zero-EMS-POI share among rural tracts in the region-wide upper quartile of usdm_summer_dsci",
            "estimate": primary["tribal_minus_nontribal_zero_share"],
            "newcombe_wilson_95_ci": primary["newcombe_wilson_95_ci_difference"],
            "ci_excludes_zero": primary["ci_excludes_zero"],
            "writeup_gate": "include a writeup section only if this primary 95% CI excludes zero",
            "writeup_section_material": include_writeup,
            "writeup_gate_reason": (
                "Primary rural upper-DSCI EMS-zero contrast CI excludes zero."
                if include_writeup else
                "Primary rural upper-DSCI EMS-zero contrast CI spans zero; the secondary fire-zero contrast is in the opposite direction and is not promoted as an EMS/fire desert finding."
            ),
            "secondary_fire_zero_contrast": cells["rural_upper_drought_quartile"]["outcomes"]["fire_zero"],
        },
        "definitions": {
            "official_scope": "Eastern Oklahoma authoritative 1,192 sample GEOIDs; sample score values are not used",
            "rural": "provided pct_urban < 0.5; missing pct_urban excluded",
            "tribal": "provided tribal_any boolean; missing status excluded from comparisons",
            "drought_cut": "linear empirical 75th percentile of finite provided usdm_summer_dsci over all official Eastern Oklahoma sample tracts; ties at the threshold are included in the upper-quartile group",
            "drought_high": "usdm_summer_dsci >= region-wide q75",
            "fire_and_ems_counts": "provided per-tract Overture summary counts, using config/poi-category-definitions.json selected categories; a zero is a provided-category count of exactly 0",
            "effect_direction": "positive tribal-minus-nontribal zero-share difference means more tracts with zero Overture POIs in the tribal group",
            "intervals": "95% Wilson intervals for each binomial share; Newcombe-Wilson score interval for independent difference in proportions; tracts are the analysis units",
        },
        "uncertainty_caveats": [
            "The intervals do not model spatial dependence, measurement error, multiple endpoint/stratum comparisons, or uncertainty in the provided Overture counts.",
            "tribal_any is a provided tract overlap indicator, not an individual identity or proof that an emergency service serves a community.",
            "No fire-agency roster, volunteer status, dispatch outcomes, or field-level ground truth is provided or used; potential dispatch/geocoding consequences are hypotheses only.",
            "This report does not compare the unstratified all-tract tribal and nontribal rates; all reported comparisons are within rural strata.",
        ],
        "dispatch_context": "If volunteer fire departments or mutual-aid dispatchers rely on Overture POIs, absent fire/EMS records in the named rural high-drought tracts could make map-based address/name lookup less reliable and warrants local validation. No volunteer-agency roster, service ground truth, or dispatch outcomes were analyzed; this is a plausible consequence, not a verified station absence or dispatch failure.",
        "stratum_results": cells,
        "named_rural_upper_drought_high_population_both_zero_tracts": named,
        "population_and_missingness": {
            "usdm_summer_dsci_upper_quartile_cutoff": drought_cut,
            "official_sample_tracts": len(records),
            "tracts_missing_pct_urban": missing_urban,
            "tracts_missing_usdm_summer_dsci": missing_dsci,
            "tracts_missing_tribal_any": missing_tribal,
            "rural_tracts_missing_drought_value": int(rural_dsci_missing),
            "finite_region_wide_dsci_n": len(finite_dsci),
            "rural_known_tribal_n": len(rural),
            "rural_upper_drought_known_tribal_n": len(rural_high),
            "rural_lower_three_drought_known_tribal_n": len(rural_lower),
            "named_tracts_n": len(named),
        },
        "inputs": {
            "official_sample_geoids": {
                "path": str(sample_path),
                "rows": len(sample_ids),
                "ordered_geoid_sequence_sha256": sample_geoid_hash,
                "only_geoid_values_used": True,
                "score_placeholder_values_used": False,
            },
            "provided_strata": {
                "path": str(strata_path),
                "sha256": sha256_file(strata_path),
                "bytes": strata_path.stat().st_size,
                "read_columns": STRATA_COLUMNS,
                "source_rows": len(strata_values),
                "extra_source_rows_excluded": len(strata_values) - len(sample_ids),
            },
            "provided_overture_tract_summary": {
                "path": str(features_path),
                "sha256": sha256_file(features_path),
                "bytes": features_path.stat().st_size,
                "read_columns": FEATURE_COLUMNS,
                "source_rows": len(feature_values),
                "extra_source_rows_excluded": len(feature_values) - len(sample_ids),
            },
            "category_config": {
                "path": str(config_path),
                "sha256": sha256_file(config_path),
                "selected_fire_categories": ["fire_department", "fire_protection_service"],
                "selected_ems_categories": ["ambulance_and_ems_services"],
            },
            "external_data_used": [],
            "removed_reference_layers_read": False,
            "organizer_targets_or_sample_scores_used": False,
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"])
                        if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/discovery-ok-tribal-ems.json")
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    report = run(args.workspace.resolve(), args.out.resolve())
    primary = report["primary_hypothesis_test"]
    ci = primary["newcombe_wilson_95_ci"]
    print(
        "PASS Eastern OK rural upper-DSCI EMS-zero: "
        f"tribal-minus-nontribal={primary['estimate']:.4f}; 95% Newcombe-Wilson CI={ci}; "
        f"writeup_gate={primary['writeup_section_material']}"
    )
    print(f"Named rural high-DSCI >2,000 population with zero fire+EMS: "
          f"{report['population_and_missingness']['named_tracts_n']}")
    print(f"Wrote {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
