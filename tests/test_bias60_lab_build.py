"""Tests for the staged external-lab build: exclusion, recipe, provenance, and the rails.

Nothing here touches the network: census files and Overture shards are supplied as synthetic bytes
through the same injected seams the real run bypasses. The real staged artifacts under
``$COMPETITION_WORKSPACE/lab/staging`` are checked only for the invariants the row promises.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import pyarrow as pa
import shapefile
from shapely import from_wkb
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_bias60_lab as builder  # noqa: E402
from bias60 import lab_eval, lab_plan  # noqa: E402

CONFIG = builder.load_config()
WORKSPACE = Path(os.environ["COMPETITION_WORKSPACE"])
REPORT_PATH = ROOT / CONFIG["outputs"]["report"]
MARKDOWN_PATH = ROOT / CONFIG["outputs"]["markdown"]
TEST_ROOT = Path(os.environ["COMPETITION_WORKSPACE"]) / "lab-test"
TEST_CACHE = TEST_ROOT / "staging" / "cache-test"
GROUP = "NM"

# A study geography on Phoenix ground, a lab group on Albuquerque ground: 700 km apart, so the
# neighbour buffer can only bite where the fixture puts it there on purpose.
STUDY_ORIGIN = (33.40, -112.10)
LAB_ORIGIN = (35.70, -105.90)


def _square(lon: float, lat: float, size: float = 0.01) -> list[tuple[float, float]]:
    return [(lon, lat), (lon + size, lat), (lon + size, lat + size), (lon, lat + size), (lon, lat)]


def _zip_directory(root: Path, name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(root.iterdir()):
            if path.suffix in (".shp", ".shx", ".dbf", ".prj"):
                archive.write(path, path.name)
    del name
    return buffer.getvalue()


def _write_shapefile(prefix: str, configure) -> bytes:
    """pyshp writes to a path, so the fixtures are written to a temp dir and then zipped."""
    directory = Path(tempfile.mkdtemp(prefix="labfix-"))
    writer = shapefile.Writer(str(directory / prefix))
    configure(writer)
    writer.close()
    return _zip_directory(directory, prefix)


def tract_zip(records: list[dict]) -> bytes:
    """A Census tract shapefile holding the supplied polygons."""

    def configure(shape_writer) -> None:
        shape_writer.shapeType = 5  # POLYGON
        shape_writer.field("GEOID", "C", 14)
        shape_writer.field("COUNTYFP", "C", 3)
        shape_writer.field("STATEFP", "C", 2)
        for entry in records:
            shape_writer.poly([_square(entry["lon"], entry["lat"])])
            shape_writer.record(entry["geoid"], entry["county"][2:], entry["county"][:2])
    return _write_shapefile("tract", configure)


def roads_zip(lines: list[dict]) -> bytes:
    """A TIGER All Roads shapefile holding the supplied lines and MTFCC codes."""

    def configure(shape_writer) -> None:
        shape_writer.shapeType = 3  # POLYLINE
        shape_writer.field("MTFCC", "C", 5)
        shape_writer.field("GEODIST", "C", 20)
        for entry in lines:
            shape_writer.line([[(entry["lon"], entry["lat"]),
                                (entry["lon"] + entry["length"], entry["lat"])]])
            shape_writer.record(entry["mtfcc"], "road")
    return _write_shapefile("roads", configure)


def study_records(official: list[str], template: list[str]) -> list[dict]:
    """Every excluded GEOID parked at the study origin, plus one neighbour for the buffer test."""
    records = []
    for index, geoid in enumerate(sorted(set(official) | set(template))):
        records.append({"geoid": geoid, "county_fips": "04013",
                        "lat": STUDY_ORIGIN[0] + (index % 50) * 0.001,
                        "lon": STUDY_ORIGIN[1] + (index // 50) * 0.001})
    return records


def empty_tract_zip() -> bytes:
    """A census tract shapefile with the fields the reader demands and no records."""

    def configure(shape_writer) -> None:
        shape_writer.shapeType = 5  # POLYGON
        for name in ("GEOID", "COUNTYFP", "STATEFP"):
            shape_writer.field(name, "C", 14)
        for name in ("INTPTLAT", "INTPTLON"):
            shape_writer.field(name, "N", 15, 10)
    return _write_shapefile("study", configure)


def overture_table(lines: list[dict]) -> pa.Table:
    rows = []
    for entry in lines:
        line = LineString([(entry["lon"], entry["lat"]), (entry["lon"] + entry["length"],
                                                           entry["lat"])])
        rows.append({"id": entry.get("id", "seg"), "class": entry["class"], "subtype": entry["subtype"],
                     "names": entry.get("names"), "geometry": line.wkb})
    return pa.Table.from_pylist(rows)


def make_fetch(tracks: dict[str, bytes]):
    """A fetcher that serves registered URLs from a fixture table and refuses everything else."""

    def fetch(url: str) -> bytes:
        for pattern, payload in tracks.items():
            if url.endswith(pattern):
                return payload
        raise AssertionError(f"the build asked for an unregistered URL: {url}")
    return fetch


def build(*, tamper: str | None = None, tract_records: list[dict] | None = None,
          tiger_lines: list[dict] | None = None, overture_lines: list[dict] | None = None,
          lister: object = None, budget_bytes: int | None = None) -> dict:
    """Run the whole staged build for one group against synthetic bytes."""
    official = lab_plan.official_scored_ids(WORKSPACE)
    template = lab_plan.template_geoids(WORKSPACE)
    keepers = [{"geoid": f"35001950{n:03d}", "county": "35001",
                "lon": LAB_ORIGIN[1] + (n // 6) * 0.05, "lat": LAB_ORIGIN[0] + (n % 6) * 0.05}
               for n in range(34)]
    tract_records = tract_records or keepers + [
        # a tract in a county the provided packages touch, which retrieval must never cover
        {"geoid": "35023950000", "county": "35023", "lon": LAB_ORIGIN[1] + 0.20,
         "lat": LAB_ORIGIN[0]},
        # a scored GEOID of the challenge itself
        {"geoid": official[0], "county": "35001", "lon": LAB_ORIGIN[1] + 0.30, "lat": LAB_ORIGIN[0]},
        # a neighbour of the study sample, which the eight kilometre buffer must drop
        {"geoid": "35001950300", "county": "35001", "lon": STUDY_ORIGIN[1] + 0.001,
         "lat": STUDY_ORIGIN[0] + 0.001},
    ]
    tiger_lines = tiger_lines if tiger_lines is not None else [
        {"mtfcc": "S1100", "lon": LAB_ORIGIN[1] - 0.001, "lat": LAB_ORIGIN[0] + 0.005,
         "length": 0.02},
        {"mtfcc": "S1200", "lon": LAB_ORIGIN[1] - 0.001, "lat": LAB_ORIGIN[0] + 0.006,
         "length": 0.02},
        {"mtfcc": "S1400", "lon": LAB_ORIGIN[1] - 0.001, "lat": LAB_ORIGIN[0] + 0.007,
         "length": 0.02},
        {"mtfcc": "S1100", "lon": LAB_ORIGIN[1] + 0.049, "lat": LAB_ORIGIN[0] + 0.005,
         "length": 0.02},
    ]
    overture_lines = overture_lines if overture_lines is not None else [
        {"class": "primary", "subtype": "road", "lon": LAB_ORIGIN[1] - 0.001,
         "lat": LAB_ORIGIN[0] + 0.005, "length": 0.01},
        {"class": "residential", "subtype": "road", "lon": LAB_ORIGIN[1] - 0.001,
         "lat": LAB_ORIGIN[0] + 0.006, "length": 0.02},
        {"class": "primary", "subtype": "road", "lon": LAB_ORIGIN[1] + 0.049,
         "lat": LAB_ORIGIN[0] + 0.005, "length": 0.02},
        {"class": "primary", "subtype": "path", "lon": LAB_ORIGIN[1] + 0.049,
         "lat": LAB_ORIGIN[0] + 0.008, "length": 0.02},
    ]

    tracks = {
        "tl_2020_35_tract.zip": tract_zip(tract_records),
        "tl_2024_35001_roads.zip": roads_zip(tiger_lines),
        "tl_2024_35023_roads.zip": roads_zip(tiger_lines),
    }
    for study_state in ("04", "06", "40", "48", "53"):
        tracks[f"tl_2024_{study_state}_tract.zip"] = empty_tract_zip()
    document = builder.lab_config_document()
    group = next(entry for entry in document["groups"] if entry["id"] == GROUP)
    config = json.loads(json.dumps(CONFIG))
    # the synthetic build lives in its own root so it can never overwrite the real staged lab
    config["lab_root"] = "$COMPETITION_WORKSPACE/lab-test"
    config["groups_this_build"] = [GROUP]
    config["__sha256__"] = CONFIG["__sha256__"]
    if budget_bytes is not None:
        config["retrieval"]["max_total_bytes"] = budget_bytes
    test_root = builder.lab_root_of(config)
    if test_root.exists():
        for path in sorted(test_root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
    staging = test_root / "staging" / GROUP
    if staging.exists():
        for path in sorted(staging.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
    staging.mkdir(parents=True, exist_ok=True)
    fetch = make_fetch(tracks)
    budget = builder.Budget(config)
    tracts = builder.build_tracts(group, config, budget=budget, fetch=fetch,
                                 study_records=study_records(official, template))
    observed = builder.observed_road_lengths(
        group, tracts["tracts"], config,
        lister=(lister or (lambda endpoint, prefix: [{
            "key": "release/2026-09-23.1/theme=transportation/type=segment/"
                   "part-00000-2585c915-3d9b-5fc8-ad89-cfc455d21f00-c000.zstd.parquet",
            "size": 10, "etag": "etag"}])),
        reader=(lambda key, groups, columns: overture_table(overture_lines)))
    reference = builder.reference_road_lengths(group, tracts["tracts"], config, budget=budget,
                                              fetch=fetch)
    defineness = builder.write_labels(group, tracts["tracts"], observed, reference, config)
    manifest = builder.write_manifest(group, tracts, observed, reference, defineness, config,
                                      lab_commit="0" * 40)
    manifest["defineness"]["study_points"] = tracts["study"]["study_points"]
    return {"report": builder.build_report(config, manifests=[manifest], tamper=tamper),
            "tracts": tracts, "observed": observed, "reference": reference,
            "defineness": defineness, "manifest": manifest, "staging": staging}


class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.built = build()
        cls.report = cls.built["report"]

    def test_every_gate_passes_on_a_synthetic_build(self) -> None:
        failed = [key for key, value in self.report["gates"].items() if value is not True]
        self.assertEqual(failed, [])
        self.assertTrue(self.report["gates"]["all_passed"])
        self.assertGreaterEqual(len(self.report["gates"]), 20)

    def test_the_exclusion_actually_drops_each_forbidden_tract(self) -> None:
        rejected = self.built["tracts"]["rejected_by_reason"]
        self.assertEqual(rejected.get("official_scored_or_template_geoid"), 1)
        self.assertEqual(rejected.get("study_county"), 1)
        self.assertEqual(rejected.get("within_neighbour_buffer_of_study_tract"), 1)
        self.assertEqual(self.built["tracts"]["accepted"], 34)
        self.assertGreater(self.built["tracts"]["min_nearest_study_tract_km"],
                           CONFIG["exclusion"]["buffer_km"])

    def test_the_gap_is_the_clipped_ratio_and_undefined_rows_are_never_scored_as_zero(self) -> None:
        defineness = self.built["defineness"]
        road = defineness["components"]["road"]
        self.assertEqual(road["defined"] + road["undefined"], self.built["tracts"]["accepted"])
        self.assertTrue(defineness["undefined_never_scored_as_zero"])
        # two keepers carry TIGER length, the other 32 measure no reference road at all
        self.assertEqual(road["defined"], 2)
        self.assertEqual(road["undefined"], 32)
        import pyarrow.parquet as pq

        labels = pq.read_table(self.built["staging"] / "labels.parquet").to_pylist()
        for row in labels:
            self.assertIn(row["component"], {"road"})
            self.assertEqual(row["recipe_variant_id"], CONFIG["recipe"]["recipe_variant_id"])
            self.assertGreaterEqual(row["label"], 0.0)
            self.assertLessEqual(row["label"], 1.0)
            if not row["defined"]:
                self.assertEqual(row["label"], 0.0)
        # one tract is half covered by Overture, the other is covered, so the two gaps differ by
        # the whole range of the recipe
        defined = sorted(row["label"] for row in labels if row["defined"])
        self.assertEqual(len(defined), 2)
        self.assertAlmostEqual(defined[0], 0.0, delta=0.05)
        self.assertAlmostEqual(defined[1], 0.55, delta=0.15)
        self.assertIn(self.built["defineness"]["road_ratio"]["inside_band"]
                      or self.built["defineness"]["road_ratio"]["within_tolerance"], [True])

    def test_only_the_preregistered_classes_and_subtypes_count_as_observed_roads(self) -> None:
        # residential roads and paths must not enter the numerator
        self.assertEqual(self.built["observed"]["segments_seen"], 2)
        self.assertEqual(self.built["reference"]["lines_skipped_other_mtfcc"], 1)

    def test_the_lab_root_stays_outside_the_repo_and_unpromoted(self) -> None:
        self.assertFalse(builder._is_under(builder.lab_root_of(CONFIG), ROOT))
        self.assertEqual(self.report["lab_root"]["promoted_artifacts_present"], [])
        for name in lab_eval.ARTIFACTS:
            self.assertFalse((builder.lab_root_of(CONFIG) / name).exists(), name)

    def test_manifest_provenance_names_every_file_with_hash_date_and_size(self) -> None:
        for entry in self.built["manifest"]["files"]:
            for field in ("url", "version", "sha256", "bytes", "retrieval_date_utc", "role"):
                self.assertNotIn(entry.get(field), (None, ""), f"{entry.get('name')} lacks {field}")

    def test_two_synthetic_builds_are_byte_identical(self) -> None:
        again = build()
        self.assertEqual(builder.canonical_bytes(again["report"]),
                         builder.canonical_bytes(self.report))


class FoldEligibilityTests(unittest.TestCase):
    """A group that fails its own recipe self-check is disclosed, not counted."""

    def config_for(self, *groups: str) -> dict:
        config = json.loads(json.dumps(CONFIG))
        config["groups_this_build"] = list(groups)
        return config

    def test_a_group_failing_its_own_self_check_is_staged_but_not_a_fold(self) -> None:
        built = build()
        manifest = json.loads(json.dumps(built["manifest"]))
        # a group whose observed length is 40% of its reference length: outside band and tolerance,
        # with the flags left as the builder would have written them
        manifest["defineness"]["road_ratio"].update(
            {"observed_km": 400.0, "reference_km": 1000.0, "ratio": 0.4,
             "inside_band": False, "within_tolerance": False})
        report = builder.build_report(self.config_for(GROUP), manifests=[manifest])
        self.assertEqual(report["groups_fold_eligible"], [])
        self.assertEqual([entry["group"] for entry in report["groups_rejected_by_self_check"]],
                         [GROUP])
        self.assertFalse(report["measurement"]["acceptance"]["met"])
        self.assertFalse(report["gates"]["recipe_self_checks_passed"])
        self.assertFalse(report["gates"]["no_measurement_without_bytes"])
        # but nothing about it is hidden: cost, ratio and flags are all published
        self.assertTrue(report["gates"]["self_check_rejections_are_disclosed"])
        entry = report["groups_rejected_by_self_check"][0]
        self.assertEqual(entry["road_ratio"], 0.4)
        self.assertGreater(entry["bytes_counted"], 0)
        self.assertGreater(entry["files_counted"], 0)
        with self.assertRaises(ValueError) as refused:
            builder.promote(self.config_for(GROUP), [manifest])
        self.assertIn("not fold-eligible", str(refused.exception))

    def test_a_fold_claiming_a_ratio_it_did_not_measure_is_caught(self) -> None:
        built = build()
        manifest = json.loads(json.dumps(built["manifest"]))
        # the lengths stay as measured; only the verdict is inflated into the band
        manifest["defineness"]["road_ratio"].update(
            {"ratio": 1.0, "inside_band": True, "within_tolerance": True})
        report = builder.build_report(self.config_for(GROUP), manifests=[manifest])
        self.assertFalse(report["gates"]["self_check_flags_agree_with_the_measured_ratio"])
        self.assertFalse(report["gates"]["all_passed"])


class NegativeControlTests(unittest.TestCase):
    EXPECTED = dict(CONFIG["negative_controls"])

    def test_every_declared_tamper_fires_its_expected_gate(self) -> None:
        for tamper, gate in sorted(self.EXPECTED.items()):
            with self.subTest(tamper=tamper):
                report = build(tamper=tamper)["report"]
                self.assertIn(gate, report["gates"])
                self.assertFalse(report["gates"][gate], f"{tamper} did not fire {gate}")
                self.assertFalse(report["gates"]["all_passed"])

    def test_an_undeclared_tamper_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build(tamper="invented_a_number")

    def test_a_group_substituted_for_a_preregistered_one_is_caught(self) -> None:
        """The group list is a preregistration, not a knob: swapping NM for another id must fail."""
        report = build(tamper="groups_are_not_the_preregistered_ones")["report"]
        self.assertFalse(report["gates"]["groups_are_the_preregistered_ones"])
        self.assertFalse(report["gates"]["all_passed"])

    def test_a_report_without_manifests_carries_no_measurement(self) -> None:
        report = builder.build_report(CONFIG, manifests=[])
        self.assertFalse(report["gates"]["no_fabricated_measurement"])
        self.assertFalse(report["gates"]["recipe_self_checks_passed"])
        self.assertEqual(report["measurement"]["groups"], [])

        def walk(value):
            if isinstance(value, dict):
                for inner in value.values():
                    yield from walk(inner)
            elif isinstance(value, (list, tuple)):
                for inner in value:
                    yield from walk(inner)
            elif isinstance(value, float) and not isinstance(value, bool):
                yield value

        # a recursive scan: the measurement block may not smuggle a number in at any depth
        floats = sorted(walk(report["measurement"]))
        self.assertEqual(floats, [])


class RetrievalSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.official = frozenset(lab_plan.official_scored_ids(WORKSPACE))
        self.counties = lab_plan.policy_from_config(builder.lab_config_document(), WORKSPACE,
                                                   study_points=[]).study_county_fips

    def assert_refuses(self, url: str, needle: str) -> None:
        with self.assertRaises(ValueError) as caught:
            builder.assert_url_is_authorized(url, CONFIG, study_geoids=self.official,
                                             study_counties=self.counties)
        self.assertIn(needle, str(caught.exception))

    def test_a_host_off_the_allowlist_is_refused(self) -> None:
        self.assert_refuses("https://example.com/geo/tiger/TIGER2024/ROADS/tl_2024_35001_roads.zip",
                            "non-allowlisted host")

    def test_a_url_matching_no_preregistered_pattern_is_refused(self) -> None:
        self.assert_refuses("https://www2.census.gov/geo/tiger/TIGER2024/PRISCTR/tl_2024_x.zip",
                            "matches no preregistered URL pattern")

    def test_downloadable_code_is_refused(self) -> None:
        self.assert_refuses("https://www2.census.gov/geo/tiger/TIGER2020/TRACT/tl_2020_35_tract.py",
                            "downloaded code")

    def test_a_url_naming_a_study_geoid_is_refused(self) -> None:
        geoid = sorted(self.official)[0]
        self.assertEqual(builder.study_value_in_url(f"https://x/tl_2020_{geoid}.zip",
                                                   self.official, self.counties), geoid)
        with self.assertRaises(ValueError) as caught:
            builder.assert_url_is_authorized(
                f"https://www2.census.gov/geo/tiger/TIGER2020/TRACT/tl_2020_{geoid}.zip", CONFIG,
                study_geoids=self.official, study_counties=self.counties)
        self.assertIn("study value", str(caught.exception))

    def test_a_url_naming_a_study_county_is_refused(self) -> None:
        county = sorted(self.counties)[0]
        self.assertEqual(builder.study_value_in_url(
            f"https://x/tl_2024_{county}_roads.zip", self.official, self.counties), county)
        with self.assertRaises(ValueError) as caught:
            builder.assert_url_is_authorized(
                f"https://www2.census.gov/geo/tiger/TIGER2024/ROADS/tl_2024_{county}_roads.zip",
                CONFIG, study_geoids=self.official, study_counties=self.counties)
        self.assertIn("study value", str(caught.exception))

    def test_the_byte_budget_is_enforced_before_the_bytes_arrive(self) -> None:
        with self.assertRaises(ValueError):
            build(budget_bytes=8)

    def test_the_builder_itself_never_uploads_or_submits(self) -> None:
        source = (ROOT / "scripts/build_bias60_lab.py").read_text(encoding="utf-8")
        for marker in ("upload(", "submit_zindi(", "zindi_submit(", "requests.", "httpx",
                       "S3FileSystem(aws", "put_object", "boto3"):
            self.assertNotIn(marker, source, marker)
        self.assertIn("anonymous=True", source)


class PromoteTests(unittest.TestCase):
    def test_promotion_is_refused_while_the_build_is_two_groups_of_five(self) -> None:
        built = build()
        manifest = built["manifest"]
        with self.assertRaises(ValueError) as caught:
            builder.promote(CONFIG, [manifest])
        self.assertIn("promotion refused", str(caught.exception))
        self.assertIn("of 5 groups staged", str(caught.exception))

    def test_promotion_is_refused_when_a_group_has_no_defined_label(self) -> None:
        built = build(overture_lines=[], tiger_lines=[])
        manifest = json.loads(json.dumps(built["manifest"]))
        manifest["defineness"]["composite_defined_count"] = 0
        with self.assertRaises(ValueError) as caught:
            builder.promote(CONFIG, [manifest, manifest, manifest, manifest, manifest])
        self.assertIn("no defined component label", str(caught.exception))


class CommittedArtifactTests(unittest.TestCase):
    def test_the_committed_report_separates_folds_from_self_check_rejections(self) -> None:
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        rejected = {entry["group"] for entry in report["groups_rejected_by_self_check"]}
        self.assertEqual(set(report["groups_staged"]),
                         set(report["groups_fold_eligible"]) | rejected)
        self.assertFalse(set(report["groups_fold_eligible"]) & rejected)
        for entry in report["groups_rejected_by_self_check"]:
            self.assertFalse(entry["inside_band"] or entry["within_tolerance"])
            self.assertGreater(entry["bytes_counted"], 0)
            self.assertGreater(entry["files_counted"], 0)
        self.assertEqual(report["summary"]["groups_fold_eligible"],
                         len(report["groups_fold_eligible"]))
        self.assertEqual(report["measurement"]["acceptance"]["met"],
                         report["summary"]["groups_fold_eligible"]
                         >= report["summary"]["groups_required"])
        for entry in report["measurement"]["groups"]:
            self.assertEqual(entry["fold_eligible"],
                             entry["group"] in report["groups_fold_eligible"])


    def test_the_committed_report_and_markdown_match_a_dry_run(self) -> None:
        completed = subprocess.run(
            ["/home/martin/hddProjects/langywrap/uv", "run", "--quiet",
             "--with", "numpy==2.5.4", "--with", "pyarrow==26.0.0", "--with", "pyproj==3.8.0",
             "--with", "shapely==2.2.0", "--with", "pyshp", "python",
             "scripts/build_bias60_lab.py", "--check-only"],
            cwd=str(ROOT), capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("unchanged", completed.stdout)

    def test_the_committed_report_passes_its_own_gates(self) -> None:
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        self.assertTrue(report["gates"]["all_passed"],
                        [key for key, value in report["gates"].items() if value is not True])
        self.assertEqual(report["summary"]["uploads"], 0)
        self.assertEqual(report["summary"]["reference_layer_bytes_computed_on_study_tracts"], 0)
        self.assertEqual(report["measurement"]["acceptance"]["met"], False)
        self.assertGreaterEqual(len(report["measurement"]["groups"]), 1)
        for entry in report["measurement"]["groups"]:
            self.assertGreater(entry["min_nearest_study_tract_km"],
                               CONFIG["exclusion"]["buffer_km"])
            self.assertGreater(entry["clipped_reference_lines"], 0)

    def test_the_markdown_reports_the_self_checks_and_the_held_components(self) -> None:
        text = MARKDOWN_PATH.read_text(encoding="utf-8")
        self.assertIn("Recipe self-checks", text)
        self.assertIn("hospital ratio", text)
        self.assertIn("not_built", text)
        self.assertIn("estimators", text)

    def test_the_staged_lab_has_no_model_readable_promoted_artifact(self) -> None:
        root = builder.lab_root_of(CONFIG)
        self.assertEqual([name for name in lab_eval.ARTIFACTS if (root / name).exists()], [])


class HashVerificationTests(unittest.TestCase):
    def test_a_corrupt_cache_is_caught_by_its_recorded_hash(self) -> None:
        staging = TEST_CACHE / "hash-verification"
        if staging.exists():
            for stale in staging.rglob("*"):
                if stale.is_file():
                    stale.unlink()
        staging.mkdir(parents=True, exist_ok=True)
        target = staging / "sample.zip"
        provenance = staging / "sample.zip.provenance.json"
        payload = b"reference bytes"
        target.write_bytes(payload)
        provenance.write_bytes(builder.canonical_bytes({
            "url": "https://www2.census.gov/geo/tiger/TIGER2020/TRACT/tl_2020_35_tract.zip",
            "name": "sample.zip", "version": "TIGER2020", "retrieval": "cache",
            "sha256": hashlib.sha256(b"other bytes").hexdigest(), "bytes": len(payload),
            "retrieval_date_utc": "2026-10-25T00:00:00Z"}))
        with self.assertRaises(ValueError) as caught:
            builder.http_download("https://www2.census.gov/geo/tiger/TIGER2020/TRACT/tl_2020_35_tract.zip",
                                  target, config=CONFIG, version="TIGER2020",
                                  budget=builder.Budget(CONFIG), fetch=lambda url: payload)
        self.assertIn("cache is corrupt", str(caught.exception))
        target.unlink()
        provenance.unlink()

    def test_the_named_helper_recognises_every_overture_names_shape(self) -> None:
        self.assertTrue(builder._has_name({"primary": "Road 66"}))
        self.assertTrue(builder._has_name({"common": {"default": "Main St"}}))
        self.assertTrue(builder._has_name(["Broadway"]))
        self.assertFalse(builder._has_name({"common": {"default": "  "}}))
        self.assertFalse(builder._has_name(None))
        self.assertFalse(builder._has_name({}))


if __name__ == "__main__":
    unittest.main()
