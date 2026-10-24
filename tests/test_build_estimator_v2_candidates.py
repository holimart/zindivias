from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from build_estimator_v2_candidates import (  # noqa: E402
    ARTIFACT,
    CANDIDATE_ID,
    COLUMNS,
    COMPONENT_ARTIFACTS,
    EXPECTED_OFFICIAL_ROWS,
    EXPECTED_TEMPLATE_ROWS,
    EXPECTED_TEMPLATE_ONLY_ROWS,
    HISTORICAL_PUBLIC_RMSE,
    PREREGISTERED_CENTRED_CORRELATION,
    RETIRED_LB_CONSTANTS,
    SCORE_COLUMNS,
    anti_probe_checks,
    csv_bytes,
    exact_defined_mean,
    fmt,
    forecast,
    model_moments,
)

ARTIFACT_PATH = REPO / ARTIFACT


def component_rows() -> dict[str, dict[str, str]]:
    tables = {}
    for name, relative in COMPONENT_ARTIFACTS.items():
        with (REPO / relative).open(newline="", encoding="utf-8") as stream:
            tables[name] = {row["GEOID"]: row for row in csv.DictReader(stream)}
    return tables


def candidate_rows() -> list[dict[str, str]]:
    with ARTIFACT_PATH.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_exact_defined_mean_ignores_undefined_and_handles_none_defined() -> None:
    assert exact_defined_mean([(0.9, 0), (0.3, 1), (0.5, 1)]) == pytest.approx(0.4, abs=1e-16)
    assert exact_defined_mean([(0.9, 0), (0.1, 0)]) == 0.0
    # an undefined component's placeholder must not pull the mean toward zero
    assert exact_defined_mean([(0.0, 0), (0.3, 1), (0.5, 1)]) == pytest.approx(0.4, abs=1e-16)


def test_serialization_is_round_trip_exact_and_newline_terminated() -> None:
    value = 0.060500636123456789
    assert float(fmt(value)) == value
    payload = csv_bytes([{"GEOID": "01001", **{column: "0.1" for column in SCORE_COLUMNS}}])
    assert payload.endswith(b"\n") and b"\r\n" not in payload
    assert payload.decode().splitlines()[0] == ",".join(COLUMNS)


def test_model_moments_measure_the_frozen_composite() -> None:
    rows = [
        {"geoid": "a", "region": "r1", "transport_gap": 0.2, "building_gap": 0.01, "poi_gap": 0.3,
         "coverage_gap_score": 0.17},
        {"geoid": "b", "region": "r1", "transport_gap": 0.4, "building_gap": 0.01, "poi_gap": 0.5,
         "coverage_gap_score": 0.3033333333333333},
        {"geoid": "c", "region": "r2", "transport_gap": 0.0, "building_gap": 0.01, "poi_gap": 0.9,
         "coverage_gap_score": 0.455},
        {"geoid": "d", "region": "r2", "transport_gap": 0.1, "building_gap": 0.01, "poi_gap": 0.7,
         "coverage_gap_score": 0.27},
    ]
    moments = model_moments(rows)
    assert moments["official_rows"] == 4
    assert moments["mean_prediction"] == pytest.approx(
        (0.17 + 0.3033333333333333 + 0.455 + 0.27) / 4, abs=1e-15)
    assert moments["region_prediction_means"]["r1"] == pytest.approx(0.23666666666666666, abs=1e-15)
    assert moments["sd_prediction"] > 0 and moments["min_prediction"] == 0.17
    assert moments["max_prediction"] == pytest.approx(0.455, abs=1e-15)
    assert moments["constant_within_any_region"] is False
    single = [dict(rows[0]), dict(rows[1]), {"geoid": "e", "region": "r2", "transport_gap": 0.0,
                                             "building_gap": 0.01, "poi_gap": 0.9,
                                             "coverage_gap_score": 0.42},
              {"geoid": "f", "region": "r3", "transport_gap": 0.0, "building_gap": 0.01,
               "poi_gap": 0.9, "coverage_gap_score": 0.42}]
    assert model_moments(single)["constant_within_any_region"] is True


def test_forecast_is_anchored_monotone_and_self_consistent() -> None:
    rows = [{"geoid": f"g{i}", "region": "r" if i % 2 else "q", "transport_gap": 0.1 * i / 9,
             "building_gap": 0.01, "poi_gap": 0.2 + 0.05 * i / 9,
             "coverage_gap_score": 0.05 + 0.02 * i / 9} for i in range(10)]
    moments = model_moments(rows)
    block = forecast(moments)
    assert block["written_before_upload"] is True
    assert block["anchor"]["E_y2"] == pytest.approx(HISTORICAL_PUBLIC_RMSE["C0"] ** 2, rel=1e-15)
    assert block["anchor"]["used_as_model_parameter"] is False
    assert block["model_side_measurements"]["mu_v"] == pytest.approx(moments["mean_prediction"])
    predicted = block["sensitivity_grid"]["target_mean_1.0x_model_mean"][
        "predicted_public_rmse_by_centred_correlation"]
    keys = sorted(predicted, key=float)
    assert all(predicted[a] >= predicted[b] for a, b in zip(keys, keys[1:]))  # more signal, lower RMSE
    mu_v = moments["mean_prediction"]
    sd_v = moments["sd_prediction"]
    sd_y = math.sqrt(HISTORICAL_PUBLIC_RMSE["C0"] ** 2 - mu_v ** 2)
    assert predicted[keys[0]] == pytest.approx(math.sqrt(sd_y ** 2 + sd_v ** 2), abs=1e-12)
    assert predicted["0.500000"] == pytest.approx(
        math.sqrt(sd_y ** 2 + sd_v ** 2 - sd_y * sd_v), abs=1e-12)
    assert block["sampling_noise_guard"]["pairwise_guard_vs_P4"] > 0
    assert block["decision_rule"]["maximum_uploads_for_this_candidate"] == 1
    assert block["decision_rule"]["second_upload_to_adjust_weights"] == "forbidden"


def test_forecast_breakevens_invert_the_forecast_identity() -> None:
    rows = [{"geoid": f"g{i}", "region": "r", "transport_gap": 0.02 + 0.005 * i,
             "building_gap": 0.01, "poi_gap": 0.06 + 0.008 * i,
             "coverage_gap_score": 0.03 + 0.005 * i} for i in range(12)]
    moments = model_moments(rows)
    block = forecast(moments)
    mu_v = moments["mean_prediction"]
    sd_v = moments["sd_prediction"]
    e_y2 = HISTORICAL_PUBLIC_RMSE["C0"] ** 2
    grid = block["sensitivity_grid"]["target_mean_1.0x_model_mean"][
        "predicted_public_rmse_by_centred_correlation"]
    for benchmark, label in ((HISTORICAL_PUBLIC_RMSE["C0"], "tie_all_zero_C0"),
                             (HISTORICAL_PUBLIC_RMSE["C1"], "tie_region_constant_C1"),
                             (HISTORICAL_PUBLIC_RMSE["P4"], "beat_P4_alpha_c1")):
        rho = block["centred_correlation_breakevens_at_model_mean"][label]
        sd_y = math.sqrt(e_y2 - mu_v ** 2)
        implied = math.sqrt(max((mu_v - mu_v) ** 2 + sd_y ** 2 + sd_v ** 2 - 2 * rho * sd_y * sd_v, 0.0))
        assert implied == pytest.approx(benchmark, abs=1e-12)
        assert isinstance(grid, dict)
    assert block["centred_correlation_breakevens_at_model_mean"]["tie_region_constant_C1"] < 0


def test_anti_probe_checks_detect_retired_constants_and_constant_regions() -> None:
    clean = [{"geoid": "a", "region": "r1", "transport_gap": 0.2, "building_gap": 0.01,
              "poi_gap": 0.3, "coverage_gap_score": 0.17},
             {"geoid": "b", "region": "r1", "transport_gap": 0.4, "building_gap": 0.01,
              "poi_gap": 0.5, "coverage_gap_score": 0.303},
             {"geoid": "c", "region": "r2", "transport_gap": 0.1, "building_gap": 0.01,
              "poi_gap": 0.2, "coverage_gap_score": 0.104},
             {"geoid": "d", "region": "r2", "transport_gap": 0.5, "building_gap": 0.01,
              "poi_gap": 0.7, "coverage_gap_score": 0.4033333333333333}]
    moments = model_moments(clean)
    emitted = [row[column] for row in clean for column in SCORE_COLUMNS] + [0.0] * 4
    checks = anti_probe_checks(clean, moments, emitted)
    assert checks["no_retired_lb_constant_in_the_scored_file"] is True
    assert checks["scored_file_cells_scanned"] == len(emitted)
    assert checks["score_is_not_constant_within_any_region"] is True

    polluted = [dict(row) for row in clean]
    for row in polluted:
        row["region"] = "r1"
        row["coverage_gap_score"] = RETIRED_LB_CONSTANTS["global_constant_c_from_P6"]
    polluted_moments = model_moments(polluted)
    polluted_emitted = [row[column] for row in polluted for column in SCORE_COLUMNS]
    # also catch a retired constant smuggled in through the template-only fill
    polluted_emitted.append(RETIRED_LB_CONSTANTS["global_constant_c_from_P6"])
    flagged = anti_probe_checks(polluted, polluted_moments, polluted_emitted)
    assert flagged["no_retired_lb_constant_in_the_scored_file"] is False
    assert "global_constant_c_from_P6" in flagged["offenders"]
    assert flagged["score_is_not_constant_within_any_region"] is False


def test_frozen_artifact_matches_its_preregistered_hash_and_manifest() -> None:
    manifest = json.loads((REPO / "reports/candidates.json").read_text(encoding="utf-8"))
    cards = [row for row in manifest["candidate_set"] if row["candidate_id"] == CANDIDATE_ID]
    assert len(cards) == 1
    card = cards[0]
    audit = json.loads((REPO / "reports/estimator-v2-audit.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(ARTIFACT_PATH.read_bytes()).hexdigest()
    assert card["sha256"] == digest == audit["artifact"]["sha256"]
    assert manifest["model_only_v2_preregistration"]["sha256"] == digest
    assert card["pre_registered"] is True
    assert card["rows"] == EXPECTED_TEMPLATE_ROWS
    assert card["columns"] == COLUMNS
    assert card["upload_status"]["uploaded"] is False
    assert card["upload_status"]["max_uploads_preregistered"] == 1
    assert card["parameters"]["predicted_public_rmse"] == pytest.approx(
        manifest["model_only_v2_preregistration"]["predicted_public_rmse"], abs=1e-15)


def test_frozen_artifact_rows_reproduce_the_frozen_component_composite() -> None:
    tables = component_rows()
    official = set(tables["transport"]) & set(tables["building"]) & set(tables["poi"])
    assert len(official) == EXPECTED_OFFICIAL_ROWS
    rows = candidate_rows()
    assert len(rows) == EXPECTED_TEMPLATE_ROWS
    assert len({row["GEOID"] for row in rows}) == EXPECTED_TEMPLATE_ROWS
    filled = 0
    for row in rows:
        values = {column: float(row[column]) for column in SCORE_COLUMNS}
        assert all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values.values())
        geoid = row["GEOID"]
        if geoid not in official:
            filled += 1
            assert all(value == 0.0 for value in values.values())
            continue
        components = [
            (float(tables["transport"][geoid]["route_reference_transport_gap"]),
             int(tables["transport"][geoid]["route_reference_transport_defined"])),
            (float(tables["building"][geoid]["building_gap_constant_0_01"]),
             int(tables["building"][geoid]["building_defined"])),
            (float(tables["poi"][geoid]["v2_raw_poi_gap"]),
             int(tables["poi"][geoid]["v2_raw_poi_defined"])),
        ]
        for (gap, flag), column in zip(components, SCORE_COLUMNS[:-1]):
            assert values[column] == pytest.approx(gap, abs=0.0)
            if flag == 0:
                assert gap == 0.0
        assert values["coverage_gap_score"] == pytest.approx(exact_defined_mean(components), abs=1e-15)
    assert filled == EXPECTED_TEMPLATE_ONLY_ROWS


def test_audit_declares_gates_prohibitions_and_no_upload() -> None:
    audit = json.loads((REPO / "reports/estimator-v2-audit.json").read_text(encoding="utf-8"))
    assert audit["acceptance"]["all_passed"] is True
    assert audit["upload_status"]["upload_performed"] is False
    assert audit["upload_status"]["live_upload_attempted"] is False
    assert audit["measure_after"]["uploads_performed"] == 0
    assert audit["measure_after"]["actual_public_rmse"] is None
    assert audit["model"]["parameters_not_used"]["probe_region_means"] is None
    assert audit["model"]["parameters_not_used"]["E_qy_over_E_q_target_moment"] is None
    for key, value in audit["prohibitions"].items():
        assert value is False, key
    assert audit["dependency_closure"]["all_present"] is True
    assert audit["dependency_closure"]["region_means_are_not_a_dependency"] is True
    assert audit["anti_probe_checks"]["no_retired_lb_constant_in_the_scored_file"] is True
    assert audit["anti_probe_checks"]["scored_file_cells_scanned"] == 9794 * 4
    assert PREREGISTERED_CENTRED_CORRELATION == 0.30
