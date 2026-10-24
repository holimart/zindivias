"""Tests for the v2 scored-candidate input provenance audit."""
from __future__ import annotations

import ast
import csv
import hashlib
import importlib.util
import json
import os
from fractions import Fraction
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "v2provenance", REPO / "scripts/audit_v2_input_provenance.py")
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)

CONFIG = module.CONFIG
REPORT_PATH = REPO / "reports/v2-input-provenance.json"
MANIFEST_PATH = REPO / "reports/candidates/v2_input_provenance_manifest.json"
ARTIFACT = REPO / CONFIG["candidate"]["artifact"]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


REPORT = load(REPORT_PATH) if REPORT_PATH.is_file() else None
MANIFEST = load(MANIFEST_PATH) if MANIFEST_PATH.is_file() else None


@pytest.mark.skipif(REPORT is None, reason="run the audit to produce the report")
def test_preregistration_is_declared_before_results_and_carries_no_timestamps() -> None:
    keys = list(REPORT)
    assert keys and keys[0] == "preregistration"
    forbidden_key_parts = ("time", "timestamp", "elapsed", "duration", "generated", "datetime")
    offenders: list[str] = []

    def walk(node: object, breadcrumb: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                lowered = str(key).lower()
                if any(part in lowered for part in forbidden_key_parts):
                    offenders.append(f"{breadcrumb}/{key}")
                walk(child, f"{breadcrumb}/{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{breadcrumb}[{index}]")

    walk(REPORT, "")
    assert offenders == []


def test_provenance_classes_are_a_closed_set_and_no_field_is_unclassified() -> None:
    classes = set(CONFIG["provenance_classes"])
    entries = REPORT["field_lineage"]["entries"]
    assert entries
    assert all(entry["provenance_class"] in classes for entry in entries)
    assert REPORT["field_lineage"]["unclassified_fields"] == []
    assert REPORT["field_lineage"]["unverified_fields"] == []
    assert REPORT["field_lineage"]["fraction_fields_with_manifest_entry"] == 1.0
    assert all(entry["manifest_entry"] for entry in entries)
    assert all(entry["basis"] for entry in entries), "every field needs a documented basis"
    assert all(entry["source"] and entry["column"] for entry in entries)


def test_every_field_layer_is_present_and_lineage_reaches_provided_data() -> None:
    counts = REPORT["field_lineage"]["layer_counts"]
    assert set(counts) == {"1", "2", "3", "4"}
    classes = REPORT["field_lineage"]["provenance_class_counts"]
    assert classes["official_challenge_provided_local"] > 0
    assert classes["official_challenge_provided_hosted"] > 0
    assert classes["documented_public_methodology"] > 0
    assert classes["derived_from_allowed_inputs"] > 0
    layer_three = [entry for entry in REPORT["field_lineage"]["entries"] if entry["layer"] == 3]
    assert all(entry["provenance_class"] in (
        "official_challenge_provided_local", "official_challenge_provided_hosted",
        "derived_from_allowed_inputs", "reconciliation_control_only") for entry in layer_three)


def test_provided_local_copies_are_byte_identical_to_the_organiser_objects() -> None:
    entries = REPORT["inputs"]["provided_local_copies"]
    assert len(entries) == 9  # 4 samples, 4 strata tables, platform template
    for key, entry in entries.items():
        assert entry["sha256"], key
        assert entry["hosted_reject_reason"] is None, key
        if entry.get("hosted_uri"):
            assert entry["hosted_allowed"] is True, key
            assert entry["byte_identical_to_hosted"] is True, key
            assert entry["hosted_sha256"] == entry["sha256"], key
        else:
            assert entry["hosted_allowed"] is None, key
            assert entry["downloader_record"]["sha256_matches"] is True, key


def test_sample_and_template_placeholder_columns_are_never_read() -> None:
    entries = REPORT["inputs"]["provided_local_copies"]
    for key, entry in entries.items():
        assert entry["placeholder_columns_read"] == [], key
        for name in entry.get("columns_read") or []:
            assert name not in CONFIG["prohibited"]["score_placeholder_columns_in_provided_samples"]
    template = entries["SampleSubmission.csv"]
    assert template["columns_read"] == ["GEOID", "region"]
    assert template["downloader_record"]["sha256_matches"] is True
    assert template["downloader_record"]["source_url"].startswith("https://api.zindi.world/")


def test_strata_reads_stay_inside_the_permitted_provided_subset() -> None:
    for key, entry in REPORT["inputs"]["provided_local_copies"].items():
        if not key.startswith("defined-flags-inputs/"):
            continue
        assert entry["columns_outside_allowed_subset"] == [], key
        assert entry["columns_in_removed_reference_families"] == [], key
        assert entry["value_mismatches_vs_provided"] == 0, key
        assert entry["rows_compared_against_provided"] > 0, key
        assert entry["hosted_missing_requested_columns"] == [], key
    assert REPORT["code_dependency_scan"]["strata_projection_violations"] == []


def test_hosted_objects_are_fingerprinted_and_carry_the_requested_columns() -> None:
    objects = REPORT["inputs"]["hosted_objects"]
    assert len(objects) == 16  # four regions x four provided layers
    for key, entry in objects.items():
        assert entry["hosted_allowed"] is True, key
        assert entry["wanted_columns_missing"] == [], key
        assert entry["footer_fingerprint_sha256"], key
        assert entry["rows"] > 0, key
        assert f"s3://{module.BUCKET}/" == entry["uri"][:len(f"s3://{module.BUCKET}/")]


def test_hosted_allowlist_rejects_removed_reference_layers_and_foreign_buckets() -> None:
    allowed, _ = module.hosted_allowed(
        f"{module.BUCKET}/reference/maricopa-az/maricopa-az-overture-roads.parquet")
    assert allowed is True
    for uri in (
            f"{module.BUCKET}/reference/maricopa-az/maricopa-az-tiger-roads.parquet",
            f"{module.BUCKET}/reference/maricopa-az/maricopa-az-ms-buildings.parquet",
            f"{module.BUCKET}/reference/maricopa-az/maricopa-az-usgs-facilities.parquet",
            f"{module.BUCKET}/reference/maricopa-az/maricopa-az-cbp-establishments.parquet",
            f"{module.BUCKET}/strata/maricopa-az/maricopa-az-carbonplan-tract-table.parquet",
            f"{module.BUCKET}/strata/maricopa-az/maricopa-az-usfs-wildfire-tract-table.parquet",
            "some-other-bucket/humane-intelligence/reference/maricopa-az/"
            "maricopa-az-overture-roads.parquet",
            f"{module.BUCKET}/private/maricopa-az/maricopa-az-overture-roads.parquet",
            f"{module.BUCKET}/reference/maricopa-az/",
    ):
        allowed, reason = module.hosted_allowed(uri)
        assert allowed is False and reason, uri


def test_prohibited_dependency_rule_fires_only_on_openable_paths() -> None:
    citation = ast.parse('MEASURE_BEFORE = {"source": "reports/lb-score-algebra.json quoted"}\n')
    assert module.opened_path_literals(citation) == []
    opener = ast.parse('pq.read_table("/tmp/reports/lb-score-algebra.json")\n')
    literals = module.opened_path_literals(opener)
    assert any("lb-score-algebra" in literal for literal in literals)
    constant = ast.parse('LEAK_PATH = "data/ledger/submissions.jsonl"\n')
    assert any("submissions.jsonl" in literal for literal in module.opened_path_literals(constant))
    scan = module.code_dependency_scan(["scripts/build_estimator_v2_candidates.py"], {}, {}, [],
                                       set(CONFIG["allowed_strata_columns"]))
    assert scan["prohibited_opened_hit_count"] == 0
    assert scan["prohibited_prose_hit_count"] > 0  # the quoted background is disclosed, not hidden


def test_strata_projection_constant_outside_the_subset_is_detected() -> None:
    tree = ast.parse('STRATA_COLUMNS = ["GEOID", "ALAND", "usgs_wildfire_ever"]\n')
    assert module.strata_projection_constants(tree) == {
        "STRATA_COLUMNS": ["GEOID", "ALAND", "usgs_wildfire_ever"]}
    scan = module.code_dependency_scan([], {}, {}, [], set(CONFIG["allowed_strata_columns"]))
    assert scan["strata_projection_violations"] == []
    # the same constant with the real projection is clean
    clean = module.strata_projection_constants(
        ast.parse('STRATA_COLUMNS = ["GEOID", "ALAND", "pop_total", "pct_urban"]\n'))
    assert all(column in CONFIG["allowed_strata_columns"]
               for column in clean["STRATA_COLUMNS"])


def test_no_code_requested_column_is_undeclared() -> None:
    audit = REPORT["column_request_audit"]
    assert audit["code_requested_but_undeclared"] == []
    assert audit["code_requested_columns"]
    for name in audit["code_requested_columns"]:
        assert not any(name.lower().startswith(family)
                       for family in CONFIG["prohibited"]["strata_column_prefixes"]) or \
            name in audit["declared_columns_union"]


def test_fixed_rules_match_the_committed_constants_independently() -> None:
    for rule in CONFIG["fixed_rules"]:
        if not rule.get("constant"):
            continue
        tree = ast.parse((REPO / rule["module"]).read_text(encoding="utf-8"))
        observed = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == rule["constant"]:
                        observed = ast.literal_eval(node.value)
        assert observed is not None, rule["rule_id"]
        if isinstance(rule["value"], (int, float)):
            assert abs(float(observed) - float(rule["value"])) <= 1e-12, rule["rule_id"]
        elif isinstance(rule["value"], dict):
            assert module.canonical(observed) == module.canonical(rule["value"]), rule["rule_id"]
        for basis in rule["basis"]:
            assert (REPO / basis).exists(), (rule["rule_id"], basis)


def test_retired_leaderboard_constants_are_absent_from_scores_and_score_path() -> None:
    isolation = REPORT["retired_constant_isolation"]
    assert isolation["clean"] is True
    assert all(count == 0 for count in isolation["score_value_hits"].values())
    assert all(count == 0 for count in isolation["region_mean_hits"].values())
    assert isolation["score_cells_scanned"] == 9794 * 4
    for entry in isolation["per_constant"].values():
        assert entry["score_path_scripts_using_value"] == []
        assert entry["provenance_class"] == "historical_disclosure_not_model_input"
    # independent value scan straight from the artifact
    with ARTIFACT.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        index = {name: header.index(name) for name in CONFIG["candidate"]["score_columns"]}
        values = [float(row[index[name]]) for row in reader if row for name in index]
    for name, expected in CONFIG["code_scan"]["retired_lb_constants"].items():
        assert sum(1 for value in values if abs(value - expected) <= 1e-12) == 0, name


def test_composite_score_is_the_defined_component_mean_recomputed_in_rationals() -> None:
    pins = json.loads((REPO / CONFIG["candidate"]["mask_config"]).read_text(encoding="utf-8"))[
        "frozen_inputs"]
    tables = {}
    for name, key in (("transport", "transport_component"), ("building", "building_component"),
                      ("poi", "poi_component")):
        pin = pins[key]
        with (REPO / pin["path"]).open(newline="", encoding="utf-8") as stream:
            tables[name] = ({str(row["GEOID"]): row for row in csv.DictReader(stream)},
                            pin["gap_column"], pin["defined_column"])
    workspace = Path(os.environ.get("COMPETITION_WORKSPACE", ""))
    if not workspace.is_dir():
        pytest.skip("COMPETITION_WORKSPACE is not set")
    official = set()
    for region in module.REGIONS:
        with (workspace / pins["official_samples"]["path_template"].format(region=region)).open(
                newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream)
            header = next(reader)
            geoid_index = header.index("GEOID")
            official.update(row[geoid_index] for row in reader if row)
    worst = 0.0
    with ARTIFACT.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            geoid = row["GEOID"]
            if geoid not in official:
                assert float(row["coverage_gap_score"]) == 0.0
                continue
            defined = [Fraction(float(table[geoid][gap_column]))
                       for table, gap_column, defined_column in tables.values()
                       if int(table[geoid][defined_column]) == 1]
            expected = float(sum(defined) / len(defined)) if defined else 0.0
            worst = max(worst, abs(float(row["coverage_gap_score"]) - expected))
    # the emitted field carries .17g text of the same defined-component mean; allow only the
    # double-rounding floor and require the same order as the audit's 1e-12 gate
    assert worst <= 1e-15
    assert REPORT["composite_recomputation"]["rows_above_1e_12"] == 0
    assert REPORT["composite_recomputation"]["zero_defined_official_rows"] == 0


def test_manifest_is_tied_to_the_candidate_build_and_every_input_is_hashed() -> None:
    assert MANIFEST is not None
    candidates = load(REPO / CONFIG["candidate"]["manifest"])
    card = next(entry for entry in candidates["candidate_set"]
                if entry["candidate_id"] == CONFIG["candidate"]["id"])
    build_audit = load(REPO / CONFIG["candidate"]["build_audit"])
    assert MANIFEST["candidate_artifact"]["sha256"] == card["sha256"]
    assert MANIFEST["candidate_artifact"]["sha256"] == build_audit["artifact"]["sha256"]
    digest = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    assert MANIFEST["candidate_artifact"]["sha256"] == digest
    assert MANIFEST["candidate_artifact"]["tie_verified"] is True
    assert MANIFEST["build"]["sha256"] == build_audit["reproducibility"]["script_sha256"]
    assert MANIFEST["inputs_without_hash"] == []
    assert all(entry["sha256"] for entry in MANIFEST["inputs"])
    kinds = {entry["kind"] for entry in MANIFEST["inputs"]}
    assert {"local_provided", "hosted_object", "derived", "code", "evidence"} <= kinds


@pytest.mark.skipif(REPORT is None, reason="run the audit to produce the report")
def test_all_acceptance_gates_pass() -> None:
    acceptance = REPORT["acceptance"]
    assert acceptance["all_passed"] is True
    assert set(CONFIG["acceptance"]) <= set(acceptance)
    for key, expected in CONFIG["acceptance"].items():
        if key == "fraction_fields_with_manifest_entry":
            assert acceptance[key] >= expected
        elif key == "prohibited_source_dependencies":
            assert acceptance[key] == expected
        else:
            assert acceptance[key] is expected, key


def test_sha256_stream_matches_sha256_file(tmp_path: Path) -> None:
    path = tmp_path / "blob.bin"
    path.write_bytes(b"overture" * 4096)
    assert module.sha256_stream(path.open("rb")) == module.sha256_file(path)


def test_canonical_hash_is_order_insensitive() -> None:
    assert module.digest({"a": 1, "b": [1, 2]}) == module.digest({"b": [1, 2], "a": 1})
    assert module.digest({"a": 1}) != module.digest({"a": 2})
