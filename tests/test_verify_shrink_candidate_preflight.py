from __future__ import annotations

import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_shrink_candidate_preflight", ROOT / "scripts/verify_shrink_candidate_preflight.py")
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def samples():
    return {
        "maricopa-az": ["04001000100"],
        "northern-ca": ["06001000100"],
        "eastern-ok": ["40001000100"],
        "south-central-tx": ["48001000100"],
    }


def test_exact_official_artifact_ids_pass():
    official = samples()
    ids = [official[r][0] for r in mod.REGIONS]
    result = mod.check_ids(ids, official)
    assert result["official_scored_subset_exact_ordered"] is True
    assert result["whole_artifact_exactly_official_scope"] is True
    assert result["candidate_ids_outside_official_scope"] == 0
    assert result["candidate_duplicate_id_excess"] == 0
    assert result["official_ids_missing"] == 0


def test_frozen_format_only_extra_is_detected_not_silently_dropped():
    official = samples()
    ids = [official[r][0] for r in mod.REGIONS] + ["53001000100"]
    result = mod.check_ids(ids, official)
    assert result["official_scored_subset_exact_ordered"] is True
    assert result["candidate_ids_outside_official_scope"] == 1
    assert result["whole_artifact_exactly_official_scope"] is False


def test_missing_duplicate_and_order_mismatch_fail_exact_contract():
    official = samples()
    ids = [official["northern-ca"][0], official["maricopa-az"][0],
           official["eastern-ok"][0], official["eastern-ok"][0]]
    result = mod.check_ids(ids, official)
    assert result["official_ids_missing"] == 1
    assert result["candidate_duplicate_id_excess"] == 1
    assert result["official_scored_subset_exact_ordered"] is False


def test_prediction_validator_checks_only_contract_properties():
    header = ["GEOID", "coverage_gap_score", "optional_component"]
    good = mod.validate_prediction_fields(header, ["04001000100", "0.25", "1"])
    assert good["numeric_values_checked"] == 2
    assert good["missing_values"] == good["nonfinite_values"] == good["out_of_range_values"] == 0
    bad = mod.validate_prediction_fields(header, ["04001000100", "nan", "1.2"])
    assert bad["nonfinite_values"] == 1
    assert bad["out_of_range_values"] == 1
    missing = mod.validate_prediction_fields(header, ["04001000100", "", "0.2"])
    assert missing["missing_values"] == 1
    malformed = mod.validate_prediction_fields(header, ["04001000100", "0.3"])
    assert malformed["malformed_rows"] == 1
