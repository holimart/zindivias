#!/usr/bin/env python3
"""Audit the defined-component masks and composite arithmetic of the frozen v2 estimator.

Validation screen only: it promotes nothing, computes no accuracy estimate, reads no
labels, no removed reference layer or equivalent, no sample-submission score value,
no leaderboard-derived constant, and performs no upload. It reads the official scored
GEOID cells, the provided ACS strata covariates needed to recompute the frozen
fixed-prior presence probability, and the already-frozen provided-feature component
artifacts declared in ``config/v2-component-mask-audit-preregistered.json``.

For every official scored tract the audit checks that each v2 component is treated as
undefined exactly where its model-defined flag is false (undefined rows carry the
documented numeric placeholder only), that every defined component lies in [0, 1] and
reproduces its frozen formula, that the POI halves and the three-component composite
average defined components only, and that the emitted candidate score matches the
recomputed defined-component mean within the registered gate.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "v2-component-mask-audit-preregistered.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

REGIONS: tuple[str, ...] = tuple(CONFIG["scope"]["official_regions_order"])
EXPECTED_ROWS = CONFIG["scope"]["expected_region_rows"]
GATES = CONFIG["gates"]
RULES = CONFIG["frozen_rules"]
PRIORS = RULES["facility_family"]["presence_prior"]
CBP_K = float(RULES["establishments"]["cbp_k"])
BUILDING_GAP_V2 = 0.01
FAMILIES = ("fire", "ems", "schools")
WRITER_FORMAT = CONFIG["numeric_precision"]["component_csv_writer_format"]
ARITH_TOL = float(GATES["max_abs_composite_arithmetic_residual"])
REDERIV_TOL = float(GATES["max_abs_rederivation_numeric_residual"])
MODULE_TOL = REDERIV_TOL


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clip01(value: float) -> float:
    return min(1.0, max(0.0, value))


def parse_float(value: str, name: str, geoid: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric {name} for {geoid}: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"non-finite {name} for {geoid}: {value!r}")
    return number


def parse_flag(value: str, name: str, geoid: str) -> int:
    try:
        flag = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid flag {name} for {geoid}: {value!r}") from exc
    if flag not in (0, 1):
        raise ValueError(f"flag {name} for {geoid} must be 0 or 1, got {flag}")
    return flag


class ArtifactTable:
    """GEOID-keyed artifact with the measured ID coverage facts for one file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: dict[str, dict[str, str]] = {}
        self.order: list[str] = []
        self.rows_read = 0
        self.duplicate_ids = 0

    def summary(self, expected_ids: list[str]) -> dict[str, Any]:
        expected = set(expected_ids)
        present = set(self.rows)
        return {
            "path": str(self.path.relative_to(ROOT)) if self.path.is_relative_to(ROOT) else str(self.path),
            "rows_read": self.rows_read,
            "unique_geoids": len(present),
            "duplicate_ids": self.duplicate_ids,
            "missing_official_ids": len(expected - present),
            "extra_non_official_ids": len(present - expected),
            "row_order_matches_official_order": self.order == list(expected_ids),
        }


def read_table(path: Path, required: list[str]) -> ArtifactTable:
    if not path.is_file():
        raise FileNotFoundError(f"missing frozen artifact {path}")
    table = ArtifactTable(path)
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        missing = [column for column in required if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path} lacks required columns {missing}")
        for row in reader:
            geoid = str(row["GEOID"])
            table.rows_read += 1
            if geoid in table.rows:
                table.duplicate_ids += 1
                raise ValueError(f"duplicate GEOID {geoid} in {path}")
            table.rows[geoid] = row
            table.order.append(geoid)
    return table


def official_scored_ids(workspace: Path) -> list[tuple[str, str]]:
    """Return the official scored (GEOID, region) pairs in official order.

    Only the GEOID column of each regional sample submission is read; the score
    placeholder columns are never accessed.
    """
    pairs: list[tuple[str, str]] = []
    template = CONFIG["frozen_inputs"]["official_samples"]["path_template"]
    for region in REGIONS:
        path = workspace / template.format(region=region)
        if not path.is_file():
            raise FileNotFoundError(f"missing official sample {path}")
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream)
            header = next(reader)
            if "GEOID" not in header:
                raise ValueError(f"{path} has no GEOID column")
            index = header.index("GEOID")
            ids = [str(row[index]) for row in reader if row]  # score placeholder cells are never read
        if len(ids) != EXPECTED_ROWS[region]:
            raise ValueError(f"{region}: {len(ids)} official IDs, expected {EXPECTED_ROWS[region]}")
        if len(set(ids)) != len(ids):
            raise ValueError(f"{region}: duplicate official GEOIDs")
        pairs.extend((geoid, region) for geoid in ids)
    if len(pairs) != CONFIG["scope"]["official_scored_rows"]:
        raise ValueError(
            f"{len(pairs)} official IDs, expected {CONFIG['scope']['official_scored_rows']}")
    return pairs


def load_strata(workspace: Path, ids: list[str]) -> dict[str, dict[str, float]]:
    import pyarrow.parquet as pq

    template = CONFIG["frozen_inputs"]["provided_strata"]["path_template"]
    columns = list(CONFIG["frozen_inputs"]["provided_strata"]["columns_read"])
    strata: dict[str, dict[str, float]] = {}
    for region in REGIONS:
        path = workspace / template.format(region=region)
        if not path.is_file():
            raise FileNotFoundError(f"missing provided strata table {path}")
        for row in pq.ParquetFile(path).read(columns=columns).to_pylist():
            geoid = str(row["GEOID"])
            if geoid in strata:
                raise ValueError(f"duplicate strata GEOID {geoid} in {path}")
            strata[geoid] = {key: (None if row[key] is None else float(row[key]))
                             for key in columns if key != "GEOID"}
    missing = [geoid for geoid in ids if geoid not in strata]
    if missing:
        raise ValueError(f"{len(missing)} official GEOIDs absent from provided strata, e.g. {missing[:3]}")
    return strata


def urbanity(row: dict[str, float]) -> float:
    value = row.get("pct_urban")
    if value is not None and math.isfinite(value):
        return clip01(value)
    population = row.get("pop_total")
    urban_pop = row.get("pop_urban")
    if population is not None and urban_pop is not None and population > 0:
        return clip01(urban_pop / population)
    return 0.5


def presence_probability(family: str, row: dict[str, float]) -> float:
    coefficients = PRIORS[family]
    population = float(row["pop_total"])
    land_area = float(row["ALAND"])
    if population < 0 or land_area < 0:
        raise ValueError(f"negative pop_total or ALAND ({population}, {land_area})")
    area_km2 = max(land_area / 1_000_000.0, 0.01)
    density_index = math.log1p((population / area_km2) / 100.0)
    logit = (float(coefficients["intercept"])
             + float(coefficients["log_density"]) * density_index
             + float(coefficients["urban_fraction"]) * urbanity(row))
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))


def rederive_transport(row: dict[str, str]) -> tuple[float, int]:
    route_km = parse_float(row["overture_route_km"], "overture_route_km", row["GEOID"])
    named_km = parse_float(row["overture_named_road_km_5070"], "overture_named_road_km_5070", row["GEOID"])
    if route_km < 0 or named_km < 0:
        raise ValueError(f"negative route length for {row['GEOID']}")
    defined = int(route_km > 0)
    if not defined:
        return 0.0, 0
    return clip01(1.0 - named_km / route_km), 1


def rederive_building(row: dict[str, str]) -> tuple[float, int]:
    count = parse_float(row["overture_building_count"], "overture_building_count", row["GEOID"])
    if count < 0:
        raise ValueError(f"negative building count for {row['GEOID']}")
    defined = int(count > 0)
    return (BUILDING_GAP_V2 if defined else 0.0), defined


def rederive_establishments(row: dict[str, str], strata: dict[str, float]) -> tuple[float, int]:
    poi = parse_float(row["all_overture_poi_count"], "all_overture_poi_count", row["GEOID"])
    population = float(strata["pop_total"])
    if poi < 0 or population < 0:
        raise ValueError(f"negative POI count or population for {row['GEOID']}")
    defined = int(population > 0 or poi > 0)
    if not defined or population == 0:
        return 0.0, defined
    return clip01(1.0 - poi / (CBP_K * population)), defined


def rederive_family(family: str, row: dict[str, str], strata: dict[str, float]
                    ) -> tuple[float, int, float, bool]:
    """Return gap, defined, presence probability, and whether the count is exactly zero."""
    probability = presence_probability(family, strata)
    raw_count = parse_float(row[f"raw_{family}_count"], f"raw_{family}_count", row["GEOID"])
    unclassified = parse_float(row["unclassified_category_count"],
                               "unclassified_category_count", row["GEOID"])
    observed = raw_count + unclassified
    if probability == 0.0:
        return 0.0, 0, probability, raw_count == 0.0 and unclassified == 0.0
    gap = probability * clip01(1.0 - observed / (observed + 1.0))
    return gap, 1, probability, raw_count == 0.0 and unclassified == 0.0


def facility_half(gaps: list[tuple[float, int]]) -> tuple[float, int]:
    defined = [gap for gap, flag in gaps if flag]
    if not defined:
        return 0.0, 0
    return sum(defined) / len(defined), 1


def combine_poi(facility_gap: float, facility_defined: int,
                establishments_gap: float, establishments_defined: int) -> tuple[float, int]:
    halves = []
    if facility_defined:
        halves.append(float(facility_gap))
    if establishments_defined:
        halves.append(float(establishments_gap))
    if not halves:
        return 0.0, 0
    return sum(halves) / len(halves), 1


def composite_mean(components: list[tuple[float, int]]) -> tuple[float, int]:
    defined = [float(gap) for gap, flag in components if flag]
    if not defined:
        return 0.0, 0
    return sum(defined) / len(defined), 1


def at_artifact_precision(rederived: float, emitted_text: str) -> bool:
    """True when the re-derived value reproduces the artifact field exactly or bit-for-bit."""
    text = emitted_text.strip()
    if format(rederived, WRITER_FORMAT) == text:
        return True
    try:
        return float(text) == rederived
    except ValueError:
        return False


class ComponentAudit:
    """Accumulates mask, range, formula and arithmetic checks for one component."""

    def __init__(self, name: str, gap_column: str, defined_column: str) -> None:
        self.name = name
        self.gap_column = gap_column
        self.defined_column = defined_column
        self.rows = 0
        self.defined_rows = 0
        self.undefined_rows = 0
        self.undefined_nonzero_placeholder = 0
        self.defined_out_of_range = 0
        self.emitted_out_of_range = 0
        self.flag_mismatch = 0
        self.precision_exact_rows = 0
        self.precision_near_miss_rows = 0
        self.precision_floor_violations: list[str] = []
        self.formula_checked_rows = 0
        self.formula_uncheckable_rows = 0
        self.flag_source = "artifact defined column"
        self.max_abs_residual = 0.0
        self.max_abs_residual_geoid: str | None = None
        self.precision_floor = REDERIV_TOL

    def add(self, geoid: str, emitted_gap: float, emitted_flag: int,
            rederived_gap: float, rederived_flag: int, emitted_gap_text: str,
            check_formula: bool = True) -> None:
        self.rows += 1
        if emitted_flag:
            self.defined_rows += 1
            if not 0.0 <= emitted_gap <= 1.0:
                self.defined_out_of_range += 1
        else:
            self.undefined_rows += 1
            if emitted_gap != 0.0:
                self.undefined_nonzero_placeholder += 1
        if not 0.0 <= emitted_gap <= 1.0:
            self.emitted_out_of_range += 1
        if emitted_flag != rederived_flag:
            self.flag_mismatch += 1
        if not check_formula:
            self.formula_uncheckable_rows += 1
            return
        self.formula_checked_rows += 1
        residual = abs(emitted_gap - rederived_gap)
        if residual > self.max_abs_residual:
            self.max_abs_residual = residual
            self.max_abs_residual_geoid = geoid
        if at_artifact_precision(rederived_gap, emitted_gap_text):
            self.precision_exact_rows += 1
        elif residual <= self.precision_floor:
            self.precision_near_miss_rows += 1
        else:
            self.precision_floor_violations.append(geoid)

    def summary(self) -> dict[str, Any]:
        return {
            "component": self.name,
            "gap_column": self.gap_column,
            "defined_column": self.defined_column,
            "rows": self.rows,
            "defined_rows": self.defined_rows,
            "undefined_rows": self.undefined_rows,
            "defined_share": (self.defined_rows / self.rows) if self.rows else None,
            "undefined_rows_with_nonzero_placeholder": self.undefined_nonzero_placeholder,
            "defined_rows_outside_zero_one": self.defined_out_of_range,
            "emitted_rows_outside_zero_one": self.emitted_out_of_range,
            "flag_mismatch_vs_recomputation": self.flag_mismatch,
            "defined_flag_source": self.flag_source,
            "formula_rederived_rows": self.formula_checked_rows,
            "formula_rows_not_rederivable_from_artifact_inputs": self.formula_uncheckable_rows,
            "rederivation_exact_string_match_rows": self.precision_exact_rows,
            "rederivation_last_digit_near_miss_rows": self.precision_near_miss_rows,
            "rederivation_serialization_floor": self.precision_floor,
            "rederivation_within_serialization_floor": not self.precision_floor_violations,
            "rederivation_floor_violation_rows": len(self.precision_floor_violations),
            "rederivation_floor_violation_examples": self.precision_floor_violations[:5],
            "max_abs_rederivation_residual": self.max_abs_residual,
            "max_abs_rederivation_residual_geoid": self.max_abs_residual_geoid,
        }


def exact_defined_mean(components: list[tuple[float, int]]) -> tuple[float | None, int]:
    """Independent arithmetic path: exact rational mean of the defined components."""
    from fractions import Fraction

    defined = [Fraction(float(gap)) for gap, flag in components if flag]
    if not defined:
        return None, 0
    return float(sum(defined) / len(defined)), 1


def load_pipeline_functions():
    """Import the repository's own frozen combiners used to emit a v2 candidate."""
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    import estimate_gaps  # noqa: E402  (the frozen estimator module under audit)
    import estimator_v2_establishments as ev2  # noqa: E402
    import estimator_v2_building as ev2b  # noqa: E402

    return estimate_gaps, ev2, ev2b


def verify_frozen_shas(workspace: Path) -> dict[str, Any]:
    """Fail closed if any declared input differs from its preregistered SHA-256."""
    results: dict[str, Any] = {}
    for name in ("transport_component", "building_component", "poi_component"):
        declared = CONFIG["frozen_inputs"][name]
        path = ROOT / declared["path"]
        actual = sha256_file(path)
        if actual != declared["sha256"]:
            raise ValueError(f"frozen artifact changed: {declared['path']} {actual} != {declared['sha256']}")
        results[declared["path"]] = actual
    sample_template = CONFIG["frozen_inputs"]["official_samples"]["path_template"]
    sample_digests = CONFIG["frozen_inputs"]["official_samples"]["sha256"]
    for region, digest in sample_digests.items():
        path = workspace / sample_template.format(region=region)
        actual = sha256_file(path)
        if actual != digest:
            raise ValueError(f"official sample changed: {path} {actual} != {digest}")
        results[f"{sample_template.format(region=region)}"] = actual
    results["scripts/estimate_gaps.py"] = sha256_file(ROOT / "scripts/estimate_gaps.py")
    results["scripts/estimator_v2_establishments.py"] = sha256_file(
        ROOT / "scripts/estimator_v2_establishments.py")
    results["scripts/estimator_v2_building.py"] = sha256_file(ROOT / "scripts/estimator_v2_building.py")
    return results


def run_audit(workspace: Path, candidate: Path | None) -> dict[str, Any]:
    estimate_gaps, ev2, ev2b = load_pipeline_functions()
    pairs = official_scored_ids(workspace)
    ids = [geoid for geoid, _ in pairs]
    region_of = dict(pairs)
    strata = load_strata(workspace, ids)

    transport_cfg = CONFIG["frozen_inputs"]["transport_component"]
    building_cfg = CONFIG["frozen_inputs"]["building_component"]
    poi_cfg = CONFIG["frozen_inputs"]["poi_component"]
    transport = read_table(ROOT / transport_cfg["path"],
                          ["GEOID", transport_cfg["gap_column"], transport_cfg["defined_column"],
                           *transport_cfg["input_columns"]])
    building = read_table(ROOT / building_cfg["path"],
                         ["GEOID", building_cfg["gap_column"], building_cfg["defined_column"],
                          *building_cfg["input_columns"]])
    poi_required = (["GEOID", poi_cfg["gap_column"], poi_cfg["defined_column"],
                     poi_cfg["facility_half_column"], poi_cfg["establishments_half_column"],
                     *poi_cfg["family_gap_columns"], *poi_cfg["family_count_columns"],
                     *poi_cfg["input_columns"]])
    poi = read_table(ROOT / poi_cfg["path"], poi_required)
    tables = {"transport": transport, "building": building, "poi": poi}
    coverage = {name: table.summary(ids) for name, table in tables.items()}
    for name, facts in coverage.items():
        if facts["missing_official_ids"] or facts["extra_non_official_ids"]:
            raise ValueError(f"{name} artifact ID coverage mismatch: {facts}")

    candidate_column = CONFIG["scope"]["candidate_score_column"]
    candidate_table: ArtifactTable | None = None
    if candidate is not None:
        candidate_table = read_table(candidate, ["GEOID", candidate_column])
        coverage["candidate"] = candidate_table.summary(ids)
        if coverage["candidate"]["missing_official_ids"] or coverage["candidate"]["extra_non_official_ids"]:
            raise ValueError(f"candidate ID coverage mismatch: {coverage['candidate']}")

    audits = {
        "transport": ComponentAudit("transport", transport_cfg["gap_column"], transport_cfg["defined_column"]),
        "building": ComponentAudit("building", building_cfg["gap_column"], building_cfg["defined_column"]),
        "poi": ComponentAudit("poi", poi_cfg["gap_column"], poi_cfg["defined_column"]),
        "poi_facility_half": ComponentAudit("poi_facility_half", poi_cfg["facility_half_column"], "derived"),
        "poi_establishments_half": ComponentAudit(
            "poi_establishments_half", poi_cfg["establishments_half_column"], "derived"),
    }
    family_audits = {family: ComponentAudit(f"poi_{family}", f"v2_raw_{family}_gap", "derived")
                     for family in FAMILIES}
    for family in FAMILIES:
        audits[f"poi_{family}"] = family_audits[family]
    audits["poi_facility_half"].flag_source = (
        "derived: any defined fire/EMS/schools family gap (artifact carries no flag column)")
    audits["poi_establishments_half"].flag_source = (
        "derived: pop_total > 0 or all_overture_poi_count > 0 (artifact carries no flag column)")
    for family in FAMILIES:
        family_audits[family].flag_source = (
            "derived: fixed-prior presence probability > 0 (artifact carries no flag column)")

    patterns_top = defaultdict(Counter)
    patterns_halves = defaultdict(Counter)
    patterns_family = defaultdict(Counter)
    zero_defined_rows = 0
    zero_defined_geoids: list[str] = []
    max_arith_residual = 0.0
    max_arith_residual_geoid: str | None = None
    max_rederived_residual = 0.0
    max_rederived_residual_geoid: str | None = None
    max_candidate_arith_residual = 0.0
    max_candidate_rederived_residual = 0.0
    max_candidate_residual_geoid: str | None = None
    nonfinite_scores = 0
    out_of_range_scores = 0
    family_bounds_violations = 0
    family_partial_rederivation_rows = 0
    family_fully_rederived = {family: 0 for family in FAMILIES}
    composite_pipeline_mismatch = 0
    poi_half_module_mismatch = 0
    building_module_mismatch = 0
    score_moments: defaultdict[str, list[float]] = defaultdict(list)

    for geoid in ids:
        region = region_of[geoid]
        tract_strata = strata[geoid]
        t_row, b_row, p_row = transport.rows[geoid], building.rows[geoid], poi.rows[geoid]

        t_gap = parse_float(t_row[transport_cfg["gap_column"]], "transport gap", geoid)
        t_flag = parse_flag(t_row[transport_cfg["defined_column"]], "transport defined", geoid)
        t_red, t_red_flag = rederive_transport(t_row)
        audits["transport"].add(geoid, t_gap, t_flag, t_red, t_red_flag,
                               t_row[transport_cfg["gap_column"]])

        b_gap = parse_float(b_row[building_cfg["gap_column"]], "building gap", geoid)
        b_flag = parse_flag(b_row[building_cfg["defined_column"]], "building defined", geoid)
        b_red, b_red_flag = rederive_building(b_row)
        audits["building"].add(geoid, b_gap, b_flag, b_red, b_red_flag,
                              b_row[building_cfg["gap_column"]])
        if abs(b_red - ev2b.estimate_building_component_v2(
                float(b_row["overture_building_count"]))["building_gap"]) > MODULE_TOL:
            building_module_mismatch += 1

        family_pairs_emitted: list[tuple[float, int]] = []
        family_pairs_rederived: list[tuple[float, int]] = []
        partial = False
        for family in FAMILIES:
            gap_text = p_row[f"v2_raw_{family}_gap"]
            gap = parse_float(gap_text, f"v2_raw_{family}_gap", geoid)
            red_gap, red_flag, probability, zero_count = rederive_family(family, p_row, tract_strata)
            family_audits[family].add(geoid, gap, red_flag, red_gap, red_flag, gap_text,
                                      check_formula=zero_count)
            if zero_count:
                family_fully_rederived[family] += 1
            if not 0.0 <= gap <= max(1.0, probability) + 1e-15:
                family_bounds_violations += 1
            if not zero_count:
                partial = True
                family_partial_rederivation_rows += 1
            family_pairs_emitted.append((gap, red_flag))
            family_pairs_rederived.append(((red_gap if zero_count else gap), red_flag))
        patterns_family[region]["".join(str(flag) for _, flag in family_pairs_emitted)] += 1

        fac_gap = parse_float(p_row[poi_cfg["facility_half_column"]], "facility half gap", geoid)
        fac_red, fac_flag = facility_half(family_pairs_emitted)
        fac_red_all, _ = facility_half(family_pairs_rederived)
        audits["poi_facility_half"].add(geoid, fac_gap, fac_flag, fac_red, fac_flag,
                                       p_row[poi_cfg["facility_half_column"]])
        if abs(float(ev2.facility_half_v2(family_pairs_emitted)["facility_gap"]) - fac_red) > MODULE_TOL:
            poi_half_module_mismatch += 1

        est_gap = parse_float(p_row[poi_cfg["establishments_half_column"]], "establishments half gap", geoid)
        est_red, est_flag = rederive_establishments(p_row, tract_strata)
        audits["poi_establishments_half"].add(geoid, est_gap, est_flag, est_red, est_flag,
                                             p_row[poi_cfg["establishments_half_column"]])
        if abs(float(ev2.estimate_establishments_component_v2(
                float(p_row["all_overture_poi_count"]), float(tract_strata["pop_total"]),
                CBP_K)["establishments_gap"]) - est_red) > MODULE_TOL:
            poi_half_module_mismatch += 1

        poi_gap = parse_float(p_row[poi_cfg["gap_column"]], "poi gap", geoid)
        poi_flag = parse_flag(p_row[poi_cfg["defined_column"]], "poi defined", geoid)
        poi_red, poi_red_flag = combine_poi(fac_red, fac_flag, est_red, est_flag)
        _, poi_red_flag_all = combine_poi(fac_red_all, fac_flag, est_red, est_flag)
        audits["poi"].add(geoid, poi_gap, poi_flag, poi_red, poi_red_flag,
                         p_row[poi_cfg["gap_column"]])
        pipeline_poi = ev2.combine_poi_halves_v2(fac_gap, fac_flag, est_gap, est_flag)
        if int(pipeline_poi["poi_defined"]) != poi_flag or abs(
                float(pipeline_poi["poi_gap"]) - poi_gap) > MODULE_TOL:
            poi_half_module_mismatch += 1
        patterns_halves[region][f"{fac_flag}{est_flag}"] += 1

        emitted_components = [(t_gap, t_flag), (b_gap, b_flag), (poi_gap, poi_flag)]
        rederived_components = [(t_red, t_red_flag), (b_red, b_red_flag), (poi_red, poi_red_flag_all)]

        pipeline_value = estimate_gaps.composite_mean(emitted_components)
        pipeline_defined = int(pipeline_value is not None)
        pipeline_score = 0.0 if pipeline_value is None else float(pipeline_value)
        audit_score, audit_defined = composite_mean(emitted_components)
        exact_score, exact_defined = exact_defined_mean(emitted_components)
        rederived_score, _ = composite_mean(rederived_components)
        if pipeline_defined != audit_defined or audit_defined != exact_defined:
            composite_pipeline_mismatch += 1
        if exact_score is None:
            zero_defined_rows += 1
            if len(zero_defined_geoids) < 5:
                zero_defined_geoids.append(geoid)
            arith_residual = abs(pipeline_score - audit_score)
            rederived_residual = 0.0
        else:
            arith_residual = max(abs(pipeline_score - audit_score), abs(pipeline_score - exact_score))
            rederived_residual = abs(pipeline_score - rederived_score)
        if arith_residual > max_arith_residual:
            max_arith_residual = arith_residual
            max_arith_residual_geoid = geoid
        if rederived_residual > max_rederived_residual:
            max_rederived_residual = rederived_residual
            max_rederived_residual_geoid = geoid

        emitted_score = pipeline_score if pipeline_defined else 0.0
        if not math.isfinite(emitted_score):
            nonfinite_scores += 1
        if not 0.0 <= emitted_score <= 1.0:
            out_of_range_scores += 1
        score_moments[region].append(emitted_score)
        if candidate_table is not None:
            cand_score = parse_float(candidate_table.rows[geoid][candidate_column], "candidate score", geoid)
            if not math.isfinite(cand_score) or not 0.0 <= cand_score <= 1.0:
                nonfinite_scores += 1
                out_of_range_scores += 1
            max_candidate_arith_residual = max(max_candidate_arith_residual,
                                               abs(cand_score - audit_score))
            candidate_rederived_residual = abs(cand_score - rederived_score)
            if candidate_rederived_residual > max_candidate_rederived_residual:
                max_candidate_rederived_residual = candidate_rederived_residual
                max_candidate_residual_geoid = geoid
        patterns_top[region]["".join(str(flag) for _, flag in emitted_components)] += 1

    def moments(values_by_region: defaultdict[str, list[float]]) -> dict[str, Any]:
        summary = {}
        for region in REGIONS:
            values = values_by_region[region]
            total = sum(values)
            summary[region] = {
                "rows": len(values), "mean": (total / len(values)) if values else None,
                "min": min(values) if values else None, "max": max(values) if values else None,
                "share_zero": (sum(1 for value in values if value == 0.0) / len(values)) if values else None,
            }
        return summary

    component_summaries = {name: audit.summary() for name, audit in audits.items()}
    candidate_present = candidate_table is not None
    coverage_exact = all(
        facts["missing_official_ids"] == 0 and facts["extra_non_official_ids"] == 0
        and facts["duplicate_ids"] == 0 and facts["row_order_matches_official_order"]
        for facts in coverage.values())
    gates = {
        "official_id_coverage_exact": coverage_exact,
        "artifact_row_order_matches_official_order": all(
            facts["row_order_matches_official_order"] for facts in coverage.values()),
        "zero_duplicate_or_missing_ids": all(
            facts["duplicate_ids"] == 0 and facts["missing_official_ids"] == 0
            and facts["extra_non_official_ids"] == 0 for facts in coverage.values()),
        "zero_undefined_rows_with_nonzero_placeholder": all(
            summary["undefined_rows_with_nonzero_placeholder"] == 0
            for summary in component_summaries.values()),
        "zero_flag_mismatches": all(summary["flag_mismatch_vs_recomputation"] == 0
                                    for summary in component_summaries.values()),
        "all_defined_components_in_range": all(summary["emitted_rows_outside_zero_one"] == 0
                                               for summary in component_summaries.values()),
        "rederivation_within_serialization_floor": all(
            summary["rederivation_within_serialization_floor"]
            for summary in component_summaries.values()),
        "rederivation_residual_within_floor": all(
            summary["max_abs_rederivation_residual"] <= REDERIV_TOL
            for summary in component_summaries.values()),
        "composite_arithmetic_residual_within_gate": max_arith_residual <= ARITH_TOL,
        "candidate_vs_recomputed_residual_within_gate": (
            True if not candidate_present else max_candidate_rederived_residual <= REDERIV_TOL),
        "zero_defined_rows_within_gate": zero_defined_rows <= int(GATES["max_zero_defined_rows"]),
        "all_composite_scores_finite": nonfinite_scores == 0,
        "all_composite_scores_in_range": out_of_range_scores == 0,
        "frozen_module_combiners_reproduced": (poi_half_module_mismatch == 0
                                              and building_module_mismatch == 0),
        "composite_definedness_consistent_across_implementations": composite_pipeline_mismatch == 0,
        "family_gap_bounds_respected": family_bounds_violations == 0,
    }
    gates["all_passed"] = all(value for value in gates.values() if isinstance(value, bool))

    return {
        "schema_version": 1,
        "audit_id": CONFIG["audit_id"],
        "backlog_item": CONFIG["backlog_item"],
        "intent": CONFIG["intent"],
        "disposition": "validation screen; no model promotion, no accuracy claim, no upload",
        "frozen_inputs": CONFIG["frozen_inputs"],
        "frozen_rules": RULES,
        "gates": GATES,
        "input_sha256": verify_frozen_shas(workspace),
        "scope": {
            "official_scored_geoids": len(ids),
            "unique_official_geoids": len(set(ids)),
            "region_rows": {region: sum(1 for _, name in pairs if name == region) for region in REGIONS},
            "expected_region_rows": EXPECTED_ROWS,
            "id_coverage_and_order": coverage,
            "official_id_order_used_for_all_checks": True,
            "candidate_score_csv": (str(candidate) if candidate_present else None),
            "candidate_score_column": (candidate_column if candidate_present else None),
            "candidate_note": CONFIG["scope"]["candidate_note"],
        },
        "component_audits": component_summaries,
        "family_rederivation": {
            "fully_rederived_rows_per_family": family_fully_rederived,
            "rows_requiring_frozen_region_unclassified_share": family_partial_rederivation_rows,
            "note": ("A facility-family gap is re-derived from its own artifact inputs only where the tract has "
                     "zero raw family counts and zero unclassified POIs, because the frozen v2 model allocates "
                     "unclassified POIs with a region-level share that the per-tract artifact does not carry. "
                     "Every other tract still receives the presence-flag, range and gap<=presence bound checks."),
        },
        "composite_audit": {
            "rule": RULES["composite"]["rule"],
            "zero_defined_fallback_count": zero_defined_rows,
            "zero_defined_fallback_geoid_examples": zero_defined_geoids,
            "zero_defined_rule": RULES["composite"]["zero_defined_rule"],
            "zero_defined_rule_note": RULES["composite"]["zero_defined_rule_note"],
            "max_abs_composite_arithmetic_residual": max_arith_residual,
            "max_abs_composite_arithmetic_residual_geoid": max_arith_residual_geoid,
            "max_abs_composite_vs_rederived_component_mean": max_rederived_residual,
            "max_abs_composite_vs_rederived_component_mean_geoid": max_rederived_residual_geoid,
            "pipeline_vs_audit_definedness_mismatches": composite_pipeline_mismatch,
            "implementations_compared": [
                "scripts/estimate_gaps.py composite_mean (the combiner a v2 candidate is emitted with)",
                "audit-local defined-component mean",
                "audit-local exact rational defined-component mean",
                "scripts/estimator_v2_establishments.py facility_half_v2 and combine_poi_halves_v2",
                "scripts/estimator_v2_building.py estimate_building_component_v2",
            ],
            "frozen_module_combiner_mismatches": {
                "poi_halves": poi_half_module_mismatch, "building": building_module_mismatch},
            "candidate_score_residuals": ({
                "max_abs_candidate_vs_emitted_mean": max_candidate_arith_residual,
                "max_abs_candidate_vs_rederived_mean": max_candidate_rederived_residual,
                "geoid_of_max_rederived": max_candidate_residual_geoid} if candidate_present else {
                "max_abs_candidate_vs_emitted_mean": None,
                "max_abs_candidate_vs_rederived_mean": None,
                "reason": "no assembled v2 candidate CSV exists yet; rerun with --candidate after generation"}),
        },
        "defined_mask_patterns": {
            "top_level_component_order": RULES["composite"]["components"],
            "by_region": {region: {
                "rows": sum(patterns_top[region].values()),
                "top_level_defined_mask_counts": dict(sorted(patterns_top[region].items())),
                "poi_half_defined_mask_counts": dict(sorted(patterns_halves[region].items())),
                "facility_family_defined_mask_counts": dict(sorted(patterns_family[region].items())),
            } for region in REGIONS},
            "all_regions": {
                "rows": len(ids),
                "top_level_defined_mask_counts": dict(sorted(
                    Counter(sum((Counter(dict(patterns_top[region])) for region in REGIONS),
                                Counter())).items())),
                "poi_half_defined_mask_counts": dict(sorted(
                    Counter(sum((Counter(dict(patterns_halves[region])) for region in REGIONS),
                                Counter())).items())),
                "facility_family_defined_mask_counts": dict(sorted(
                    Counter(sum((Counter(dict(patterns_family[region])) for region in REGIONS),
                                Counter())).items())),
            },
        },
        "finite_range_checks": {
            "nonfinite_component_values": 0,
            "nonfinite_composite_scores": nonfinite_scores,
            "composites_outside_zero_one": out_of_range_scores,
            "family_gap_bound_violations": family_bounds_violations,
        },
        "composite_score_moments": moments(score_moments),
        "acceptance": gates,
        "prohibitions": CONFIG["prohibitions"],
    }


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except Exception as exc:  # pragma: no cover - defensive
        return f"unavailable: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                       default=os.environ.get("COMPETITION_WORKSPACE"),
                       help="competition workspace holding official samples and provided strata")
    parser.add_argument("--candidate", type=Path, default=None,
                       help="optional assembled v2 candidate CSV to score-check")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/v2-defined-mask-audit.json")
    args = parser.parse_args()
    if not args.workspace:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    workspace = Path(args.workspace).resolve()

    report = run_audit(workspace, args.candidate)
    report["reproducibility"] = {
        "command": (f"uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 "
                    f"--with shapely==2.2.0 python scripts/audit_v2_component_masks.py "
                    f"--workspace \"$COMPETITION_WORKSPACE\" --out reports/v2-defined-mask-audit.json"
                    + (f" --candidate {args.candidate}" if args.candidate else "")),
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "git_commit": git_commit(),
    }
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    report["reproducibility"]["report_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    print(f"official tracts audited: {report['scope']['official_scored_geoids']}")
    print(f"max composite arithmetic residual: "
          f"{report['composite_audit']['max_abs_composite_arithmetic_residual']:.3e}")
    print(f"max composite vs re-derived component mean residual: "
          f"{report['composite_audit']['max_abs_composite_vs_rederived_component_mean']:.3e}")
    print(f"zero-defined composite rows: {report['composite_audit']['zero_defined_fallback_count']}")
    print(f"report: {args.out} sha256 {report['reproducibility']['report_sha256']}")
    if not report["acceptance"]["all_passed"]:
        failed = sorted(key for key, value in report["acceptance"].items()
                        if isinstance(value, bool) and not value)
        print(f"FAIL v2 mask/composite audit: {failed}")
        return 1
    print("PASS v2 mask/composite audit: exact official ID coverage, masks honoured, "
          "composite arithmetic within gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
