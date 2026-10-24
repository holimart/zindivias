"""Tests for the frozen v2 candidate's descriptive distribution across provided strata."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "v2strata", REPO / "scripts/audit_v2_strata_distribution.py")
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)

CONFIG = module.CONFIG
REPORT_PATH = REPO / "reports/v2-strata-descriptive-audit.json"
REPORT = json.loads(REPORT_PATH.read_text(encoding="utf-8")) if REPORT_PATH.is_file() else None
ARTIFACT = REPO / CONFIG["candidate"]["artifact"]


def stratum_block(stratum_id: str) -> dict:
    return next(block for block in REPORT["strata"] if block["stratum_id"] == stratum_id)


def test_preregistration_leads_the_report_and_no_wall_clock_is_serialized() -> None:
    assert list(REPORT)[0] == "preregistration"
    prereg = REPORT["preregistration"]
    assert prereg["config_sha256"] == hashlib.sha256(
        (REPO / "config/v2-strata-descriptive-preregistered.json").read_bytes()).hexdigest()
    assert prereg["strata_count"] == len(CONFIG["strata"])
    assert prereg["contrast_tolerance"] == CONFIG["contrast_tolerance"]
    offenders: list[str] = []

    def walk(node: object, breadcrumb: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if any(part in str(key).lower() for part in
                       ("time", "timestamp", "elapsed", "duration", "generated", "datetime")):
                    offenders.append(f"{breadcrumb}/{key}")
                walk(child, f"{breadcrumb}/{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{breadcrumb}[{index}]")

    walk(REPORT, "")
    assert offenders == []


def test_every_required_strata_family_is_covered_and_measured() -> None:
    families = {block["family"] for block in REPORT["strata"]}
    assert {"urban_rural", "tribal", "svi", "cvi", "hazard_drought", "hazard_wildfire",
            "hazard_heat", "hazard_flood", "population"} <= families
    assert REPORT["summary"]["strata_not_available"] == []
    assert REPORT["summary"]["strata_measured"] == len(CONFIG["strata"])
    assert REPORT["summary"]["groups_total"] == sum(
        block["group_count"] for block in REPORT["strata"])


def test_every_stratum_partitions_the_official_population_exactly_once() -> None:
    assert REPORT["candidate"]["official_scored_rows"] == CONFIG["candidate"]["official_scored_rows"]
    for block in REPORT["strata"]:
        if block["status"] != "measured":
            continue
        assert block["partition_total"] == 9379, block["stratum_id"]
        assert block["partition_is_exact_once"] is True, block["stratum_id"]
        assert (block["value_rows"] + block["missing_rows"] + block["undefined_rows"]) == 9379, \
            block["stratum_id"]
        assert sum(group["n"] for group in block["groups"].values()) == 9379, block["stratum_id"]
        assert sum(sum(group["region_counts"].values())
                   for group in block["groups"].values()) == 9379, block["stratum_id"]


def test_every_group_summary_is_internally_consistent() -> None:
    for block in REPORT["strata"]:
        for label, group in block["groups"].items():
            assert group["n"] > 0, (block["stratum_id"], label)
            assert 0.0 <= group["score_min"] <= group["score_max"] <= 1.0
            assert group["score_p05"] <= group["score_p25"] <= group["score_p50"] \
                <= group["score_p75"] <= group["score_p95"]
            assert group["score_mean_exact"] == pytest.approx(group["score_mean"], abs=1e-15)
            assert 1.0 <= group["defined_component_mean"] <= 3.0
            assert group["zero_defined_components_rows"] == 0
            for component in CONFIG["candidate"]["component_gap_columns"]:
                assert group[f"{component}_defined_rows"] \
                    + group[f"{component}_undefined_rows"] == group["n"], (block["stratum_id"],
                                                                            label, component)


def test_group_contrasts_are_reproduced_in_exact_arithmetic_within_tolerance() -> None:
    tolerance = CONFIG["contrast_tolerance"]
    worst = 0.0
    for block in REPORT["strata"]:
        for key in ("contrast_all_groups", "contrast_excluding_missing"):
            contrast = block[key]
            assert contrast["groups_compared"] >= 2, (block["stratum_id"], key)
            assert contrast["reproduced"] is True, (block["stratum_id"], key)
            assert contrast["reproduction_abs_error"] <= tolerance, (block["stratum_id"], key)
            worst = max(worst, contrast["reproduction_abs_error"])
            assert contrast["max_minus_min_mean"] == pytest.approx(
                contrast["max_minus_min_mean_exact"], abs=tolerance)
    assert worst <= tolerance
    assert REPORT["summary"]["max_contrast_reproduction_error"] <= tolerance


def test_missing_strata_get_an_explicit_bucket_and_are_never_imputed() -> None:
    for block in REPORT["strata"]:
        missing = [label for label, group in block["groups"].items() if group["is_missing_bucket"]]
        if block["missing_rows"]:
            assert block["missing_label"] in missing, block["stratum_id"]
            assert block["groups"][block["missing_label"]]["n"] == block["missing_rows"]
        if block["undefined_label"]:
            assert block["groups"][block["undefined_label"]]["n"] == block["undefined_rows"]
    heavy = stratum_block("hazard_heat.uhe_wbgtmax28_days_band")
    assert heavy["missing_rows"] == 3599
    assert heavy["missing_share"] == pytest.approx(3599 / 9379)
    undefined = stratum_block("population.density_band")
    assert undefined["undefined_rows"] == 2
    assert undefined["groups"][undefined["undefined_label"]]["defined_component_mean"] == 1.0


def test_missing_bucket_can_dominate_a_contrast_so_both_contrasts_are_required() -> None:
    flood = stratum_block("hazard_flood.carbonplan_rps_2011_band")
    assert flood["contrast_all_groups"]["max_minus_min_mean"] > \
        flood["contrast_excluding_missing"]["max_minus_min_mean"]
    assert flood["contrast_all_groups"]["high_group"] == flood["missing_label"]
    assert flood["contrast_excluding_missing"]["high_group"] == "0.0"


def test_candidate_scores_are_finite_in_unit_interval_and_template_rows_are_documented() -> None:
    sanity = REPORT["candidate_score_sanity"]
    assert sanity["artifact_rows"] == 9794
    assert sanity["official_rows"] == 9379
    assert sanity["non_official_template_rows"] == CONFIG["candidate"]["template_only_rows"]
    assert sanity["all_official_scores_finite"] is True
    assert sanity["all_official_scores_in_unit_interval"] is True
    assert sanity["template_only_rows_equal_documented_fill"] is True
    assert 0.0 <= sanity["score_min"] and sanity["score_max"] <= 1.0
    # independent re-read of the artifact
    with ARTIFACT.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    score = CONFIG["candidate"]["score_column"]
    assert len(rows) == 9794
    assert all(0.0 <= float(row[score]) <= 1.0 for row in rows)


def test_out_of_scope_rows_are_excluded_and_documented_not_silently_dropped() -> None:
    accounting = REPORT["accounting"]
    assert accounting["official_geoids"] == 9379
    assert accounting["artifact_rows_outside_official_scope"] == 415
    assert sum(accounting["strata_rows_outside_official_scope_by_region"].values()) == 7
    assert accounting["strata_rows_outside_official_scope_by_region"]["south-central-tx"] == 7
    assert len(accounting["strata_rows_outside_official_scope_examples"]["south-central-tx"]) == 7
    assert sum(accounting["strata_join"].values()) == 9379
    for region, entry in REPORT["inputs"]["provided_strata"]["per_region"].items():
        assert entry["duplicate_official_rows"] == 0, region
        assert entry["official_rows_joined"] + entry["non_official_rows_excluded"] \
            == entry["rows_read"], region


def test_inputs_tie_to_the_candidate_build_and_official_hashes() -> None:
    inputs = REPORT["inputs"]
    assert inputs["component_artifacts"]["transport"]["sha256_matches_pin"] is True
    assert inputs["component_artifacts"]["building"]["sha256_matches_pin"] is True
    assert inputs["component_artifacts"]["poi"]["sha256_matches_pin"] is True
    for region, entry in inputs["official_samples"].items():
        assert entry["sha256_matches_pin"] is True, region
        assert entry["columns_read"] == ["GEOID"], region
    for region, entry in inputs["provided_strata"]["per_region"].items():
        name = Path(entry["path"]).name
        assert entry["sha256"] == inputs["provided_strata_pin_hashes"][name], region
    assert REPORT["candidate"]["sha256"] == REPORT["candidate"]["manifest_card_sha256"] \
        == REPORT["candidate"]["build_audit_sha256"]
    assert len(REPORT["candidate"]["build_commit_of_record"]) == 40


def test_definedness_matches_the_frozen_component_mask_audit() -> None:
    definedness = REPORT["definedness"]
    assert definedness["matches_frozen_mask_audit"] is True
    assert definedness["zero_defined_rows"] == 0
    assert definedness["observed_mask_counts"] == {"001": 2, "011": 2642, "111": 6735}


def test_descriptive_only_columns_never_appear_in_the_model_chain() -> None:
    separation = REPORT["model_input_separation"]
    assert separation["clean"] is True
    assert len(separation["descriptive_only_columns"]) == 27
    assert all(entry["model_chain_occurrences"] == 0
               for entry in separation["per_column"].values())
    assert set(separation["permitted_model_strata_columns"]) == set(
        module.PERMITTED_MODEL_STRATA_COLUMNS)
    assert set(separation["descriptive_only_columns"]).isdisjoint(
        separation["permitted_model_strata_columns"])
    assert "svi_overall" in separation["prohibited_family_columns_read_for_description_only"]
    assert "cvi_overall" in separation["prohibited_family_columns_read_for_description_only"]
    # the descriptive audit may read them; the scored model chain may not
    for column in ("svi_overall", "cvi_overall", "usfs_FLEP8_mean", "carbonplan_rps_2011_mean"):
        assert separation["per_column"][column]["model_chain_occurrences"] == 0
        assert separation["per_column"][column]["in_prohibited_model_input_family"] is True


def test_no_label_leaderboard_ledger_sample_or_removed_layer_is_opened() -> None:
    scan = REPORT["forbidden_source_scan"]
    assert scan["forbidden_token_count"] == 0
    assert scan["forbidden_opened_hits"] == []
    assert scan["sample_files_opened_by_this_script"] == []
    reader = scan["delegated_sample_reader"]
    assert reader["declared_columns_read"] == ["GEOID"]
    assert reader["score_placeholders_read"] is False
    assert reader["placeholder_literals_in_file_access_calls"] == []
    assert scan["candidate_columns_read_by_this_script"] == [
        "building_gap", "coverage_gap_score", "poi_gap", "transport_gap"]


@pytest.mark.skipif(REPORT is None, reason="run the audit to produce the report")
def test_all_acceptance_gates_pass() -> None:
    acceptance = REPORT["acceptance"]
    assert acceptance["all_passed"] is True
    assert set(CONFIG["acceptance"]) - {"all_passed"} <= set(acceptance)
    for key, expected in CONFIG["acceptance"].items():
        if key == "all_passed":
            continue
        assert acceptance[key] is expected, key


# --------------------------------------------------------------------- pure unit tests
def test_band_assignment_uses_the_declared_boundaries_exactly() -> None:
    bands = [{"label": "0.0", "min": 0.0, "max": 0.0},
             {"label": "(0.0,0.2]", "min": 0.0, "max": 0.2, "lower_exclusive": True},
             {"label": ">0.2", "min": 0.2, "max": None, "lower_exclusive": True}]
    assert module.band_for(Decimal("0.0"), bands) == "0.0"
    assert module.band_for(Decimal("1e-9"), bands) == "(0.0,0.2]"
    assert module.band_for(Decimal("0.2"), bands) == "(0.0,0.2]"
    assert module.band_for(Decimal("0.20000000000000001"), bands) == ">0.2"
    with pytest.raises(ValueError, match="matches no preregistered band"):
        module.band_for(Decimal("-1"), bands)


def test_stratum_kinds_are_a_closed_set_and_fail_closed() -> None:
    with pytest.raises(ValueError, match="undeclared stratum kind"):
        module.assign_label({"kind": "vibes", "missing_label": "m", "column": "x"}, {"x": 1})
    with pytest.raises(ValueError, match="undeclared derived transform"):
        module.derived_value("population_per_parsec", {"a": 1, "b": 2}, ["a", "b"])
    with pytest.raises(ValueError, match="not a provided boolean"):
        module.assign_label({"kind": "binary", "missing_label": "m", "column": "x",
                             "true_label": "t", "false_label": "f"}, {"x": 1})


def test_categorical_and_binary_missing_values_land_in_the_missing_bucket() -> None:
    categorical = {"kind": "categorical", "column": "ur_class",
                   "missing_label": "class_missing_not_imputed"}
    assert module.assign_label(categorical, {"ur_class": None}) == (
        "class_missing_not_imputed", "missing")
    assert module.assign_label(categorical, {"ur_class": "  Rural  "}) == ("Rural", "value")
    assert module.assign_label(categorical, {"ur_class": ""}) == (
        "class_missing_not_imputed", "missing")
    with_null_category = dict(categorical, null_is_a_real_category=True,
                              null_label="no_aiannh_intersection_recorded")
    assert module.assign_label(with_null_category, {"aiannh_kind": None}) == (
        "no_aiannh_intersection_recorded", "value")
    binary = {"kind": "binary", "column": "tribal_any", "true_label": "tribal",
              "false_label": "non_tribal", "missing_label": "m"}
    assert module.assign_label(binary, {"tribal_any": True}) == ("tribal", "value")
    assert module.assign_label(binary, {"tribal_any": False}) == ("non_tribal", "value")
    assert module.assign_label(binary, {"tribal_any": None}) == ("m", "missing")


def test_derived_density_separates_missing_inputs_from_undefined_land_area() -> None:
    stratum = {"kind": "derived_bands", "transform": "population_density_per_km2",
               "operands": ["pop_total", "ALAND"], "missing_label": "inputs_missing",
               "undefined_label": "land_area_zero",
               "bands": [{"label": "[0,1)", "min": 0, "max": 1, "upper_exclusive": True},
                         {"label": ">=1", "min": 1, "max": None}]}
    # 3 people over 4 km2 is 0.75 per km2; 5000 over 4 km2 is 1250 per km2
    assert module.assign_label(stratum, {"pop_total": 3, "ALAND": 4_000_000}) == (
        "[0,1)", "value")
    assert module.assign_label(stratum, {"pop_total": 5000, "ALAND": 4_000_000}) == (">=1", "value")
    assert module.assign_label(stratum, {"pop_total": None, "ALAND": 4_000_000}) == (
        "inputs_missing", "missing")
    assert module.assign_label(stratum, {"pop_total": 500, "ALAND": None}) == (
        "inputs_missing", "missing")
    assert module.assign_label(stratum, {"pop_total": 500, "ALAND": 0}) == (
        "land_area_zero", "undefined")


def test_nearest_rank_quantiles_follow_the_documented_rule() -> None:
    values = [float(value) for value in range(1, 11)]
    assert module.nearest_rank(values, 0.05) == 1.0
    assert module.nearest_rank(values, 0.5) == 5.0
    assert module.nearest_rank(values, 0.95) == 10.0
    assert module.nearest_rank([], 0.5) is None


def test_summarize_reports_float_and_exact_means_of_the_same_values() -> None:
    texts = ["0.1", "0.2", "0.3"]
    summary = module.summarize(texts)
    assert summary["n"] == 3
    assert summary["score_mean_exact_rational"] == str(Fraction("0.2"))
    assert summary["score_mean"] == pytest.approx(0.2, abs=1e-16)
    assert summary["score_min"] == 0.1 and summary["score_max"] == 0.3
    assert module.summarize([]) == {"n": 0}


def test_opened_literals_ignores_prose_and_catches_file_arguments(tmp_path: Path) -> None:
    script = tmp_path / "probe.py"
    script.write_text(
        'CONFIG_PATH = ROOT / "config/thing.json"\n'
        'NOTE = "data/ledger/standings.md is deliberately never read here"\n'
        'pq.ParquetFile("/tmp/reports/submissions.jsonl")\n', encoding="utf-8")
    opened = module.opened_literals(script)
    assert "config/thing.json" in opened
    assert "/tmp/reports/submissions.jsonl" in opened
    assert "data/ledger/standings.md is deliberately never read here" not in opened


def test_official_population_and_regions_match_the_frozen_scope() -> None:
    workspace = Path(os.environ.get("COMPETITION_WORKSPACE", ""))
    if not workspace.is_dir():
        pytest.skip("COMPETITION_WORKSPACE is not set")
    pairs = module.MASKS.official_scored_ids(workspace)
    assert len(pairs) == 9379
    assert {region for _, region in pairs} == set(module.REGIONS)
    assert REPORT["accounting"]["official_geoid_order_sha256"] == hashlib.sha256(
        "\n".join(f"{geoid},{region}" for geoid, region in pairs).encode()).hexdigest()
