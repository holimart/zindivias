"""Tests for the bias60 common harness: registry derivation, idea contract, orthogonality report.

Network-free by construction: the seed ideas are exercised against a stub input object, and the only
tests that touch the real provided data are skipped when the workspace or its caches are absent.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60 import harness as harness_module  # noqa: E402
from bias60.contracts import Emission, IdeaOutput, clip_one_minus_ratio  # noqa: E402
from bias60.ideas import IMPLEMENTATIONS, implemented_ids  # noqa: E402
from bias60.inputs import ProvidedInputs  # noqa: E402
from bias60.orthogonality import (average_linkage_clusters, correlation_matrix,  # noqa: E402
                                  variance_inflation)
from bias60.spec import parse_spec  # noqa: E402

CONFIG_PATH = ROOT / "config/bias60-harness-preregistered.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
WORKSPACE = Path(os.environ.get("COMPETITION_WORKSPACE", "") or ".")
UV = "/home/martin/hddProjects/langywrap/uv"
DEPENDENCIES = ["--with", "numpy==2.5.4", "--with", "pyarrow==26.0.0", "--with", "shapely==2.2.0",
                "--with", "pyproj==3.8.0"]


def load_script(name: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def spec_parse() -> dict:
    return parse_spec(Path(CONFIG["spec"]["resolved_path"]))


def harness_config() -> dict:
    config = copy.deepcopy(CONFIG)
    config["__path__"] = "config/bias60-harness-preregistered.json"
    config["__sha256__"] = hashlib.sha256(
        harness_module.canonical_bytes(config)).hexdigest()
    return config


def stub_geoids(region: str) -> list[str]:
    return [f"{region[:2]}{index:06d}00" for index in range(1, 9)]


class StubInputs:
    """A ProvidedInputs stand-in with deterministic provided-shaped columns."""

    def __init__(self, config: dict, workspace: Path, region: str, geoids: list[str],
                 *, overrides: dict | None = None) -> None:
        self.config = config
        self.workspace = workspace
        self.region = region
        self.geoids = list(geoids)
        self.accessed = {"parquet_columns": [], "csv_columns": [], "layers": [], "caches": []}
        self.overrides = overrides or {}
        count = len(geoids)
        index = np.arange(count, dtype=np.float64)
        self._strata = {
            "carbonplan_buildings": self.overrides.get(
                "carbonplan_buildings", np.array([40.0, 50.0, 0.0, 20.0, 10.0, 8.0, 100.0, 25.0][:count])),
            "carbonplan_covered": self.overrides.get(
                "carbonplan_covered", np.array([True, True, True, True, True, True, True, False][:count])),
            "COUNTYFP": self.overrides.get(
                "COUNTYFP", np.array(["01", "01", "01", "02", "02", "02", "03", "03"][:count], dtype=object)),
            "ALAND": self.overrides.get("ALAND", np.array([10e6, 20e6, 30e6, 40e6, 50e6, 60e6, 70e6, 80e6][:count])),
            "pop_total": self.overrides.get("pop_total", np.array([100., 200., 300., 400., 500., 600., 700., 800.][:count])),
            "ruca_primary": self.overrides.get("ruca_primary", np.array([1., 4., 7., 8., 3., 6., 9., 10.][:count])),
            "svi_minority": self.overrides.get("svi_minority", np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8][:count])),
            "pct_urban": self.overrides.get("pct_urban", np.array([0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 0.5, 0.5][:count])),
            "svi_covered": self.overrides.get("svi_covered", np.ones(count, dtype=np.bool_)),
            "ruca_covered": self.overrides.get("ruca_covered", np.ones(count, dtype=np.bool_)),
            "tribal_legal_pct": self.overrides.get("tribal_legal_pct", np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7][:count])),
            "tribal_legal": self.overrides.get("tribal_legal", np.array([False, True, True, True, True, True, True, True][:count])),
            "cvi_baseline_infrastructure": self.overrides.get("cvi_baseline_infrastructure", np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8][:count])),
            "cvi_baseline_socioeconomic": self.overrides.get("cvi_baseline_socioeconomic", np.array([0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1][:count])),
            "cvi_xwalk_coverage": self.overrides.get("cvi_xwalk_coverage", np.ones(count)),
            "cvi_covered": self.overrides.get("cvi_covered", np.ones(count, dtype=np.bool_)),
        }
        self._features = {
            "overture_buildings": self.overrides.get(
                "overture_buildings", np.array([40.0, 45.0, 0.0, 25.0, 10.0, 4.0, 130.0, 25.0][:count])),
            "overture_building_esri_source_share": self.overrides.get(
                "overture_building_esri_source_share",
                np.array([0.1, 0.2, 0.0, 0.4, 0.5, 0.6, 0.05, np.nan][:count])),
            "overture_named_road_km_5070": self.overrides.get(
                "overture_named_road_km_5070",
                np.array([10.0, 20.0, 0.0, 5.0, 2.5, 1.0, 40.0, 8.0][:count])),
            "overture_poi_fire": self.overrides.get(
                "overture_poi_fire", np.array([0.0, 1.0, 0.0, 2.0, 1.0, 2.0, 0.0, 3.0][:count])),
            "overture_poi_ems": self.overrides.get(
                "overture_poi_ems", np.array([1.0, 0.0, 0.0, 1.0, 2.0, 1.0, 0.0, 3.0][:count])),
        }
        self._housing_units = self.overrides.get(
            "housing_units", np.array([50.0, 100.0, 150.0, 200.0, 250.0, 300.0, 350.0, 400.0][:count]))
        self._km = {
            "roads": self.overrides.get("roads_km", self._features["overture_named_road_km_5070"]),
            "roads-unfiltered": self.overrides.get(
                "unfiltered_km", self._features["overture_named_road_km_5070"] * 1.25),
        }

    def strata(self, columns):
        self.accessed["parquet_columns"].extend(
            f"{self.region}-strata-tract-table.parquet:{column}" for column in columns)
        return {column: np.asarray(self._strata[column], dtype=object if self._strata[column].dtype == object else np.float64)
                if self._strata[column].dtype != np.bool_ else self._strata[column]
                for column in columns}

    def features(self, columns):
        self.accessed["parquet_columns"].extend(
            f"{self.region}-overture-tract-summary.parquet:{column}" for column in columns)
        return {column: self._features[column] for column in columns}

    def acs_housing_units(self):
        self.accessed["layers"].append("census-acs-housing")
        self.accessed["parquet_columns"].append(
            f"{self.region}-census-acs-housing.parquet:GEOID,housing_units")
        return np.asarray(self._housing_units, dtype=np.float64)

    def named_class_km(self, layer: str):
        self.accessed["layers"].append(layer)
        return (np.asarray(self._km[layer], dtype=np.float64),
                {"region": self.region, "layer": layer, "rows_total": 100,
                 "rows_scanned": 100, "rows_named": 40, "cache_hit": True,
                 "road_named_classes": sorted(CONFIG["seeds"]["Sc5"]["road_named_classes"]),
                 "crs": "EPSG:5070", "truncated_rows": 0})

    def poi_shape_capture(self, source_a: str, source_b: str):
        self.accessed["layers"].append("pois")
        count = len(self.geoids)
        return ({"n_categories": np.full(count, 10.0), "s_obs": np.full(count, 8.0),
                 "f1": np.full(count, 2.0), "f2": np.full(count, 1.0),
                 "poi_total": np.full(count, 12.0),
                 "source_a": np.full(count, 5.0), "source_b": np.full(count, 3.0),
                 "source_overlap": np.zeros(count), "source_union": np.full(count, 8.0)},
                {"region": self.region, "layer": "pois", "rows_total": 100,
                 "assigned_points": count, "boundary_or_outside_points": 0,
                 "datasets_seen": [source_a, source_b], "cache_hit": True,
                 "microsoft_membership_used": False, "foursquare_membership_used": False})

    def building_area_bins(self, small_m2: float, upper_m2: float):
        self.accessed["layers"].append("buildings")
        count = len(self.geoids)
        return ({"below_small": np.zeros(count), "in_band": np.full(count, 5.0)},
                {"region": self.region, "layer": "buildings", "rows_total": 100,
                 "in_band_features": 2000, "log_area_sum": 2000 * np.log(100.0),
                 "log_area_squared_sum": 2000 * (np.log(100.0) ** 2 + 0.5),
                 "cache_hit": True, "microsoft_reference_used": False})


def stub_factory(overrides: dict | None = None):
    def factory(config, workspace, region, geoids):
        return StubInputs(config, workspace, region, geoids, overrides=overrides)
    return factory


def _stub_g_implementations():
    """Keep common harness tests isolated from raw-geometry package I/O."""
    originals = {idea: IMPLEMENTATIONS[idea] for idea in [*(f"G{i}" for i in range(1, 6)), "P1", "P3",
                                                            "Sc1", "Sc2", "Sc3", "Sc4", "Sca2", "Sca3",
                                                            "Sca4", "Sca5", "X1", "X3", "X4", "X5"]}
    def stub(idea_id, specification):
        def execute(inputs):
            from bias60.contracts import Emission, IdeaOutput
            emissions = []
            for component, channel in specification:
                emissions.append(Emission(component, channel, np.zeros(len(inputs.geoids), dtype=np.float64),
                                          np.ones(len(inputs.geoids), dtype=np.bool_)))
            return IdeaOutput(idea_id, inputs.region, inputs.geoids, emissions, {"synthetic_test_stub": True})
        return execute
    specs = {"G1": [("R", "gap"), ("R", "covariate")], "G2": [("R", "gap")],
             "G3": [("B", "gap")], "G4": [("B", "covariate")],
             "G5": [("Est", "covariate"), ("F", "covariate")],
             "P1": [("Est", "gap")], "P3": [("F", "covariate")],
             "Sc1": [("R", "gap")], "Sc2": [("F", "covariate")],
             "Sc3": [("F", "gap"), ("F", "covariate")],
             "Sc4": [("R", "covariate")], "Sca2": [("C", "covariate")],
             "Sca3": [("B", "covariate"), ("R", "covariate")],
             "Sca4": [("Est", "covariate")], "Sca5": [("C", "covariate")],
             "X1": [("F", "covariate")],
             "X3": [("Est", "covariate")], "X4": [("R", "covariate")],
             "X5": [("R", "covariate"), ("F", "covariate")]}
    IMPLEMENTATIONS.update({idea: stub(idea, specs[idea]) for idea in specs})
    return originals


def run_build(report: Path | None = None, *, workspace: Path | None = None,
              overrides: dict | None = None, tamper: str | None = None):
    config = harness_config()
    registry = harness_module.build_registry(spec_parse())
    regions = ["maricopa-az", "eastern-ok"]
    geoids = {region: stub_geoids(region) for region in regions}
    directory = workspace or Path(tempfile.mkdtemp(prefix="bias60-"))
    originals = _stub_g_implementations()
    try:
        return harness_module.build_report(config=config, registry_document=registry,
                                           workspace=directory, regions=regions,
                                           geoids_by_region=geoids,
                                           ideas_dir=directory / "ideas", tamper=tamper,
                                           inputs_factory=stub_factory(overrides))
    finally:
        IMPLEMENTATIONS.update(originals)


class SpecAndRegistryTests(unittest.TestCase):
    def test_spec_parses_sixty_ideas_with_every_required_field(self) -> None:
        parsed = spec_parse()
        self.assertEqual(len(parsed["ideas"]), 60)
        self.assertEqual(parsed["spec"]["family_count"], 12)
        for idea in parsed["ideas"]:
            for key in ("id", "family", "component", "kind", "inputs", "cost", "recipe",
                        "signal", "failure_mode", "orthogonal_because"):
                self.assertTrue(idea[key], f"{idea['id']} missing {key}")

    def test_spec_hash_matches_the_pin(self) -> None:
        self.assertEqual(spec_parse()["spec"]["sha256"], CONFIG["spec"]["sha256"])

    def test_registry_document_is_derived_and_complete(self) -> None:
        registry = harness_module.build_registry(spec_parse())
        self.assertEqual(registry["summary"]["ideas_total"], 60)
        self.assertEqual(sorted(registry["summary"]["by_family"].values()), [5] * 12)
        self.assertEqual(sorted(registry["summary"]["implemented"]), sorted(implemented_ids()))
        expected_components = {
            seed["component"] if "component" in seed else seed["components"][0]
            for seed in CONFIG["seeds"].values()}
        self.assertEqual(set(registry["summary"]["implemented_components"]), expected_components)
        for idea in registry["ideas"]:
            self.assertIn(idea["component"], CONFIG["vocabulary"]["components"])
            self.assertIn(idea["cost"], CONFIG["vocabulary"]["costs"])
            self.assertIn(idea["kind"], CONFIG["vocabulary"]["kinds"])

    def test_committed_registry_on_disk_matches_a_fresh_derivation(self) -> None:
        committed = json.loads((ROOT / "config/ideas.json").read_text(encoding="utf-8"))
        fresh = harness_module.build_registry(spec_parse())
        self.assertEqual(harness_module.canonical_bytes(committed),
                         harness_module.canonical_bytes(fresh))

    def test_three_seeds_span_three_components(self) -> None:
        registry = harness_module.build_registry(spec_parse())
        gates = harness_module.validate_registry_document(registry, harness_config(), spec_parse())
        self.assertTrue(gates["three_seeds_implemented"])
        self.assertTrue(gates["seeds_span_components"])
        expected_components = {
            seed["component"] if "component" in seed else seed["components"][0]
            for seed in CONFIG["seeds"].values()}
        self.assertEqual(set(registry["summary"]["implemented_components"]), expected_components)

    def test_registry_gates_fail_when_an_idea_is_dropped(self) -> None:
        registry = harness_module.build_registry(spec_parse())
        registry["ideas"] = registry["ideas"][1:]
        registry["summary"]["ideas_total"] = len(registry["ideas"])
        gates = harness_module.validate_registry_document(registry, harness_config(), spec_parse())
        self.assertFalse(gates["registry_holds_all_60_spec_ids"])
        self.assertFalse(gates["every_family_has_five_ideas"])

    def test_orphan_implementation_is_rejected(self) -> None:
        registry = harness_module.build_registry(spec_parse())
        for idea in registry["ideas"]:
            if idea["id"] == "Tm1":
                idea["id"] = "Tm9"
        gates = harness_module.validate_registry_document(registry, harness_config(), spec_parse())
        self.assertFalse(gates["no_orphan_implementation"])


class ContractTests(unittest.TestCase):
    def test_fixed_mapping_bounds_and_definedness(self) -> None:
        observed = np.array([0.0, 5.0, 10.0, 20.0, np.nan], dtype=np.float64)
        expectation = np.array([10.0, 5.0, 10.0, 0.0, 10.0], dtype=np.float64)
        gap, defined = clip_one_minus_ratio(observed, expectation)
        self.assertTrue(np.array_equal(defined, np.array([True, True, True, False, False])))
        self.assertEqual(list(gap[defined]), [1.0, 0.0, 0.0])
        self.assertTrue((gap[~defined] == 0.0).all())
        self.assertGreaterEqual(gap.min(), 0.0)
        self.assertLessEqual(gap.max(), 1.0)

    def test_mapping_is_not_fitted_and_is_symmetric_under_scaling(self) -> None:
        observed = np.array([3.0, 7.0])
        expectation = np.array([10.0, 10.0])
        first, _ = clip_one_minus_ratio(observed, expectation)
        second, _ = clip_one_minus_ratio(observed * 1000.0, expectation * 1000.0)
        self.assertTrue(np.allclose(first, second))

    def test_emission_validation_rejects_bad_values(self) -> None:
        config = harness_config()
        defined = np.array([True, True, False])
        with self.assertRaises(ValueError):
            Emission("R", "gap", np.array([0.5, 1.5, 0.0]), defined).validate(n_rows=3, config=config)
        with self.assertRaises(ValueError):
            Emission("R", "gap", np.array([0.5, np.nan, 0.0]), defined).validate(n_rows=3, config=config)
        with self.assertRaises(ValueError):
            Emission("R", "gap", np.array([0.5, 0.4, 0.2]), defined).validate(n_rows=3, config=config)
        with self.assertRaises(ValueError):
            Emission("Z", "gap", np.zeros(3), defined).validate(n_rows=3, config=config)
        with self.assertRaises(ValueError):
            Emission("R", "score", np.zeros(3), defined).validate(n_rows=3, config=config)

    def test_duplicate_emission_is_rejected(self) -> None:
        defined = np.array([True, True])
        emission = Emission("B", "gap", np.array([0.1, 0.2]), defined)
        output = IdeaOutput("Tm1", "maricopa-az", ["1", "2"], [emission, copy.deepcopy(emission)])
        with self.assertRaises(ValueError):
            output.validate(config=harness_config())


class SeedIdeaTests(unittest.TestCase):
    def test_tm1_gap_is_the_fixed_mapping_and_undefined_cells_are_not_zero_filled(self) -> None:
        inputs = StubInputs(harness_config(), Path("."), "maricopa-az", stub_geoids("maricopa-az"))
        output = IMPLEMENTATIONS["Tm1"](inputs)
        gap = next(e for e in output.emissions if e.channel == "gap")
        over = next(e for e in output.emissions if e.channel == "covariate")
        # tract 1: 40/40 -> 0 ; tract 2: 45/50 -> 0.1 ; tract 7: 130/100 -> gap 0, over 0.3
        self.assertAlmostEqual(gap.values[0], 0.0)
        self.assertAlmostEqual(gap.values[1], 0.1)
        self.assertAlmostEqual(gap.values[6], 0.0)
        self.assertAlmostEqual(over.values[6], 0.3)
        # the last tract is not covered by CarbonPlan: undefined, value exactly 0.0
        self.assertFalse(bool(gap.defined[-1]))
        self.assertEqual(gap.values[-1], 0.0)
        # a zero earlier-release count is undefined, never a fabricated total gap
        self.assertFalse(bool(gap.defined[2]))
        self.assertEqual(gap.values[2], 0.0)
        self.assertEqual({e.component for e in output.emissions}, {"B"})
        self.assertEqual(output.diagnostics["undefined_reasons"]
                         ["carbonplan_uncovered"], 1)

    def test_tm1_gap_stays_inside_the_unit_interval(self) -> None:
        inputs = StubInputs(harness_config(), Path("."), "eastern-ok", stub_geoids("eastern-ok"),
                            overrides={"carbonplan_buildings": np.array([1.0] * 8),
                                       "overture_buildings": np.array([0.0] * 8)})
        output = IMPLEMENTATIONS["Tm1"](inputs)
        gap = next(e for e in output.emissions if e.channel == "gap")
        self.assertEqual(gap.values[gap.defined].max(), 1.0)

    def test_mx4_county_share_is_the_declared_weighted_mean(self) -> None:
        inputs = StubInputs(harness_config(), Path("."), "maricopa-az", stub_geoids("maricopa-az"))
        output = IMPLEMENTATIONS["Mx4"](inputs)
        county = next(e for e in output.emissions if e.component == "F")
        tract = next(e for e in output.emissions if e.component == "B")
        weight = inputs._features["overture_buildings"]
        share = inputs._features["overture_building_esri_source_share"]
        expected = ((0.1 * 40 + 0.2 * 45 + 0.0 * 0) / (40 + 45))
        self.assertAlmostEqual(county.values[0], expected)
        # county 03 has one zero-building tract: its share comes from the other tract only
        self.assertAlmostEqual(county.values[6], 0.05)
        self.assertTrue(bool(county.defined[7]))
        # the tract's own share is undefined where the provided share is missing, not zero
        self.assertFalse(bool(tract.defined[7]))
        self.assertEqual(tract.values[7], 0.0)
        self.assertEqual(output.diagnostics["counties"], 3)
        self.assertAlmostEqual(float(weight.sum()), 279.0)
        self.assertAlmostEqual(float(share[0]), 0.1)

    def test_sc5_uses_both_layers_and_the_fixed_mapping(self) -> None:
        inputs = StubInputs(harness_config(), Path("."), "eastern-ok", stub_geoids("eastern-ok"))
        output = IMPLEMENTATIONS["Sc5"](inputs)
        gap = next(e for e in output.emissions if e.channel == "gap")
        # filtered is 0.8 of unfiltered in the stub, so the gap is 0.2 wherever E > 0
        self.assertAlmostEqual(gap.values[0], 0.2)
        self.assertFalse(bool(gap.defined[2]))
        share = next(e for e in output.emissions if e.channel == "covariate")
        self.assertAlmostEqual(share.values[0], 0.2)
        self.assertEqual({e.component for e in output.emissions}, {"R"})
        layers = output.diagnostics["layers"]
        self.assertEqual(sorted(layers), ["roads", "roads-unfiltered"])

    def test_sc5_rejects_a_convention_mismatch_with_the_feature_cache(self) -> None:
        inputs = StubInputs(harness_config(), Path("."), "eastern-ok", stub_geoids("eastern-ok"),
                            overrides={"roads_km": np.array([1.0] * 8)})
        with self.assertRaises(ValueError):
            IMPLEMENTATIONS["Sc5"](inputs)

    def test_sc5_measured_the_organiser_filter_as_class_neutral_on_the_real_package(self) -> None:
        """Documented measured result: filtered and unfiltered named-class length agree exactly."""
        report = json.loads((ROOT / "reports/ideas-harness.json").read_text(encoding="utf-8"))
        diagnostics = report["per_idea"]["Sc5"]["diagnostics"]
        for region, values in diagnostics.items():
            with self.subTest(region=region):
                self.assertEqual(values["tracts_excluded_entirely"], 0)
                self.assertEqual(values["tracts_with_filtered_length_absent_from_unfiltered"], 0)
                layers = values["layers"]
                self.assertEqual(layers["roads"]["rows_named"],
                                 layers["roads-unfiltered"]["rows_named"])
                self.assertLessEqual(values["gap_distribution"]["max"], 1e-9)


class HarnessTests(unittest.TestCase):
    def test_incremental_refresh_hash_verifies_preserved_prior_artifacts(self) -> None:
        cli = load_script("run_bias60_ideas.py", "bias60_run_cli_incremental_test")
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            prior_path = workspace / "ideas" / "Old.parquet"
            prior_path.parent.mkdir(parents=True)
            prior_payload = b"prior-parquet"
            prior_path.write_bytes(prior_payload)
            prior_hash = hashlib.sha256(prior_payload).hexdigest()
            previous = {
                "gates": {"all_passed": True, "emission_contract_satisfied": True,
                          "no_label_or_leaderboard_value_used": True,
                          "deterministic_rerun": True, "report_has_no_temporal_field": True},
                "per_idea": {"Old": {"path": "ideas/Old.parquet", "sha256": prior_hash,
                                       "rows": 8, "geoid_partition_exact": True,
                                       "undeclared_columns_read": [], "forbidden_source_hits": [],
                                       "emissions": [{"component": "B"}]}},
                "summary": {"report_sha256": "prior"},
            }
            partial = {
                "per_idea": {"D1": {"path": "ideas/D1.parquet", "sha256": "d1-hash",
                                      "rows": 8, "geoid_partition_exact": True,
                                      "undeclared_columns_read": [], "forbidden_source_hits": [],
                                      "emissions": [{"component": "Est"}]}},
                "gates": {"all_passed": True, "emission_contract_satisfied": True,
                          "no_label_or_leaderboard_value_used": True,
                          "deterministic_rerun": True, "report_has_no_temporal_field": True},
                "scope": {"official_scored_rows": 8},
                "environment": {"git_commit": "a" * 40},
                "code_commit_at_run": "a" * 40,
            }
            registry = {"ideas": [{"id": "Old", "status": "implemented"},
                                  {"id": "D1", "status": "implemented"}],
                        "summary": {"ideas_total": 60}}
            merged = cli.merge_partial_report(previous, partial, selected_ids={"D1"},
                                              workspace=workspace, registry_document=registry)
            self.assertTrue(merged["gates"]["all_passed"])
            self.assertEqual(set(merged["per_idea"]), {"Old", "D1"})
            self.assertTrue(merged["incremental_refresh"]["preserved_parquet_hashes_verified"])
            self.assertEqual(merged["summary"]["ideas_implemented"], 2)
            prior_path.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "prior parquet hash differs"):
                cli.merge_partial_report(previous, partial, selected_ids={"D1"},
                                         workspace=workspace, registry_document=registry)

    def test_every_implemented_idea_writes_one_parquet_with_the_official_partition(self) -> None:
        report, payloads = run_build()
        self.assertTrue(report["gates"]["all_passed"], report["gates"])
        self.assertEqual(set(report["per_idea"]), set(implemented_ids()))
        for idea_id, entry in report["per_idea"].items():
            self.assertEqual(entry["rows"], 16 * entry["emissions_per_region"])
            self.assertEqual(len(payloads[idea_id]), entry["bytes"])
            self.assertEqual(entry["geoid_partition_exact"], True)
            self.assertEqual(entry["undeclared_columns_read"], [])
            self.assertEqual(entry["forbidden_source_hits"], [])
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / f"{idea_id}.parquet"
                path.write_bytes(payloads[idea_id])
                table = pq.read_table(path)
                self.assertEqual(table.column_names, harness_module.PARQUET_COLUMNS)
                self.assertEqual(table.schema.field("value").type, pa.float64())
                self.assertEqual(table.schema.field("defined").type, pa.bool_())

    def test_two_runs_are_byte_identical(self) -> None:
        _, first = run_build()
        _, second = run_build()
        self.assertEqual(sorted(first), sorted(second))
        for idea_id in first:
            self.assertEqual(first[idea_id], second[idea_id])

    def test_report_carries_no_wall_clock_and_is_strict_json(self) -> None:
        report, _ = run_build()
        self.assertEqual(harness_module._wall_clock_hits(report), [])
        text = json.dumps(report, allow_nan=True)
        self.assertNotIn("NaN", text)
        self.assertTrue(report["gates"]["report_has_no_temporal_field"])

    def test_tamper_dropping_an_idea_fails_the_parquet_gate(self) -> None:
        report, _ = run_build(tamper="drop_idea")
        self.assertFalse(report["gates"]["every_idea_wrote_one_parquet"])
        self.assertFalse(report["gates"]["all_passed"])

    def test_out_of_range_gap_is_refused_by_the_contract(self) -> None:
        """The gap channel is bounded by contract: a value above 1 cannot be written at all.

        ``run_idea`` validates every emission before serialisation, so the only way to see a value
        above 1 in a parquet is the explicit tamper case below.
        """
        with self.assertRaises(ValueError):
            Emission("B", "gap", np.array([1.5, 0.0, 0.0]),
                     np.array([True, True, False])).validate(n_rows=3, config=harness_config())
        config = harness_config()
        registry = harness_module.build_registry(spec_parse())
        broken = copy.deepcopy(IMPLEMENTATIONS["Tm1"])

        def out_of_range(inputs):
            output = broken(inputs)
            emission = next(e for e in output.emissions if e.channel == "gap")
            emission.values[0] = 1.5
            return output

        IMPLEMENTATIONS["Tm1"] = out_of_range
        try:
            with self.assertRaises(ValueError):
                harness_module.build_report(
                    config=config, registry_document=registry, workspace=Path(tempfile.mkdtemp()),
                    regions=["maricopa-az"],
                    geoids_by_region={"maricopa-az": stub_geoids("maricopa-az")},
                    ideas_dir=Path(tempfile.mkdtemp()) / "ideas",
                    inputs_factory=stub_factory(None), only_ids={"Tm1"})
        finally:
            IMPLEMENTATIONS["Tm1"] = broken

    def test_tamper_duplicate_geoid_fails_the_partition_gate(self) -> None:
        report, _ = run_build(tamper="duplicate_geoid")
        self.assertFalse(report["gates"]["official_geoid_partition_exact"])
        self.assertTrue(report["partition_problems"])
        self.assertFalse(report["gates"]["all_passed"])

    def test_tamper_undeclared_column_and_forbidden_source_fail_their_gates(self) -> None:
        report, _ = run_build(tamper="undeclared_column")
        self.assertFalse(report["gates"]["no_undeclared_column_read"])
        report, _ = run_build(tamper="forbidden_source")
        self.assertFalse(report["gates"]["no_forbidden_source_accessed"])

    def test_tamper_nondeterministic_rerun_fails_the_determinism_gate(self) -> None:
        report, _ = run_build(tamper="nondeterministic_rerun")
        self.assertFalse(report["gates"]["deterministic_rerun"])
        self.assertFalse(report["gates"]["all_passed"])

    def test_check_only_reports_a_hash_drift(self) -> None:
        report, payloads = run_build()
        recorded = {idea: entry["sha256"] for idea, entry in report["per_idea"].items()}
        self.assertEqual(harness_module.check_only(report, payloads, recorded), [])
        broken = dict(recorded)
        broken["Tm1"] = "0" * 64
        problems = harness_module.check_only(report, payloads, broken)
        self.assertTrue(any("Tm1" in problem for problem in problems))

    def test_forbidden_value_scan_is_leaf_wise(self) -> None:
        tokens = ["0.0911", "coverage_gap_score"]
        self.assertEqual(harness_module.forbidden_value_hits({"a": 0.0911234}, tokens), [])
        self.assertEqual(harness_module.forbidden_value_hits({"a": 0.0911}, tokens), ["0.0911"])
        self.assertEqual(harness_module.forbidden_value_hits({"b": "coverage_gap_score"}, tokens),
                         ["coverage_gap_score"])

    def test_inputs_module_refuses_a_forbidden_path_token(self) -> None:
        config = harness_config()
        inputs = ProvidedInputs(config, WORKSPACE, "maricopa-az", ["1"])
        with self.assertRaises(PermissionError):
            inputs.layer_path("tiger-roads")
        with self.assertRaises(PermissionError):
            inputs.polygon_layer_path("usgs_facilities")


class OrthogonalityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.geoids = [f"g{index}" for index in range(40)]
        rng = np.random.default_rng(20261025)
        base = rng.normal(size=len(self.geoids))
        self.columns = {
            "A|R|gap": base + rng.normal(scale=0.01, size=len(self.geoids)),
            "A|R|covariate": base,                       # near-duplicate of A|R|gap
            "B|B|gap": rng.normal(size=len(self.geoids)),
            "C|F|gap": np.zeros(len(self.geoids)) + 1.0,  # constant
            "D|Est|gap": rng.normal(size=len(self.geoids)) * 1e-13,  # numerically null
        }
        self.paths: dict[str, Path] = {}
        for idea_id in ("A", "B", "C", "D"):
            rows = {"GEOID": [], "region": [], "idea": [], "family": [], "component": [],
                    "channel": [], "value": [], "defined": []}
            for column, values in self.columns.items():
                idea, component, channel = column.split("|")
                if idea != idea_id:
                    continue
                rows["GEOID"].extend(self.geoids)
                rows["region"].extend(["maricopa-az"] * len(self.geoids))
                rows["idea"].extend([idea] * len(self.geoids))
                rows["family"].extend([idea] * len(self.geoids))
                rows["component"].extend([component] * len(self.geoids))
                rows["channel"].extend([channel] * len(self.geoids))
                rows["value"].extend([float(v) for v in values])
                rows["defined"].extend([True] * len(self.geoids))
            table = pa.Table.from_pydict(rows)
            path = self.directory / f"{idea_id}.parquet"
            pq.write_table(table, path, compression="zstd")
            self.paths[idea_id] = path
        self.config = harness_config()

    def tearDown(self) -> None:
        self.folder.cleanup()

    def _report(self, tamper: str | None = None) -> dict:
        from bias60.orthogonality import build_report
        return build_report(config=self.config, idea_paths=self.paths, geoids=self.geoids,
                            implemented_ids=["A", "B", "C", "D"], git_commit="0" * 40,
                            versions={"numpy": "test"}, tamper=tamper)

    def test_report_is_gated_and_label_free(self) -> None:
        report = self._report()
        self.assertTrue(report["gates"]["all_passed"], report["gates"])
        self.assertFalse(report["scope"]["uses_target_or_label"])
        self.assertEqual(report["summary"]["columns_evaluated"], 5)

    def test_near_duplicate_columns_are_merged_and_flagged(self) -> None:
        report = self._report()
        flagged = {(pair["left"], pair["right"]) for pair in report["pairs_exceeding_merge_threshold"]}
        self.assertIn(("A|R|gap", "A|R|covariate"), flagged)
        members = report["clustering"]["cluster_members"]
        merged = [members[key] for key in members if "A|R|gap" in members[key]]
        self.assertIn("A|R|covariate", merged[0])
        self.assertLessEqual(report["clustering"]["maximum_within_cluster_mean_distance"], 0.2)

    def test_constant_and_numerically_null_columns_are_declared(self) -> None:
        report = self._report()
        self.assertEqual([entry["column"] for entry in report["degenerate_columns"]], ["C|F|gap"])
        self.assertEqual([entry["column"] for entry in report["numerically_null_columns"]],
                         ["D|Est|gap"])
        vif = {entry["column"]: entry["vif"] for entry in report["variance_inflation_factors"]}
        self.assertIsNone(vif["C|F|gap"])
        self.assertTrue(np.isfinite(vif["A|R|gap"]))
        self.assertTrue(report["gates"]["condition_number_finite"])

    def test_correlation_and_vif_agree_with_a_direct_computation(self) -> None:
        matrix = np.column_stack([self.columns[column] for column in
                                  ("A|R|gap", "A|R|covariate", "B|B|gap")])
        correlation, overlap = correlation_matrix(matrix)
        expected = np.corrcoef(matrix, rowvar=False)
        self.assertTrue(np.allclose(correlation, expected, atol=1e-9))
        self.assertEqual(overlap[0, 1], len(self.geoids))
        vif = variance_inflation(matrix)
        self.assertTrue(all(value > 1.0 for value in vif))

    def test_average_linkage_is_deterministic_and_respects_the_cut(self) -> None:
        distance = np.array([[0.0, 0.1, 0.9], [0.1, 0.0, 0.9], [0.9, 0.9, 0.0]])
        first = average_linkage_clusters(distance, 0.2)
        second = average_linkage_clusters(distance, 0.2)
        self.assertEqual(first, second)
        self.assertEqual(first[0], first[1])
        self.assertNotEqual(first[0], first[2])

    def test_tamper_breaking_symmetry_is_caught(self) -> None:
        report = self._report(tamper="break_symmetry")
        self.assertFalse(report["gates"]["correlation_is_symmetric"])
        self.assertFalse(report["gates"]["all_passed"])

    def test_tamper_low_coverage_is_caught(self) -> None:
        report = self._report(tamper="low_coverage")
        self.assertFalse(report["gates"]["coverage_threshold_enforced"])
        self.assertTrue(report["excluded_columns"])

    def test_tamper_missing_column_is_caught(self) -> None:
        report = self._report(tamper="drop_column")
        self.assertFalse(report["gates"]["evaluated_columns_are_the_implemented_ideas"])
        self.assertFalse(report["gates"]["all_passed"])

    def test_multitrait_multimethod_direction_is_reported(self) -> None:
        report = self._report()
        mtmm = report["multitrait_multimethod"]
        self.assertGreater(mtmm["mean_abs_rho_within_component"],
                           mtmm["mean_abs_rho_between_components"])


class CommittedArtifactTests(unittest.TestCase):
    """The committed reports must match the code that produced them, when data is available."""

    def setUp(self) -> None:
        if not WORKSPACE.is_dir():
            self.skipTest("COMPETITION_WORKSPACE is not set")

    def test_orthogonality_report_is_check_only_clean(self) -> None:
        if not (WORKSPACE / "ideas" / "Tm1.parquet").is_file():
            self.skipTest("idea parquets have not been built in this workspace")
        completed = subprocess.run(
            [UV, "run", "--quiet", *DEPENDENCIES, "python",
             "scripts/report_ideas_orthogonality.py", "--check-only"],
            cwd=ROOT, capture_output=True, text=True, timeout=900,
            env={**os.environ, "COMPETITION_WORKSPACE": str(WORKSPACE)})
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("unchanged", completed.stdout)

    def test_registry_check_only_is_clean(self) -> None:
        completed = subprocess.run(
            [UV, "run", "--quiet", *DEPENDENCIES, "python",
             "scripts/build_ideas_registry.py", "--check-only"],
            cwd=ROOT, capture_output=True, text=True, timeout=600)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("unchanged", completed.stdout)

    def test_registry_tamper_cases_fail(self) -> None:
        for tamper in ("drop_idea", "wrong_spec_hash"):
            with self.subTest(tamper=tamper):
                completed = subprocess.run(
                    [UV, "run", "--quiet", *DEPENDENCIES, "python",
                     "scripts/build_ideas_registry.py", "--tamper", tamper,
                     "--out", "/tmp/ideas-registry-tamper.json"],
                    cwd=ROOT, capture_output=True, text=True, timeout=600)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(tamper == "drop_idea" and "FAIL" or "FAIL", completed.stdout)




class DeterminismTests(unittest.TestCase):
    def test_normalized_bytes_ignores_the_runtime_commit_fields(self) -> None:
        from bias60.determinism import normalized_bytes
        first = {"environment": {"versions": {"numpy": "2.5.4"}, "git_commit": "a" * 40},
                 "code_commit_at_run": "a" * 40, "gates": {"all_passed": True}}
        second = json.loads(json.dumps(first))
        second["environment"]["git_commit"] = "b" * 40
        second["code_commit_at_run"] = "b" * 40
        self.assertEqual(normalized_bytes(first), normalized_bytes(second))
        second["gates"]["all_passed"] = False
        self.assertNotEqual(normalized_bytes(first), normalized_bytes(second))

    def test_check_only_accepts_a_report_written_at_an_earlier_commit(self) -> None:
        cli = load_script("run_bias60_ideas.py", "bias60_run_cli_for_test")
        from bias60.determinism import normalized_bytes
        report, _ = run_build()
        earlier = json.loads(json.dumps(report))
        earlier["environment"]["git_commit"] = "0" * 40
        earlier["code_commit_at_run"] = "0" * 40
        self.assertEqual(normalized_bytes(cli.without_content_sha(earlier)),
                         normalized_bytes(cli.without_content_sha(report)))
        tampered = json.loads(json.dumps(report))
        tampered["gates"]["all_passed"] = False
        self.assertNotEqual(normalized_bytes(cli.without_content_sha(tampered)),
                            normalized_bytes(cli.without_content_sha(report)))

    def test_committed_reports_carry_no_temporal_key(self) -> None:
        from bias60.determinism import wall_clock_hits
        for name in ("reports/ideas-harness.json", "reports/ideas-orthogonality.json",
                     "config/ideas.json"):
            path = ROOT / name
            if not path.is_file():
                self.skipTest(f"{name} has not been generated")
            with self.subTest(report=name):
                self.assertEqual(wall_clock_hits(json.loads(path.read_text())), [])


if __name__ == "__main__":
    unittest.main()
