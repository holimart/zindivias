from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from build_source_freshness_candidate import (  # noqa: E402
    ALL_CUTOFFS, CUTOFFS, MAX_ADJUSTMENT, PRIMARY_AGE_STATUSES, STALE_DAYS,
    TIMESTAMP_CODE, TIMESTAMP_STATUSES, UNDEFINED_PLACEHOLDER, VERSION_STATUSES, W_GAP, W_STALE,
    candidate_columns, classify_timestamp, classify_version, closed_set_accounting,
    dataset_classes, has_explicit_designator, ols, summarize_ages,
)
import build_source_freshness_candidate as module  # noqa: E402
from estimate_gaps import classify_building_sources, classify_poi_sources  # noqa: E402

REPORT_PATH = REPO / "reports/source-freshness-candidate.json"
CSV_PATH = REPO / "reports/candidates/source_freshness_candidate.csv"
MANIFEST_PATH = REPO / "reports/candidates/source_freshness_candidate_manifest.json"
REPORT = json.loads(REPORT_PATH.read_text(encoding="utf-8")) if REPORT_PATH.is_file() else None
MANIFEST = (json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            if MANIFEST_PATH.is_file() else None)

WORKSPACE = Path(os.environ.get("COMPETITION_WORKSPACE", "")) if os.environ.get(
    "COMPETITION_WORKSPACE") else None

INSTANT_CASES = [
    ("2026-03-23T03:45:19Z", "parsed_utc_z"),
    ("2020-03-07T00:00:00.000Z", "parsed_utc_z"),
    ("2026-08-02T00:00:00+00:00", "parsed_utc_offset"),
    ("2026-08-02T00:00:00-00:00", "parsed_utc_offset"),
    ("2026-03-23T03:45:19+02:00", "parsed_utc_offset"),
    ("2026-03-23T03:45:19", "naive_no_timezone"),
    ("2026-03-23T03:45", "naive_no_timezone"),
    ("2025-08-22T00:00:00.000", "naive_no_timezone"),
    ("2025-08-22", "naive_no_timezone"),
    (None, "missing_null"),
    ("", "missing_empty"),
    ("   ", "missing_empty"),
    (12345, "malformed"),
    ("not-a-timestamp", "malformed"),
    ("2026-13-45T99:99:99Z", "malformed"),
    ("2026-02-30T00:00:00Z", "malformed"),
]


def test_timestamp_classifier_uses_one_closed_status_per_entry() -> None:
    for value, expected in INSTANT_CASES:
        status, instant = classify_timestamp(value)
        assert status == expected, value
        assert status in TIMESTAMP_STATUSES
        if status in PRIMARY_AGE_STATUSES:
            assert instant is not None
        else:
            assert instant is None, value


def test_stated_offsets_are_converted_to_utc_and_naive_strings_never_become_instants() -> None:
    zulu = classify_timestamp("2026-01-01T00:00:00Z")
    offset = classify_timestamp("2025-12-31T22:00:00-02:00")
    assert offset[0] == "parsed_utc_offset"
    assert abs(zulu[1] - offset[1]) < 1e-6
    assert classify_timestamp("2026-01-01T00:00:00") == ("naive_no_timezone", None)
    assert classify_timestamp("2026-01-01T00:00:00+05:30")[1] == pytest.approx(
        datetime(2025, 12, 31, 18, 30, tzinfo=timezone.utc).timestamp())


def test_timezone_ambiguity_is_isolated_from_the_primary_path() -> None:
    assert set(PRIMARY_AGE_STATUSES) == {"parsed_utc_z", "parsed_utc_offset"}
    assert "naive_no_timezone" not in PRIMARY_AGE_STATUSES


@pytest.mark.skipif(REPORT is None, reason="run the builder to produce the report")
def test_primary_path_declares_that_naive_timestamps_are_not_used_as_instants() -> None:
    assert REPORT["pooled"]["naive_used_in_primary"] is False
    assert REPORT["pooled"]["primary_timestamps_without_explicit_designator"] == 0
    assert REPORT["pooled"]["closed_set_accounting"]["share"] >= 0.95
    missing = REPORT["pooled"]["missing_or_malformed_timestamp_share"]
    assert 0.0 <= missing <= 1.0
    # whatever is not an explicit instant is still an explicitly declared state, never a hole
    statuses = REPORT["pooled"]["entries_by_timestamp_status"]
    assert sum(statuses.values()) == REPORT["pooled"]["entries"]
    assert statuses["missing_empty"] == 0 and statuses["malformed"] == 0


def test_only_designated_strings_can_enter_the_primary_age_path() -> None:
    assert has_explicit_designator("2026-03-23T03:45:19Z") is True
    assert has_explicit_designator("2026-03-23T03:45:19+02:00") is True
    assert has_explicit_designator("2026-03-23T03:45:19.123-00:00") is True
    assert has_explicit_designator("2026-03-23T03:45:19") is False
    assert has_explicit_designator("2026-03-23") is False
    assert has_explicit_designator(None) is False
    assert has_explicit_designator(12) is False
    for value, status in INSTANT_CASES:
        if status in PRIMARY_AGE_STATUSES:
            assert has_explicit_designator(value), value


def test_version_classifier_status_set_is_closed_and_documented() -> None:
    cases = [("2025-02-25", "iso_date"), ("2026-08-02T00:00:00Z", "datetime_utc"),
             ("2633", "numeric"), (None, "missing_null"), ("", "missing_null"),
             ("   ", "missing_null"), (7, "other"), ("alpha", "other")]
    for value, expected in cases:
        status = classify_version(value)
        assert status == expected, value
        assert status in VERSION_STATUSES


def test_provenance_classes_reuse_the_frozen_rules_for_buildings_and_pois() -> None:
    for names in [("OpenStreetMap".casefold(),),
                  ("microsoft ml buildings".casefold(),),
                  ("microsoft ml buildings".casefold(), "openstreetmap"),
                  ("esri community maps".casefold(),),
                  ("usgs lidar".casefold(),),
                  ()]:
        expected = classify_building_sources([{"dataset": name} for name in names])["class"]
        assert dataset_classes("buildings", names) == expected, names
    for names in [("meta", "microsoft"), ("meta",), ("microsoft",), ("foursquare",), ()]:
        expected = classify_poi_sources([{"dataset": name} for name in names])["class"]
        assert dataset_classes("pois", names) == expected, names


def test_roads_dataset_classes_are_documented_and_exhaustive() -> None:
    assert dataset_classes("roads", ()) == "no_dataset"
    assert dataset_classes("roads", ("openstreetmap",)) == "osm_only"
    assert dataset_classes("roads", ("osm",)) == "osm_only"
    assert dataset_classes("roads", ("tomtom",)) == "tomtom_only"
    assert dataset_classes("roads", ("openstreetmap", "tomtom")) == "osm_and_tomtom"
    # any provider outside the declared road set makes the class mixed rather than silently kept
    assert dataset_classes("roads", ("openstreetmap", "nvt")) == "other_or_mixed"
    assert dataset_classes("roads", ("tomtom", "nvt")) == "other_or_mixed"
    assert dataset_classes("roads", ("nvt",)) == "other_or_mixed"


def synthetic_frames(n: int, theme: str, stale: np.ndarray, parsed: np.ndarray,
                     gap: np.ndarray, entries: np.ndarray, features: np.ndarray):
    return {"r": {theme: {"features": features, "entries": entries, "parsed": parsed,
                          "gap": gap, "stale": stale, "age_p50": np.zeros(n),
                          "age_p90": np.zeros(n), "age_p10": np.zeros(n)}}}


def test_candidate_is_bounded_monotone_and_undefined_free() -> None:
    n = 6
    parsed = np.array([10, 10, 10, 10, 0, 4])
    entries = np.array([10, 10, 10, 10, 0, 8])
    gap = entries - parsed
    features = np.array([5, 5, 5, 5, 0, 3])
    low = module.candidate_from_stale(synthetic_frames(
        n, "buildings", np.zeros(n, dtype=np.int64), parsed, gap, entries, features), "r",
        ("buildings",))
    high = module.candidate_from_stale(synthetic_frames(
        n, "buildings", parsed.copy(), parsed, gap, entries, features), "r", ("buildings",))
    assert np.all(low >= 0.0) and np.all(low <= MAX_ADJUSTMENT)
    assert np.all(high >= low)
    assert np.all(high <= MAX_ADJUSTMENT)
    assert high[0] == pytest.approx(MAX_ADJUSTMENT * W_STALE)
    assert low[0] == pytest.approx(0.0)
    assert high[4] == UNDEFINED_PLACEHOLDER  # no feature in the tract: undefined placeholder
    assert low[4] == UNDEFINED_PLACEHOLDER


def test_float_and_rational_arithmetic_paths_agree() -> None:
    rng = np.random.default_rng(20261025)
    n = 400
    themes = ("buildings", "roads", "pois")
    frames = {}
    for theme in themes:
        entries = rng.integers(0, 500, size=n).astype(np.int64)
        parsed = (entries * rng.uniform(0.7, 1.0, size=n)).astype(np.int64)
        stale = (parsed * rng.uniform(0.0, 1.0, size=n)).astype(np.int64)
        features = np.where(entries > 0, rng.integers(1, 200, size=n), 0).astype(np.int64)
        frames[theme] = {"features": features, "entries": entries, "parsed": parsed,
                         "gap": entries - parsed, "stale": stale,
                         "age_p50": np.zeros(n), "age_p90": np.zeros(n),
                         "age_p10": np.zeros(n)}
    float_path = module.candidate_from_stale({"r": frames}, "r", themes)
    rational_path = module.independent_candidate(frames, themes)
    assert float_path.shape == rational_path.shape
    assert float(np.max(np.abs(float_path - rational_path))) <= 1e-15
    assert np.all(float_path >= 0.0) and np.all(float_path <= MAX_ADJUSTMENT)


def test_age_summaries_use_nearest_rank_and_the_reversed_instant_mapping() -> None:
    n = 3
    epoch = 2_000_000_000.0
    tracts = np.array([0, 0, 0, 1, 1, 2], dtype=np.int32)
    seconds = np.array([epoch - 100 * 86400, epoch - 50 * 86400, epoch - 10 * 86400,
                        epoch - 400 * 86400, epoch - 200 * 86400, epoch - 1 * 86400],
                       dtype=np.float64)
    summary = summarize_ages((tracts, seconds), n, epoch)
    assert list(summary["parsed_total"]) == [3, 2, 1]
    ages_p50 = (epoch - summary["tract_p50_seconds"]) / 86400.0
    assert ages_p50[0] == pytest.approx(50.0)
    assert ages_p50[1] == pytest.approx(400.0)  # nearest rank rounds up to the oldest entry
    assert ages_p50[2] == pytest.approx(1.0)
    for cutoff in ALL_CUTOFFS:
        expected = int(np.sum((epoch - seconds) / 86400.0 <= cutoff))
        assert int(summary["vintage"][cutoff].sum()) == expected, cutoff
    # pooled entry quantiles use numpy's interpolated quantiles on the reversed instant axis
    assert summary["entry_age_days"]["p50"] == pytest.approx(75.0)
    assert summary["entry_age_days"]["min"] == pytest.approx(1.0)
    assert summary["entry_age_days"]["max"] == pytest.approx(400.0)
    empty = summarize_ages((np.zeros(0, dtype=np.int32), np.zeros(0)), 2, epoch)
    assert list(empty["parsed_total"]) == [0, 0]
    assert empty["entry_age_days"] == {}


def test_closed_set_accounting_counts_entries_and_entryless_features() -> None:
    status_count = len(TIMESTAMP_STATUSES)
    entry_status = np.zeros((2, status_count), dtype=np.int64)
    entry_status[0, TIMESTAMP_CODE["parsed_utc_z"]] = 2
    entry_status[0, TIMESTAMP_CODE["naive_no_timezone"]] = 1
    region_data = {"r": {"themes": {"buildings": {"counts": {
        "entries": np.array([3, 0], dtype=np.int64),
        "features_no_entries": np.array([0, 2], dtype=np.int64),
        "entry_status": entry_status}}}}}
    units, accounted, entries, entryless = closed_set_accounting(region_data, ("r",),
                                                                ("buildings",))
    assert (units, accounted, entries, entryless) == (5, 5, 3, 2)
    # an entry whose status is not tallied breaks the closed-set identity
    entry_status[0, TIMESTAMP_CODE["naive_no_timezone"]] = 0
    units, accounted, entries, _ = closed_set_accounting(region_data, ("r",), ("buildings",))
    assert (units, accounted, entries) == (5, 4, 3)


def test_accounting_ratio_reacts_to_unclassified_entries() -> None:
    # a hypothetical status set that failed to declare missing_null would leave entries uncounted
    declared = [status for status in TIMESTAMP_STATUSES if status != "missing_null"]
    observed = Counter({"parsed_utc_z": 90, "naive_no_timezone": 5, "missing_null": 5})
    total = sum(observed.values())
    accounted = sum(observed[status] for status in declared)
    assert accounted / total < 1.0
    assert accounted / total == pytest.approx(0.95)


def test_ols_recovers_a_known_linear_relationship() -> None:
    rng = np.random.default_rng(7)
    x = rng.normal(size=(500, 2))
    y = 0.25 + 1.5 * x[:, 0] - 0.5 * x[:, 1] + 1e-9 * rng.normal(size=500)
    result = ols(y, x)
    assert result["r_squared"] > 0.999
    assert result["unexplained_sd"] < 1e-6
    assert ols(np.zeros(50), np.ones((50, 1)))["r_squared"] is None


def test_candidate_column_layout_is_stable_and_complete() -> None:
    columns = candidate_columns(("buildings", "roads", "pois"))
    assert columns[:4] == ["GEOID", "region", "adjustment_defined", "source_freshness_adjustment"]
    assert columns[-6:] == ["entries_total", "entries_parsed_utc", "entries_naive_tz",
                            "entries_missing", "entries_malformed", "themes_present"]
    for theme in ("buildings", "roads", "pois"):
        for suffix in ("present", "features", "entries", "parsed_share", "metadata_gap_share",
                       "stale_share", "age_days_p50", "age_days_p90", "uncertainty"):
            assert f"{theme}_{suffix}" in columns
    assert len(columns) == len(set(columns))


@pytest.mark.skipif(REPORT is None, reason="run the builder to produce the report")
def test_report_gates_boundaries_and_prohibitions() -> None:
    assert REPORT["backlog_item"] == "bias-model-source-freshness-candidate"
    assert REPORT["acceptance"]["all_passed"] is True
    for name, value in REPORT["prohibitions"].items():
        assert value is False, name
    assert REPORT["preregistration"]["constants_fixed_before_seeing_data"] is True
    assert REPORT["preregistration"]["band_or_bound_changed_after_seeing_data"] is False
    assert REPORT["preregistration"]["frozen_constants"]["max_adjustment"] == MAX_ADJUSTMENT
    assert REPORT["preregistration"]["frozen_constants"]["stale_cutoff_days"] == STALE_DAYS
    assert REPORT["candidate"]["adjustment_max"] <= MAX_ADJUSTMENT
    assert REPORT["candidate"]["adjustment_min"] >= 0.0
    assert REPORT["candidate"]["independent_arithmetic_max_abs_difference"] <= 1e-12
    assert REPORT["pooled"]["closed_set_accounting"]["share"] >= 0.95
    assert REPORT["measure_after"]["bounds_and_hash_audit"][
        "adjustment_within_declared_bound"] is True
    assert REPORT["measure_after"]["bounds_and_hash_audit"]["candidate_csv_sha256"]
    assert REPORT["reference_epoch"]["external_clock_read"] is False
    assert REPORT["reproducibility"]["script_sha256"] == _sha(REPO / "scripts/build_source_freshness_candidate.py")
    assert REPORT["reproducibility"]["config_sha256"] == _sha(REPO / "config/source-freshness-preregistered.json")


@pytest.mark.skipif(REPORT is None, reason="run the builder to produce the report")
def test_metadata_coverage_and_vintage_metrics_are_reported() -> None:
    themes = list(REPORT["pooled"]["by_theme"])
    assert themes == ["buildings", "roads", "pois"]
    for theme in themes:
        block = REPORT["pooled"]["by_theme"][theme]
        assert block["entries"] > 0
        assert 0.0 <= block["parseable_share"] <= 1.0
        assert abs(sum(block["timestamp_status_shares"].values()) - 1.0) < 1e-9
        assert abs(sum(block["version_status_shares"].values()) - 1.0) < 1e-9
        assert block["entries_with_explicit_utc_time"] <= block["entries"]
        for cutoff in CUTOFFS:
            share = block["vintage_shares"][str(cutoff)]
            assert 0.0 <= share <= 1.0
        assert REPORT["measure_after"]["age_quantiles_days_by_theme_and_region"][theme]
        assert REPORT["measure_after"]["data_vintage_sensitivity"]["730"]["pearson_r_vs_primary"] \
            == pytest.approx(1.0)
    for name, variant in REPORT["sensitivity"]["stale_cutoffs"].items():
        assert variant["adopted"] is False, name
    for name, variant in REPORT["sensitivity"]["variants"].items():
        assert variant["adopted"] is (name == "primary"), name


@pytest.mark.skipif(REPORT is None, reason="run the builder to produce the report")
def test_no_primary_timestamp_lacks_an_explicit_timezone_designator() -> None:
    assert REPORT["pooled"]["primary_timestamps_without_explicit_designator"] == 0
    for region in REPORT["regions"].values():
        for theme in region["themes"].values():
            assert theme["input"]["primary_timestamps_without_explicit_designator"] == 0
            assert theme["input"]["examples_without_designator"] == []


@pytest.mark.skipif(REPORT is None or not CSV_PATH.is_file(), reason="run the builder first")
def test_declared_bound_comes_from_the_committed_config_and_holds_on_the_csv() -> None:
    config = json.loads((REPO / "config/source-freshness-preregistered.json").read_text(
        encoding="utf-8"))
    declared = float(config["adjustment"]["max_adjustment"])
    assert declared == MAX_ADJUSTMENT == 0.05
    assert REPORT["preregistration"]["frozen_constants"]["weights"] == config["adjustment"]["weights"]
    assert (REPORT["preregistration"]["frozen_constants"]["theme_weights"]
            == config["scope"]["theme_weights"])
    assert REPORT["preregistration"]["frozen_constants"]["fixed_cutoff_days"] == \
        config["timestamp_rules"]["fixed_cutoff_days"]
    with CSV_PATH.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        index = header.index("source_freshness_adjustment")
        values = [float(row[index]) for row in reader if row]
    assert max(values) <= declared
    assert min(values) >= 0.0


@pytest.mark.skipif(REPORT is None or MANIFEST is None, reason="run the builder first")
def test_candidate_csv_population_bounds_and_hashes() -> None:
    assert MANIFEST["upload_performed"] is False
    assert MANIFEST["analysis_only_not_a_submission_artifact"] is True
    assert MANIFEST["candidate_rows"] == 9379
    assert MANIFEST["candidate_csv_sha256"] == _sha(CSV_PATH)
    assert MANIFEST["frozen_bounds"]["adjustment_max"] == MAX_ADJUSTMENT
    with CSV_PATH.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        rows = list(reader)
    assert header == candidate_columns(("buildings", "roads", "pois"))
    assert len(rows) == 9379
    adjustment_index = header.index("source_freshness_adjustment")
    defined_index = header.index("adjustment_defined")
    values = np.array([float(row[adjustment_index]) for row in rows])
    defined = np.array([int(row[defined_index]) for row in rows])
    assert np.isfinite(values).all()
    assert values.min() >= 0.0 and values.max() <= MAX_ADJUSTMENT
    assert np.all(values[defined == 0] == UNDEFINED_PLACEHOLDER)
    assert defined.sum() == REPORT["candidate"]["defined_tracts"]
    # no region-mean imputation: within-region spread survives and undefined rows are explicit
    by_region: dict[str, list[float]] = {}
    region_index = header.index("region")
    for row in rows:
        by_region.setdefault(row[region_index], []).append(float(row[adjustment_index]))
    assert min(len(set(values)) for values in by_region.values()) > 1
    for theme in ("buildings", "roads", "pois"):
        index = header.index(f"{theme}_age_days_p90")
        p50_index = header.index(f"{theme}_age_days_p50")
        for row in rows:
            if row[index] and row[p50_index]:
                assert float(row[p50_index]) <= float(row[index]) + 1e-9


@pytest.mark.skipif(REPORT is None or WORKSPACE is None,
                    reason="run the builder with COMPETITION_WORKSPACE set first")
def test_candidate_official_population_matches_the_sample_submissions() -> None:
    expected = []
    for region in ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx"):
        path = WORKSPACE / "samples" / f"{region}-sample-submission.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream)
            header = next(reader)
            index = header.index("GEOID")
            expected.extend(row[index] for row in reader if row)
    with CSV_PATH.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        next(reader)
        emitted = [row[0] for row in reader if row]
    assert emitted == expected
    assert len(set(emitted)) == 9379


@pytest.mark.skipif(REPORT is None, reason="run the builder first")
def test_freshness_adjustment_is_not_collinear_with_the_frozen_source_mixture() -> None:
    beyond = REPORT["beyond_source_mixture"]
    assert beyond["adopted"] is False
    on_mixture = beyond["adjustment_on_frozen_mixture"]
    assert on_mixture["r_squared"] is not None
    assert on_mixture["r_squared"] < 0.9
    assert beyond["adjustment_on_frozen_mixture_and_theme_counts"]["r_squared"] is not None


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()
