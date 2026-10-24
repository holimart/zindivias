#!/usr/bin/env python3
"""Descriptive distribution audit of the frozen v2 candidate across provided strata.

The scored v2 candidate (``reports/candidates/V2_model.csv``) is described -- never
re-scored -- across strata that the organisers *provided* in
``defined-flags-inputs/<region>-strata-tract-table.parquet``: urban/rural, population,
tribal, SVI, CVI and hazard families. Everything here is a description of our own
predictions. No organiser target is estimated, no label, sample score placeholder,
public leaderboard value or removed reference layer is read, and nothing is uploaded.

Design rules that make the report auditable rather than decorative:

* every stratum, band edge and missing label is preregistered in
  ``config/v2-strata-descriptive-preregistered.json`` and hashed into the report;
* group membership comes only from a non-null provided value -- nulls land in one
  explicit missing bucket, never zero-filled or mean-filled;
* a value that matches no declared band aborts the run instead of being dropped;
* every stratum must partition the 9,379 official scored GEOIDs exactly once;
* group means and their max-minus-min contrast are computed twice, in float64 and in
  exact rationals over the emitted decimal text, and must agree to 1e-12;
* the report is tied to the candidate SHA, the frozen component pins, the official
  sample hashes and the provided strata hashes;
* no wall-clock value is serialized, so reruns are byte-identical.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import importlib.util
import json
import math
import os
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "v2-strata-descriptive-preregistered.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

_spec = importlib.util.spec_from_file_location(
    "v2_masks_for_strata", ROOT / "scripts/audit_v2_component_masks.py")
MASKS = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(MASKS)

MASK_CONFIG: dict[str, Any] = json.loads((ROOT / CONFIG["frozen_pins"]["mask_config"]).read_text(
    encoding="utf-8"))
REGIONS: tuple[str, ...] = tuple(MASK_CONFIG["scope"]["official_regions_order"])
EXPECTED_ROWS: dict[str, int] = dict(MASK_CONFIG["scope"]["expected_region_rows"])
STRATA_TEMPLATE: str = MASK_CONFIG["frozen_inputs"]["provided_strata"]["path_template"]
SAMPLE_TEMPLATE: str = MASK_CONFIG["frozen_inputs"]["official_samples"]["path_template"]
PERMITTED_MODEL_STRATA_COLUMNS: tuple[str, ...] = tuple(
    CONFIG["frozen_pins"].get("permitted_model_strata_columns")
    or json.loads((ROOT / CONFIG["frozen_pins"]["provenance_config"]).read_text(
        encoding="utf-8"))["allowed_strata_columns"])
STRATA_COLUMNS_USED_BY_MODEL: tuple[str, ...] = tuple(
    MASK_CONFIG["frozen_inputs"]["provided_strata"]["columns_read"])
COMPONENT_PINS: dict[str, dict[str, str]] = {
    "transport": MASK_CONFIG["frozen_inputs"]["transport_component"],
    "building": MASK_CONFIG["frozen_inputs"]["building_component"],
    "poi": MASK_CONFIG["frozen_inputs"]["poi_component"],
}
TRANSFORMS = ("population_density_per_km2",)
DEFAULT_OUT = ROOT / "reports/v2-strata-descriptive-audit.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def decimal_of(value: Any) -> Decimal:
    """Exact decimal reading of a provided numeric value, so band edges behave as written."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("bool is not a numeric strata value")
    return Decimal(repr(float(value)))


def nearest_rank(sorted_values: list[float], quantile: float) -> float | None:
    if not sorted_values:
        return None
    index = max(0, math.ceil(quantile * len(sorted_values)) - 1)
    return sorted_values[min(index, len(sorted_values) - 1)]


# --------------------------------------------------------------------------- inputs
def load_candidate(path: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]], list[str]]:
    """Read the frozen scored artifact. Text is kept verbatim for exact arithmetic."""
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        header = list(reader.fieldnames or [])
        score_column = CONFIG["candidate"]["score_column"]
        needed = [score_column] + list(CONFIG["candidate"]["component_gap_columns"].values())
        missing = [name for name in needed if name not in header]
        if missing:
            raise ValueError(f"{path} lacks candidate columns {missing}")
        rows = [dict(row) for row in reader]
    seen: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for row in rows:
        geoid = str(row["GEOID"])
        if geoid in seen:
            raise ValueError(f"duplicate GEOID {geoid} in {path}")
        seen[geoid] = row
        order.append(geoid)
    return rows, seen, order


def load_defined_flags(workspace: Path) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
    """Read the defined flags of each frozen component from its pinned artifact."""
    flags: dict[str, dict[str, int]] = {}
    evidence: dict[str, Any] = {}
    for name, pin in COMPONENT_PINS.items():
        path = ROOT / pin["path"]
        if not path.is_file():
            raise FileNotFoundError(f"missing frozen component artifact {path}")
        table = MASKS.read_table(path, [pin["defined_column"]])
        flags[name] = {geoid: MASKS.parse_flag(row[pin["defined_column"]],
                                               pin["defined_column"], geoid)
                       for geoid, row in table.rows.items()}
        observed = sha256_file(path)
        evidence[name] = {
            "path": pin["path"],
            "sha256": observed,
            "pin_sha256": pin["sha256"],
            "sha256_matches_pin": observed == pin["sha256"],
            "defined_column": pin["defined_column"],
            "rows_read": table.rows_read,
            "duplicate_ids": table.duplicate_ids,
        }
    return flags, evidence


def load_strata(workspace: Path, official: dict[str, str]) -> tuple[dict[str, dict[str, Any]],
                                                                    dict[str, Any]]:
    """Read only the declared strata columns, keyed by official GEOID."""
    columns: list[str] = ["GEOID"]
    optional_columns: set[str] = set()
    for stratum in CONFIG["strata"]:
        referenced = list(stratum.get("operands") or [stratum["column"]])
        if stratum.get("coverage_column"):
            referenced.append(stratum["coverage_column"])
        for column in referenced:
            if column not in columns:
                columns.append(column)
        if stratum.get("optional"):
            optional_columns.update(referenced)

    values: dict[str, dict[str, Any]] = {}
    per_region: dict[str, Any] = {}
    extra_rows: dict[str, list[str]] = {}
    schemas: dict[str, list[str]] = {}
    import pyarrow.parquet as pq  # imported here so the module imports without pyarrow

    for region in REGIONS:
        path = workspace / STRATA_TEMPLATE.format(region=region)
        if not path.is_file():
            raise FileNotFoundError(f"missing provided strata table {path}")
        handle = pq.ParquetFile(path)
        schema_columns = list(handle.schema_arrow.names)
        schemas[region] = schema_columns
        absent = [column for column in columns if column not in schema_columns]
        unexpected_absent = [column for column in absent if column not in optional_columns]
        if unexpected_absent:
            raise ValueError(f"{path} lacks declared strata columns {unexpected_absent}")
        projected = [column for column in columns if column in schema_columns]
        table = handle.read(columns=projected).to_pylist()
        duplicates = 0
        joined = 0
        for row in table:
            geoid = str(row["GEOID"])
            if geoid not in official:
                extra_rows.setdefault(region, [])
                if len(extra_rows[region]) < 10:
                    extra_rows[region].append(geoid)
                continue
            if geoid in values:
                duplicates += 1
                continue
            values[geoid] = {key: payload for key, payload in row.items() if key != "GEOID"}
            joined += 1
        per_region[region] = {
            "path": f"$COMPETITION_WORKSPACE/{STRATA_TEMPLATE.format(region=region)}",
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "table_rows": handle.metadata.num_rows,
            "rows_read": len(table),
            "official_rows_joined": joined,
            "non_official_rows_excluded": len(table) - joined,
            "duplicate_official_rows": duplicates,
            "columns_read": projected,
            "declared_columns_absent_from_schema": absent,
            "schema_column_count": len(schema_columns),
        }
    return values, {"per_region": per_region, "columns_read": columns,
                    "non_official_row_examples": extra_rows, "schemas": schemas}


# --------------------------------------------------------------------------- grouping
def band_for(value: Decimal, bands: list[dict[str, Any]]) -> str:
    for band in bands:
        low, high = band.get("min"), band.get("max")
        if low is not None:
            lower = value > Decimal(str(low)) if band.get("lower_exclusive") else value >= Decimal(
                str(low))
            if not lower:
                continue
        if high is not None:
            upper = value < Decimal(str(high)) if band.get("upper_exclusive") else value <= Decimal(
                str(high))
            if not upper:
                continue
        return band["label"]
    raise ValueError(f"provided value {value} matches no preregistered band")


def derived_value(transform: str | None, row: dict[str, Any],
                  operands: list[str]) -> tuple[Any, str | None]:
    """Return (value, reason). reason is set only for mathematically undefined operands."""
    if transform not in TRANSFORMS:
        raise ValueError(f"undeclared derived transform {transform!r}")
    population = row.get(operands[0])
    land_square_metres = row.get(operands[1])
    if population is None or land_square_metres is None:
        return None, "missing_operand"
    if float(land_square_metres) == 0.0:
        return None, "land_area_zero"
    return float(population) / (float(land_square_metres) / 1_000_000.0), None


def assign_label(stratum: dict[str, Any], row: dict[str, Any]) -> tuple[str, str]:
    """Return (group label, classification) where classification is value|missing|undefined."""
    kind = stratum["kind"]
    missing_label = stratum["missing_label"]
    if kind in ("categorical", "binary", "bands", "derived_bands"):
        if kind == "derived_bands":
            value, reason = derived_value(stratum.get("transform"), row, list(stratum["operands"]))
            if reason == "land_area_zero":
                return stratum["undefined_label"], "undefined"
            if reason == "missing_operand":
                return missing_label, "missing"
        else:
            value = row.get(stratum["column"])
        if value is None:
            if kind == "categorical" and stratum.get("null_is_a_real_category"):
                return stratum["null_label"], "value"
            return missing_label, "missing"
        if kind == "categorical":
            text = str(value).strip()
            if not text:
                return missing_label, "missing"
            return text, "value"
        if kind == "binary":
            if not isinstance(value, bool):
                raise ValueError(f"{stratum.get('stratum_id', stratum.get('column'))}: "
                                 f"{value!r} is not a provided boolean")
            return (stratum["true_label"] if value else stratum["false_label"]), "value"
        return band_for(decimal_of(value), stratum["bands"]), "value"
    raise ValueError(f"undeclared stratum kind {kind!r}")


# --------------------------------------------------------------------------- statistics
def summarize(values: list[str]) -> dict[str, Any]:
    """Float and exact-rational summaries of one group's candidate scores."""
    if not values:
        return {"n": 0}
    floats = sorted(float(text) for text in values)
    exact = [Fraction(text) for text in values]
    total = sum(exact)
    mean_exact = total / len(exact)
    return {
        "n": len(values),
        "score_mean": sum(floats) / len(floats),
        "score_mean_exact": float(mean_exact),
        "score_mean_exact_rational": str(mean_exact),
        "score_min": floats[0],
        "score_max": floats[-1],
        **{f"score_p{int(quantile * 100):02d}": nearest_rank(floats, quantile)
           for quantile in CONFIG["quantiles"]},
    }


def group_block(geoids: list[str], official: dict[str, str], scores: dict[str, str],
                gaps: dict[str, dict[str, str]], defined: dict[str, dict[str, int]],
                components: tuple[str, ...]) -> dict[str, Any]:
    block: dict[str, Any] = summarize([scores[geoid] for geoid in geoids])
    block["region_counts"] = {region: 0 for region in REGIONS}
    for geoid in geoids:
        block["region_counts"][official[geoid]] += 1
    defined_counts = [sum(defined[name][geoid] for name in components) for geoid in geoids]
    block["defined_component_mean"] = (sum(defined_counts) / len(defined_counts)) if geoids else None
    block["zero_defined_components_rows"] = sum(1 for value in defined_counts if value == 0)
    for name in components:
        members = [geoid for geoid in geoids if defined[name][geoid] == 1]
        block[f"{name}_defined_rows"] = len(members)
        block[f"{name}_undefined_rows"] = len(geoids) - len(members)
        block[f"{name}_gap_mean_over_defined"] = (
            sum(float(gaps[name][geoid]) for geoid in members) / len(members)) if members else None
    return block


def audit_stratum(stratum: dict[str, Any], official: dict[str, str], scores: dict[str, str],
                  gaps: dict[str, dict[str, str]], defined: dict[str, dict[str, int]],
                  strata: dict[str, dict[str, Any]],
                  components: tuple[str, ...]) -> dict[str, Any]:
    required = list(stratum.get("operands") or [stratum["column"]])
    absent = [column for column in required if not all(
        column in schema for schema in SCHEMAS.values())]
    if absent and stratum.get("optional"):
        return {"stratum_id": stratum["stratum_id"], "family": stratum["family"],
                "status": "not_available_in_provided_inputs", "groups": {},
                "group_count": 0, "never_imputed": True, "absent_columns": absent}
    if absent:
        raise ValueError(f"{stratum['stratum_id']}: required provided columns {absent} absent "
                         "from the provided strata schema")

    buckets: dict[str, list[str]] = {}
    classifications: dict[str, int] = {"value": 0, "missing": 0, "undefined": 0}
    for geoid in official:
        label, classification = assign_label(stratum, strata[geoid])
        buckets.setdefault(label, []).append(geoid)
        classifications[classification] += 1

    missing_labels = {stratum["missing_label"]}
    if stratum.get("undefined_label"):
        missing_labels.add(stratum["undefined_label"])
    groups: dict[str, Any] = {}
    for label, geoids in buckets.items():
        block = group_block(geoids, official, scores, gaps, defined, components)
        block["is_missing_bucket"] = label in missing_labels
        groups[label] = block

    present = {label: block for label, block in groups.items() if not block["is_missing_bucket"]}
    means_float = {label: block["score_mean"] for label, block in groups.items() if block["n"]}
    means_exact = {label: Fraction(block["score_mean_exact_rational"])
                   for label, block in groups.items() if block["n"]}
    tolerance = CONFIG["contrast_tolerance"]

    def contrast(selection: dict[str, dict[str, Any]]) -> dict[str, Any]:
        float_means = {label: block["score_mean"] for label, block in selection.items()
                       if block["n"]}
        exact_means = {label: Fraction(block["score_mean_exact_rational"])
                       for label, block in selection.items() if block["n"]}
        if len(float_means) < 2:
            return {"groups_compared": len(float_means), "max_minus_min_mean": None,
                    "reproduction_abs_error": None, "reproduced": True,
                    "high_group": None, "low_group": None}
        high = max(float_means, key=lambda key: float_means[key])
        low = min(float_means, key=lambda key: float_means[key])
        float_contrast = float_means[high] - float_means[low]
        exact_contrast = max(exact_means.values()) - min(exact_means.values())
        error = abs(float_contrast - float(exact_contrast))
        return {"groups_compared": len(float_means),
                "max_minus_min_mean": float_contrast,
                "max_minus_min_mean_exact": float(exact_contrast),
                "high_group": high, "low_group": low,
                "reproduction_abs_error": error, "reproduced": error <= tolerance}

    return {
        "stratum_id": stratum["stratum_id"],
        "family": stratum["family"],
        "status": "measured",
        "column": stratum["column"],
        "kind": stratum["kind"],
        "unit": stratum.get("unit"),
        "methodology": stratum["methodology"],
        "declared_groups": ([band["label"] for band in stratum["bands"]]
                            if stratum.get("bands") else None),
        "missing_label": stratum["missing_label"],
        "undefined_label": stratum.get("undefined_label"),
        "null_label": stratum.get("null_label"),
        "groups": groups,
        "group_count": len(groups),
        "value_rows": classifications["value"],
        "missing_rows": classifications["missing"],
        "undefined_rows": classifications["undefined"],
        "missing_share": classifications["missing"] / len(official),
        "partition_total": sum(block["n"] for block in groups.values()),
        "partition_is_exact_once": (sorted(geoid for geoids in buckets.values()
                                           for geoid in geoids) == sorted(official)),
        "contrast_all_groups": contrast(groups),
        "contrast_excluding_missing": contrast(present),
    }


# --------------------------------------------------------------------------- audits
SCHEMAS: dict[str, list[str]] = {}
CONFIG_COLUMNS_READ: set[str] = set()


def score_sanity(rows: list[dict[str, str]], official: dict[str, str]) -> dict[str, Any]:
    score_column = CONFIG["candidate"]["score_column"]
    official_values = [row[score_column] for row in rows if str(row["GEOID"]) in official]
    template_values = [row[score_column] for row in rows if str(row["GEOID"]) not in official]
    floats = [float(text) for text in official_values]
    finite = all(math.isfinite(value) for value in floats)
    in_unit = all(0.0 <= value <= 1.0 for value in floats)
    fill = CONFIG["candidate"]["template_only_fill"]
    return {
        "artifact_rows": len(rows),
        "official_rows": len(official_values),
        "non_official_template_rows": len(template_values),
        "all_official_scores_finite": finite,
        "all_official_scores_in_unit_interval": in_unit,
        "score_min": min(floats), "score_max": max(floats),
        "score_mean": sum(floats) / len(floats),
        "score_mean_exact": float(sum(Fraction(text) for text in official_values) / len(floats)),
        "distinct_official_score_values": len(set(official_values)),
        "template_only_rows_equal_documented_fill": all(
            float(text) == float(fill) for text in template_values),
        "template_only_fill_documented": fill,
    }


def definedness_tie(official: dict[str, str], defined: dict[str, dict[str, int]],
                    components: tuple[str, ...]) -> dict[str, Any]:
    patterns: dict[str, int] = {}
    for geoid in official:
        key = "".join(str(defined[name][geoid]) for name in components)
        patterns[key] = patterns.get(key, 0) + 1
    mask_audit = json.loads((ROOT / CONFIG["frozen_pins"]["mask_audit"]).read_text(
        encoding="utf-8"))
    frozen = mask_audit["defined_mask_patterns"]["all_regions"]["top_level_defined_mask_counts"]
    frozen_map = {pattern: count for pattern, count in frozen.items()}
    return {"observed_mask_counts": dict(sorted(patterns.items())),
            "frozen_mask_audit_mask_counts": dict(sorted(frozen_map.items())),
            "matches_frozen_mask_audit": patterns == frozen_map,
            "zero_defined_rows": patterns.get("0" * len(components), 0),
            "frozen_mask_audit_path": CONFIG["frozen_pins"]["mask_audit"]}


FILE_ACCESS = {"open", "open_input_file", "read_table", "read_row_group", "ParquetFile",
               "read_text", "read_bytes", "read_csv"}


def opened_literals(script: Path) -> list[str]:
    """String literals the script can hand to a file-access callable, plus path constants."""
    tree = ast.parse(script.read_text(encoding="utf-8"))
    opened: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            name = function.attr if isinstance(function, ast.Attribute) else (
                function.id if isinstance(function, ast.Name) else None)
            if name not in FILE_ACCESS:
                continue
            for argument in ast.walk(node):
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    opened.append(argument.value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "PATH" in target.id.upper():
                    for literal in ast.walk(node):
                        if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                            opened.append(literal.value)
    return opened


def forbidden_source_scan(script: Path) -> dict[str, Any]:
    """Fail closed if this audit can open a label, leaderboard, ledger, sample or removed layer."""
    scan_config = CONFIG["forbidden_source_scan"]
    prohibited = (list(scan_config["prohibited_path_tokens"])
                  + list(scan_config["prohibited_uri_tokens"]))
    sample_tokens = list(scan_config["sample_file_tokens"])
    opened = opened_literals(script)
    hits = sorted({literal for literal in opened if any(token in literal for token in prohibited)})
    sample_opens = sorted({literal for literal in opened
                           if any(token in literal for token in sample_tokens)})
    delegated = ROOT / "scripts/audit_v2_component_masks.py"
    delegated_opened = opened_literals(delegated)
    placeholder_source = scan_config["placeholder_columns_source"]
    source_path, _, source_key = placeholder_source.partition(":")
    payload = json.loads((ROOT / source_path).read_text(encoding="utf-8"))
    for part in source_key.split("."):
        payload = payload[part]
    placeholders = list(payload)
    delegated_placeholder_hits = sorted({literal for literal in delegated_opened
                                         if literal in placeholders})
    return {"scanned_script": relative(script),
            "opened_string_literals": sorted(set(opened)),
            "forbidden_opened_hits": hits,
            "forbidden_token_count": len(hits),
            "prohibited_tokens_applied": prohibited,
            "sample_files_opened_by_this_script": sample_opens,
            "candidate_columns_read_by_this_script": sorted(
                [CONFIG["candidate"]["score_column"]]
                + list(CONFIG["candidate"]["component_gap_columns"].values())),
            "candidate_columns_are_own_emissions_not_sample_placeholders": True,
            "delegated_sample_reader": {
                "module": relative(delegated),
                "function": "official_scored_ids",
                "declared_columns_read": MASK_CONFIG["frozen_inputs"]["official_samples"][
                    "columns_read"],
                "score_placeholders_read": MASK_CONFIG["frozen_inputs"]["official_samples"][
                    "score_placeholders_read"],
                "placeholder_literals_in_file_access_calls": delegated_placeholder_hits,
                "declared_placeholder_columns": placeholders,
            },
            "score_placeholder_columns_read": sample_opens}


def model_input_separation(descriptive_columns: list[str]) -> dict[str, Any]:
    """Columns beyond the permitted model subset must be absent from every model script."""
    extra = sorted(set(descriptive_columns) - set(PERMITTED_MODEL_STRATA_COLUMNS))
    provenance_config = json.loads((ROOT / CONFIG["frozen_pins"]["provenance_config"]).read_text(
        encoding="utf-8"))
    prohibited_families = list(provenance_config["prohibited"]["strata_column_prefixes"])
    occurrences: dict[str, dict[str, Any]] = {}
    for column in extra:
        per_script: dict[str, int] = {}
        for script in CONFIG["model_input_separation"]["model_chain_scripts"]:
            text = (ROOT / script).read_text(encoding="utf-8")
            count = text.count(column)
            if count:
                per_script[script] = count
        occurrences[column] = {"model_chain_occurrences": sum(per_script.values()),
                               "scripts": per_script,
                               "outside_permitted_model_subset": True,
                               "in_prohibited_model_input_family": any(
                                   column.lower().startswith(family.lower())
                                   for family in prohibited_families)}
    return {"descriptive_only_columns": extra,
            "permitted_model_strata_columns": sorted(PERMITTED_MODEL_STRATA_COLUMNS),
            "prohibited_model_input_column_families": prohibited_families,
            "columns_read_by_frozen_model": sorted(STRATA_COLUMNS_USED_BY_MODEL),
            "per_column": occurrences,
            "prohibited_family_columns_read_for_description_only": sorted(
                column for column, entry in occurrences.items()
                if entry["in_prohibited_model_input_family"]),
            "clean": all(entry["model_chain_occurrences"] == 0
                         for entry in occurrences.values())}


def measure_before() -> dict[str, Any]:
    prior: dict[str, Any] = {}
    for name in CONFIG["frozen_pins"]["prior_strata_reports"]:
        path = ROOT / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        prior[name] = {
            "sha256": sha256_file(path),
            "subject": payload.get("analysis") or payload.get("purpose"),
            "strata_columns_read": (payload.get("constraints", {}) or {}).get("strata_columns_read")
            or (payload.get("definitions", {}) or {}).get("svi_field"),
        }
    return {
        "statement": ("Previous strata discovery reports analyse C2/C3-era predictors and "
                      "provided-feature prevalence; no descriptive distribution of the frozen v2 "
                      "candidate across the same strata was recorded."),
        "prior_strata_reports": prior,
        "existing_v2_strata_descriptive_reports": sorted(
            str(path.relative_to(ROOT)) for path in ROOT.glob("reports/*v2*strata*")
            if path.name != DEFAULT_OUT.name),
        "frozen_v2_candidate_is_frozen": True,
    }


def input_inventory(workspace: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    samples: dict[str, Any] = {}
    pinned = MASK_CONFIG["frozen_inputs"]["official_samples"]["sha256"]
    for region in REGIONS:
        path = workspace / SAMPLE_TEMPLATE.format(region=region)
        observed = sha256_file(path)
        samples[region] = {"path": f"$COMPETITION_WORKSPACE/{SAMPLE_TEMPLATE.format(region=region)}",
                           "sha256": observed, "pin_sha256": pinned[region],
                           "sha256_matches_pin": observed == pinned[region],
                           "columns_read": ["GEOID"], "rows_expected": EXPECTED_ROWS[region]}
    provenance_path = ROOT / CONFIG["frozen_pins"]["provenance_manifest"]
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    manifest_hashes = {entry["path"].split("/")[-1]: entry["sha256"]
                       for entry in provenance["inputs"] if entry["kind"] == "local_provided"}
    return {"official_samples": samples,
            "component_artifacts": evidence,
            "provenance_manifest": {"path": relative(provenance_path),
                                    "sha256": sha256_file(provenance_path)},
            "provided_strata_pin_source": CONFIG["frozen_pins"]["provenance_manifest"],
            "provided_strata_pin_hashes": manifest_hashes}


# --------------------------------------------------------------------------- main
def main() -> int:
    global SCHEMAS, CONFIG_COLUMNS_READ, CONFIG, CONFIG_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--config", type=Path, default=None,
                        help="override the preregistered config (negative controls only)")
    parser.add_argument("--candidate", type=str, default=None,
                        help="override the candidate artifact (negative controls only)")
    args = parser.parse_args()
    config_path = CONFIG_PATH
    if args.config is not None:
        CONFIG_PATH = Path(args.config).resolve()
        config_path = CONFIG_PATH
        CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not args.workspace:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    workspace = Path(args.workspace).resolve()

    candidate_path = Path(args.candidate) if args.candidate else ROOT / CONFIG["candidate"]["artifact"]
    if not candidate_path.is_absolute():
        candidate_path = ROOT / candidate_path

    pairs = MASKS.official_scored_ids(workspace)
    official = {geoid: region for geoid, region in pairs}
    rows, score_rows, order = load_candidate(candidate_path)
    scores = {geoid: row[CONFIG["candidate"]["score_column"]] for geoid, row in score_rows.items()}
    gaps = {name: {geoid: row[column] for geoid, row in score_rows.items()}
            for name, column in CONFIG["candidate"]["component_gap_columns"].items()}
    missing_scores = [geoid for geoid in official if geoid not in scores]
    if missing_scores:
        raise ValueError(f"{len(missing_scores)} official GEOIDs absent from the candidate artifact")

    components = tuple(CONFIG["candidate"]["component_gap_columns"])
    defined, component_evidence = load_defined_flags(workspace)
    for name in components:
        absent = [geoid for geoid in official if geoid not in defined[name]]
        if absent:
            raise ValueError(f"{len(absent)} official GEOIDs absent from {name} defined flags")

    CONFIG_COLUMNS_READ = set()
    for stratum in CONFIG["strata"]:
        CONFIG_COLUMNS_READ.update(stratum.get("operands") or [stratum["column"]])
        if stratum.get("coverage_column"):
            CONFIG_COLUMNS_READ.add(stratum["coverage_column"])
    strata, strata_evidence = load_strata(workspace, official)
    SCHEMAS = strata_evidence["schemas"]
    absent_official = [geoid for geoid in official if geoid not in strata]
    if absent_official:
        raise ValueError(f"{len(absent_official)} official GEOIDs absent from provided strata, "
                         f"e.g. {absent_official[:3]}")

    artifact_sha = sha256_file(candidate_path)
    manifest = json.loads((ROOT / CONFIG["candidate"]["manifest"]).read_text(encoding="utf-8"))
    card = next((entry for entry in manifest["candidate_set"]
                 if entry["candidate_id"] == CONFIG["candidate"]["id"]), None)
    build_audit = json.loads((ROOT / CONFIG["candidate"]["build_audit"]).read_text(encoding="utf-8"))

    stratum_blocks = [audit_stratum(stratum, official, scores, gaps, defined, strata, components)
                      for stratum in CONFIG["strata"]]
    measured = [block for block in stratum_blocks if block["status"] == "measured"]

    families: dict[str, Any] = {}
    for block in measured:
        entry = families.setdefault(block["family"], {"strata": [], "max_contrast": 0.0,
                                                      "max_contrast_stratum": None})
        entry["strata"].append(block["stratum_id"])
        contrast = block["contrast_all_groups"].get("max_minus_min_mean")
        if contrast is not None and contrast > entry["max_contrast"]:
            entry["max_contrast"] = contrast
            entry["max_contrast_stratum"] = block["stratum_id"]

    sanity = score_sanity(rows, official)
    definedness = definedness_tie(official, defined, components)
    separation = model_input_separation(sorted(CONFIG_COLUMNS_READ))
    forbidden = forbidden_source_scan(Path(__file__))
    inventory = input_inventory(workspace, component_evidence)

    strata_hash_ties = all(
        entry["sha256"] == inventory["provided_strata_pin_hashes"].get(
            Path(entry["path"]).name)
        for entry in strata_evidence["per_region"].values())

    acceptance = {
        "candidate_artifact_sha_ties_to_manifest_and_build_audit": bool(
            card and card["sha256"] == artifact_sha
            and build_audit["artifact"]["sha256"] == artifact_sha),
        "component_artifact_pins_verified": all(
            entry["sha256_matches_pin"] for entry in component_evidence.values()),
        "official_sample_hashes_verified": all(
            entry["sha256_matches_pin"] for entry in inventory["official_samples"].values()),
        "provided_strata_hashes_verified": bool(strata_hash_ties),
        "all_official_geoids_accounted_exactly_once_per_stratum": bool(measured) and all(
            block["partition_is_exact_once"] and block["partition_total"] == len(official)
            for block in measured),
        "every_official_tract_has_one_strata_row": not absent_official and all(
            entry["duplicate_official_rows"] == 0
            for entry in strata_evidence["per_region"].values()),
        "candidate_scores_finite_and_in_unit_interval": bool(
            sanity["all_official_scores_finite"] and sanity["all_official_scores_in_unit_interval"]),
        "all_group_contrasts_reproduced_within_tolerance": bool(measured) and all(
            block["contrast_all_groups"]["reproduced"]
            and block["contrast_excluding_missing"]["reproduced"] for block in measured),
        "missing_strata_reported_never_imputed": bool(measured) and all(
            block["value_rows"] + block["missing_rows"] + block["undefined_rows"] == len(official)
            for block in measured),
        "excluded_rows_documented": bool(
            sanity["non_official_template_rows"] == CONFIG["candidate"]["template_only_rows"]
            and sanity["template_only_rows_equal_documented_fill"]
            and all(entry["non_official_rows_excluded"] == entry["rows_read"]
                    - entry["official_rows_joined"]
                    for entry in strata_evidence["per_region"].values())),
        "definedness_matches_frozen_mask_audit": bool(definedness["matches_frozen_mask_audit"]
                                                     and definedness["zero_defined_rows"] == 0),
        "descriptive_only_columns_absent_from_model_chain": bool(separation["clean"]),
        "no_forbidden_source_read": bool(
            forbidden["forbidden_token_count"] == 0
            and not forbidden["sample_files_opened_by_this_script"]
            and not forbidden["delegated_sample_reader"][
                "placeholder_literals_in_file_access_calls"]
            and forbidden["delegated_sample_reader"]["declared_columns_read"] == ["GEOID"]
            and forbidden["delegated_sample_reader"]["score_placeholders_read"] is False),
        "no_accuracy_claim": True,
        "no_upload": True,
        "deterministic_serialization": True,
    }
    declared_gates = set(CONFIG["acceptance"]) - {"all_passed"}
    acceptance["all_passed"] = bool(
        declared_gates <= set(acceptance)
        and all(acceptance[key] is True for key in declared_gates))

    report = {
        "preregistration": {
            "config_path": relative(config_path),
            "config_sha256": sha256_file(config_path),
            "audit_id": CONFIG["audit_id"],
            "backlog_item": CONFIG["backlog_item"],
            "intent": CONFIG["intent"],
            "quantiles": CONFIG["quantiles"],
            "quantile_method": CONFIG["quantile_method"],
            "contrast_tolerance": CONFIG["contrast_tolerance"],
            "arithmetic": CONFIG["arithmetic"],
            "missing_strata_policy": CONFIG["missing_strata_policy"],
            "prohibitions": CONFIG["prohibitions"],
            "strata_count": len(CONFIG["strata"]),
            "strata_ids": [stratum["stratum_id"] for stratum in CONFIG["strata"]],
        },
        "measure_before": measure_before(),
        "candidate": {
            "id": CONFIG["candidate"]["id"],
            "artifact": relative(candidate_path),
            "sha256": artifact_sha,
            "manifest_card_sha256": (card or {}).get("sha256"),
            "build_audit_sha256": build_audit["artifact"]["sha256"],
            "build_commit_of_record": build_audit["reproducibility"]["git_commit"],
            "score_column": CONFIG["candidate"]["score_column"],
            "component_gap_columns": CONFIG["candidate"]["component_gap_columns"],
            "official_scored_rows": len(official),
        },
        "inputs": {
            **inventory,
            "provided_strata": strata_evidence,
            "strata_columns_read": sorted(CONFIG_COLUMNS_READ),
        },
        "candidate_score_sanity": sanity,
        "accounting": {
            "official_geoids": len(official),
            "official_geoid_order_sha256": hashlib.sha256(
                "\n".join(f"{geoid},{region}" for geoid, region in pairs).encode()).hexdigest(),
            "artifact_rows_outside_official_scope": sanity["non_official_template_rows"],
            "strata_rows_outside_official_scope_by_region": {
                region: entry["non_official_rows_excluded"]
                for region, entry in strata_evidence["per_region"].items()},
            "strata_rows_outside_official_scope_examples": strata_evidence[
                "non_official_row_examples"],
            "strata_join": {region: entry["official_rows_joined"]
                            for region, entry in strata_evidence["per_region"].items()},
            "strata_table_rows": {region: entry["table_rows"]
                                  for region, entry in strata_evidence["per_region"].items()},
        },
        "definedness": definedness,
        "strata_family_provenance": CONFIG["strata_family_provenance"],
        "model_input_separation": separation,
        "forbidden_source_scan": forbidden,
        "families": families,
        "strata": stratum_blocks,
        "summary": {
            "strata_measured": len(measured),
            "strata_not_available": [block["stratum_id"] for block in stratum_blocks
                                     if block["status"] != "measured"],
            "groups_total": sum(block["group_count"] for block in measured),
            "missing_bucket_groups": sum(
                1 for block in measured for block_ in block["groups"].values()
                if block_["is_missing_bucket"]),
            "rows_in_missing_buckets_total": sum(block["missing_rows"] for block in measured),
            "largest_contrast": max((block["contrast_all_groups"]["max_minus_min_mean"] or 0.0
                                     for block in measured), default=None),
            "largest_contrast_stratum": max(
                measured, key=lambda block: (
                    block["contrast_all_groups"]["max_minus_min_mean"] or 0.0))["stratum_id"]
            if measured else None,
            "max_contrast_reproduction_error": max(
                (max(block["contrast_all_groups"]["reproduction_abs_error"] or 0.0,
                     block["contrast_excluding_missing"]["reproduction_abs_error"] or 0.0)
                 for block in measured), default=None),
        },
        "descriptive_only_not_an_accuracy_claim": {
            "no_organizer_target_estimated": True,
            "no_accuracy_or_rmse_value_emitted": True,
            "no_candidate_change": True,
            "upload_performed": False,
            "note": ("Group means describe how the frozen candidate's own emitted scores are "
                     "distributed. They are not estimates of any organiser coverage-gap target "
                     "and must not be read as accuracy."),
        },
        "acceptance": acceptance,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    summary = {
        "gates_all_passed": acceptance["all_passed"],
        "strata_measured": len(measured),
        "groups_total": report["summary"]["groups_total"],
        "official_geoids": len(official),
        "largest_contrast": report["summary"]["largest_contrast"],
        "largest_contrast_stratum": report["summary"]["largest_contrast_stratum"],
        "max_contrast_reproduction_error": report["summary"]["max_contrast_reproduction_error"],
        "missing_bucket_rows": report["summary"]["rows_in_missing_buckets_total"],
        "score_min": sanity["score_min"], "score_max": sanity["score_max"],
        "definedness_matches": definedness["matches_frozen_mask_audit"],
        "descriptive_only_columns_clean": separation["clean"],
    }
    print(json.dumps(summary, indent=1))
    if not acceptance["all_passed"]:
        print("FAIL v2 strata descriptive gates: "
              + ", ".join(sorted(key for key, value in acceptance.items() if value is False)))
        return 1
    print("PASS v2 strata descriptive audit: every official GEOID placed exactly once per "
          "stratum from a non-null provided value, contrasts reproduced within tolerance, "
          "missing strata reported and never imputed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
