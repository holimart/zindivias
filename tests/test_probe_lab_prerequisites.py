"""Tests for the lab prerequisite probe.

The probe's whole purpose is to measure things the lane would rather not discover after a 20 GB
download, so these tests feed it a synthetic census geography with a designed outcome: groups that
should clear the fold floor, one group that must not, a study line whose neighbour buffer must bite,
and source patterns that answer or do not. No test touches the network; the fetchers are injected.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from bias60 import lab_plan  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "probe_lab_prerequisites", ROOT / "scripts/probe_lab_prerequisites.py")
probe_module = importlib.util.module_from_spec(SPEC)
sys.modules["probe_lab_prerequisites"] = probe_module
SPEC.loader.exec_module(probe_module)

CONFIG_PATH = ROOT / "config/lab-prerequisites-preregistered.json"
LAB_CONFIG_PATH = ROOT / "config/bias60-lab-preregistered.json"
REPORT_PATH = ROOT / "reports/lab-prerequisites.json"
MARKDOWN_PATH = ROOT / "docs/decisions/lab-prerequisites.md"
WORKSPACE = Path(os.environ.get("COMPETITION_WORKSPACE", "") or ".").resolve()
UV = "/home/martin/hddProjects/langywrap/uv"
DEPENDENCIES = ["--with", "numpy==2.5.4", "--with", "pyarrow==26.0.0", "--with", "pyproj==3.8.0",
                "--with", "shapely==2.2.0", "--with", "pyshp"]
COMMITTED = [CONFIG_PATH, LAB_CONFIG_PATH, ROOT / "scripts/probe_lab_prerequisites.py",
             ROOT / "src/bias60/lab_plan.py", REPORT_PATH, MARKDOWN_PATH]

require_workspace = unittest.skipUnless((WORKSPACE / "defined-flags-inputs").is_dir(),
                                        "COMPETITION_WORKSPACE is not available")


def config() -> dict:
    document = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    document["__path__"] = str(CONFIG_PATH)
    document["__sha256__"] = "0" * 64
    return document


def _place(geoid: str, index: int) -> dict:
    # a compact study cluster of roughly 0.1 by 0.1 degrees near Phoenix, so an 8 km buffer has a
    # designed bite: the near tracts fall inside it and the far tracts, 600 km away, do not
    return {"geoid": geoid, "county_fips": "04013", "lat": 33.40 + (index % 100) * 0.001,
            "lon": -112.10 + (index // 100) * 0.001}


def _far(geoid: str, county: str, index: int) -> dict:
    return {"geoid": geoid, "county_fips": county, "lat": 39.0, "lon": -112.0 + index * 0.01}


def _near(geoid: str, county: str, index: int) -> dict:
    # inside the study cluster, so the eight kilometre neighbour buffer must bite
    return {"geoid": geoid, "county_fips": county, "lat": 33.45 + index * 0.0002,
            "lon": -112.05 + index * 0.0002}


def synthetic_records(official: list[str], template: list[str]) -> dict[str, list[dict]]:
    """A census geography with a designed result per planned group.

    Study tracts are the real excluded GEOIDs, all parked in one compact cluster. Everything the
    groups are meant to *keep* carries a state-99 GEOID, which cannot be an official or template
    GEOID, so a kept tract can never double as a study point of its own. The official GEOIDs used as
    candidates are parked in the Nevada file, where they are not study records, which is what lets the
    official-GEOID tamper put a scored tract into the accepted set.
    """
    excluded = sorted(set(official) | set(template))
    states: dict[str, list[dict]] = {}

    def add(state: str, entry: dict) -> None:
        states.setdefault(state, []).append(entry)

    for index, geoid in enumerate(excluded):
        state = geoid[:2]
        if state not in ("04", "06", "40", "48", "53"):
            continue
        add(state, {"geoid": geoid, "county_fips": "04013",
                    "lat": 33.40 + (index % 100) * 0.001, "lon": -112.10 + (index // 100) * 0.001})

    # AZ-R: 10 study neighbours and 30 far tracts -> 30 accepted, exactly on the fold floor
    for index in range(10):
        add("04", {"geoid": f"99001{index:06d}", "county_fips": "04001",
                   "lat": 33.45 + index * 0.0002, "lon": -112.05 + index * 0.0002})
    for index in range(30):
        add("04", {"geoid": f"99002{index:06d}", "county_fips": "04005",
                   "lat": 39.0, "lon": -112.0 + index * 0.01})

    # OK-R: the study sample itself plus two far tracts -> 2 survivors, which must be a shortfall
    for index, geoid in enumerate(sorted(g for g in official if g.startswith("40"))[:38]):
        add("40", {"geoid": geoid, "county_fips": geoid[:5], "lat": 35.5, "lon": -98.0})
    for index in range(2):
        add("40", {"geoid": f"99003{index:06d}", "county_fips": "40109",
                   "lat": 39.0, "lon": -98.5 + index * 0.01})

    # NV: 40 keepers, 10 study neighbours, and 20 official GEOIDs parked here so the tamper that
    # disables the official exclusion has something to let through
    for index in range(40):
        add("32", {"geoid": f"99004{index:06d}", "county_fips": "32003",
                   "lat": 39.5, "lon": -117.0 + index * 0.01})
    for index in range(10):
        add("32", {"geoid": f"99005{index:06d}", "county_fips": "32003",
                   "lat": 33.45 + index * 0.0002, "lon": -112.05 + index * 0.0002})
    for index, geoid in enumerate(sorted(g for g in official if g.startswith("04"))[:20]):
        add("32", {"geoid": geoid, "county_fips": "32031", "lat": 38.0, "lon": -115.0})

    # LA and AR-KS joined the plan when the build reached for five folds; the fixture must reach them
    # too, or a group with no synthetic geography looks like a measured shortfall
    for state, prefix, count, origin in (("22", "22019", 40, (-92.0, 31.0)),
                                         ("05", "05119", 20, (-93.5, 35.0)),
                                         ("20", "20091", 20, (-99.5, 38.0))):
        for index in range(count):
            add(state, {"geoid": f"990{state}{index:07d}"[:11], "county_fips": prefix,
                        "lat": origin[1], "lon": origin[0] + index * 0.01})

    for state, prefix, count in (("08", "08031", 25), ("49", "49035", 25), ("35", "35001", 40)):
        for index in range(count):
            add(state, {"geoid": f"9900{state}{index:05d}"[:11], "county_fips": prefix,
                        "lat": 40.0, "lon": -105.0 + index * 0.01})
    return states


def build(tamper: str | None = None, *, records: dict[str, list[dict]] | None = None,
          head_status: dict[str, int] | None = None, cache: Path | None = None,
          head: "Callable[..., dict] | None" = None) -> dict:
    probe_module.OPENED.clear()
    official = lab_plan.official_scored_ids(WORKSPACE)
    template = lab_plan.template_geoids(WORKSPACE)
    synthetic = records if records is not None else synthetic_records(official, template)
    if cache is None:
        cache = Path(tempfile.mkdtemp(prefix="prereq-"))

    def fake_get(url: str, cache_dir: Path, *, user_agent: str = "", timeout: int = 0) -> dict:
        target = Path(cache_dir) / Path(url).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic census geography")
        return {"url": url, "path": str(target), "bytes": 28, "sha256": "1" * 64}

    def fake_head(url: str, *, user_agent: str = "", timeout: int = 0) -> dict:
        # one registered pattern deliberately does not answer in every test, so the disclosure gates
        # always have something to disclose
        status = 403 if "cbp" in url else 200
        for token, code in (head_status or {}).items():
            if token in url:
                status = code
        return {"url": url, "method": "HEAD", "status": status, "content_length_bytes": 1024,
                "content_bytes_read": False, "credentials_used": False}

    lab_plan.set_recorder(probe_module.record)
    return probe_module.build_report(config(), workspace=WORKSPACE, lab_config=LAB_CONFIG_PATH,
                                     cache=cache, tamper=tamper, head=head or fake_head,
                                     get=fake_get,
                                     records_for_state=lambda state: synthetic.get(state, []))


@require_workspace
class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build()
        cls.groups = {group["id"]: group for group in cls.report["candidate_probe"]["groups"]}

    def test_the_probe_passes_its_own_contract_and_reports_a_negative_finding(self) -> None:
        self.assertTrue(self.report["gates"]["all_passed"], self.report["gates"])
        self.assertTrue(self.report["findings"]["lab_plan_needs_amendment"])
        self.assertEqual([entry["id"] for entry in self.report["findings"]["group_shortfalls"]],
                         ["OK-R"])
        self.assertIn("OK-R", self.report["findings"]["verdict"])

    def test_the_measured_groups_come_out_as_designed(self) -> None:
        self.assertEqual(self.groups["AZ-R"]["candidates_found"], 40)
        self.assertEqual(self.groups["AZ-R"]["accepted"], 30)
        self.assertEqual(self.groups["AZ-R"]["rejected_by_reason"]
                         .get("within_neighbour_buffer_of_study_tract"), 10)
        self.assertGreater(self.groups["OK-R"]["rejected_by_reason"]
                           .get("official_scored_or_template_geoid", 0), 1000)
        self.assertEqual(self.groups["OK-R"]["accepted"], 2)
        self.assertFalse(self.groups["OK-R"]["ready"])
        self.assertEqual(self.groups["NV"]["accepted"], 40)
        self.assertEqual(self.groups["NV"]["rejected_by_reason"]
                         .get("official_scored_or_template_geoid"), 20)
        self.assertEqual(self.groups["NV"]["rejected_by_reason"]
                         .get("within_neighbour_buffer_of_study_tract"), 10)
        self.assertTrue(self.groups["NV"]["ready"])
        self.assertEqual(self.groups["CO-UT"]["accepted"], 50)
        self.assertEqual(self.groups["NM"]["accepted"], 40)
        for group_id in ("AZ-R", "OK-R", "NV", "CO-UT", "NM"):
            with self.subTest(group=group_id):
                distance = self.groups[group_id]["accepted_min_distance_km"]
                self.assertTrue(distance is None or distance > 8.0,
                                f"{group_id} accepted a tract inside the buffer")

    def test_study_interior_points_are_established_from_the_public_file(self) -> None:
        self.assertGreater(self.report["study_geography"]["study_points"], 9000)
        self.assertTrue(self.report["gates"]["study_points_measured_from_public_geography"])

    def test_only_the_allowlisted_census_geometry_is_downloaded(self) -> None:
        self.assertTrue(self.report["gates"]["only_public_census_geography_downloaded"])
        self.assertTrue(self.report["gates"]["no_reference_layer_bytes_on_disk"])
        for entry in self.report["downloads"]["files"]:
            with self.subTest(url=entry["url"]):
                self.assertIn("www2.census.gov", entry["url"])
                self.assertTrue(entry["url"].endswith("_tract.zip"))
        self.assertTrue(self.report["gates"]["cache_outside_lab_root"])
        self.assertTrue(self.report["gates"]["no_lab_artifact_written"])

    def test_the_source_probe_is_head_only_anonymous_and_records_alternates(self) -> None:
        self.assertTrue(self.report["gates"]["sources_probed_without_content_bytes"])
        self.assertTrue(self.report["gates"]["source_probe_credentials_unused"])
        self.assertTrue(self.report["gates"]["all_registered_sources_probed"])
        self.assertEqual(len({entry["id"] for entry in self.report["sources"]["probed"]}),
                         self.report["sources"]["registered"])
        registered = json.loads(LAB_CONFIG_PATH.read_text(encoding="utf-8"))["sources"]
        with_alternates = [entry for entry in self.report["sources"]["probed"]
                           if entry.get("alternates")]
        self.assertEqual(len(with_alternates), len(json.loads(CONFIG_PATH.read_text())
                                                  ["source_probe"]["alternates"]))
        for entry in with_alternates:
            with self.subTest(source=entry["id"]):
                self.assertGreaterEqual(len(entry["alternates"]), 1)
        self.assertIn("hospital_check", [entry["id"] for entry in
                                         self.report["findings"]["sources_not_probeable"]])
        self.assertTrue(registered)

    def test_a_source_that_does_not_answer_is_disclosed_not_gated(self) -> None:
        report = build(head_status={"cbp": 503})
        self.assertTrue(report["gates"]["all_passed"], report["gates"])
        self.assertTrue(report["gates"]["source_availability_disclosed"])
        self.assertEqual([entry["id"] for entry in report["findings"]["sources_not_answered"]],
                         ["cbp_2023"])
        self.assertTrue(report["findings"]["lab_plan_needs_amendment"])

    def test_no_lab_artifact_and_no_reference_layer_is_written_anywhere(self) -> None:
        encoded = json.dumps(self.report).lower()
        for token in ("prisecroads", "usbuildingfootprints", "nationalmap", "cbp23",
                      "overturemaps-us-west-2/release/2"):
            self.assertNotIn(f"\"{token}", encoded, token)
        self.assertNotIn("manifest.json", [Path(entry["path"]).name for entry in
                                           self.report["downloads"]["files"]])

    def test_two_runs_are_identical_and_carry_no_wall_clock(self) -> None:
        # the same cache directory, because the report records where its census files live
        again = build(cache=Path(tempfile.mkdtemp(prefix="prereq-shared-")))
        third = build(cache=Path(str(again["access"]["cache_dir"])))
        self.assertEqual(without_probe_mode(third), without_probe_mode(again))
        # the mode field is the only thing a cache round may legitimately change
        self.assertEqual(again["sources"]["probed_from"], "network")
        self.assertEqual(third["sources"]["probed_from"], "cache")
        self.assertTrue(self.report["gates"]["report_has_no_temporal_field"])
        self.assertTrue(self.report["gates"]["report_is_strict_json"])


@require_workspace
class NegativeControlTests(unittest.TestCase):
    def expected_map(self) -> dict:
        return {case["id"]: case["expect_gate"] for case in
                json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["negative_controls"]}

    def test_the_preregistered_cases_match_the_expected_gates(self) -> None:
        self.assertEqual(self.expected_map(), {
            "source_probe_reads_bytes": "sources_probed_without_content_bytes",
            "source_probe_uses_credentials": "source_probe_credentials_unused",
            "registered_source_skipped": "all_registered_sources_probed",
            "download_off_allowlist": "only_public_census_geography_downloaded",
            "reference_layer_download": "no_reference_layer_bytes_on_disk",
            "cache_written_into_lab_root": "cache_outside_lab_root",
            "buffer_removed": "no_accepted_tract_within_buffer",
            "official_geoid_accepted": "no_official_geoid_accepted",
            "study_points_dropped": "study_points_measured_from_public_geography",
            "group_without_survivors_reported_as_ready": "shortfalls_disclosed",
            "temporal_field_added": "report_has_no_temporal_field",
            "source_unavailability_hidden": "source_availability_disclosed",
        })

    def test_every_negative_control_fires_its_expected_gate(self) -> None:
        for tamper, gate in self.expected_map().items():
            with self.subTest(tamper=tamper):
                report = build(tamper)
                self.assertIn(gate, report["gates"])
                self.assertFalse(report["gates"][gate], f"{tamper} did not fire {gate}")
                self.assertFalse(report["gates"]["all_passed"])

    def test_an_unknown_tamper_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build("download_the_lab_anyway")

    def test_tamper_runs_never_rewrite_a_committed_file(self) -> None:
        import hashlib
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in COMMITTED}
        for tamper in self.expected_map():
            build(tamper)
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in COMMITTED}
        self.assertEqual(before, after)


class GeographyTests(unittest.TestCase):
    def test_tract_records_are_read_from_a_real_zipped_shapefile(self) -> None:
        import shapefile

        directory = Path(tempfile.mkdtemp(prefix="tractzip-"))
        writer = shapefile.Writer(str(directory / "tl_2024_32_tract"))
        writer.field("GEOID", "C", 14)
        writer.field("STATEFP", "C", 2)
        writer.field("COUNTYFP", "C", 3)
        writer.field("INTPTLAT", "C", 15)
        writer.field("INTPTLON", "C", 15)
        writer.line([[(0, 0), (1, 1), (1, 0), (0, 0)]])
        writer.record("32003000100", "32", "003", "36.1", "-115.1")
        writer.line([[(0, 0), (1, 1), (1, 0), (0, 0)]])
        writer.record("32003000200", "32", "003", "0", "0")  # unusable interior point
        writer.close()
        zip_path = directory / "tl_2024_32_tract.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            for suffix in (".shp", ".shx", ".dbf"):
                archive.write(directory / f"tl_2024_32_tract{suffix}",
                              f"tl_2024_32_tract{suffix}")
        records = probe_module.read_tract_records(zip_path, json.loads(
            CONFIG_PATH.read_text(encoding="utf-8")))
        self.assertEqual([entry["geoid"] for entry in records], ["32003000100"])
        self.assertEqual(records[0]["county_fips"], "32003")

    def test_a_shapefile_without_the_required_fields_raises(self) -> None:
        import shapefile

        directory = Path(tempfile.mkdtemp(prefix="tractzip-bad-"))
        writer = shapefile.Writer(str(directory / "tl_2024_32_tract"))
        writer.field("NAME", "C", 20)
        writer.line([[(0, 0), (1, 1), (1, 0), (0, 0)]])
        writer.record("nothing")
        writer.close()
        zip_path = directory / "tl_2024_32_tract.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            for suffix in (".shp", ".shx", ".dbf"):
                archive.write(directory / f"tl_2024_32_tract{suffix}",
                              f"tl_2024_32_tract{suffix}")
        with self.assertRaises(ValueError):
            probe_module.read_tract_records(zip_path, json.loads(
                CONFIG_PATH.read_text(encoding="utf-8")))

    def test_nearest_km_matches_a_brute_force_scan(self) -> None:
        points = [(0.0, 0.0), (50_000.0, 12_000.0), (400_000.0, -300_000.0)]
        study = [(1_000.0, 1_000.0), (-250_000.0, 40_000.0), (900_000.0, 900_000.0)]
        import math
        expected = [min(math.hypot(x - sx, y - sy) for sx, sy in study) / 1000.0
                    for x, y in points]
        self.assertEqual(probe_module.nearest_km(points, study), expected)
        self.assertEqual(probe_module.nearest_km([], study), [])
        self.assertEqual(probe_module.nearest_km(points, []), [float("inf")] * len(points))

    def test_state_of_filename_only_accepts_the_census_tract_pattern(self) -> None:
        self.assertEqual(probe_module.state_of_filename(
            "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_08_tract.zip"), "08")
        self.assertEqual(probe_module.state_of_filename(
            "https://www2.census.gov/geo/tiger/TIGER2024/PRISECRROADS/tl_2024_32_prisecroads.zip"),
            "")


def without_probe_mode(report: dict) -> bytes:
    """Canonical bytes with the probe-mode disclosure removed.

    Whether a status line came from the network or from the recorded round is a disclosure about the
    run, not a measurement: the measurements themselves must survive a cache round-trip unchanged.
    """
    body = json.loads(json.dumps(report))
    body.get("summary", {}).pop("report_sha256", None)  # the self-hash follows the body
    body["sources"].pop("probed_from", None)
    body["sources"].pop("probed_from_cache_sha256", None)
    for entry in body["sources"]["probed"]:
        entry.pop("probed_from", None)
    return probe_module.canonical_bytes(body)


@require_workspace
class HeadAnswerCacheTests(unittest.TestCase):
    """A status line that can only be produced once is not a measurement the lane can keep."""

    def test_the_published_head_answers_survive_a_rerun(self) -> None:
        cache = Path(tempfile.mkdtemp(prefix="prereq-head-"))
        asked = {"n": 0}

        def flaky(url: str, *, user_agent: str = "", timeout: int = 0) -> dict:
            asked["n"] += 1
            answered = asked["n"] % 2 == 1
            return {"url": url, "method": "HEAD", "status": 403 if answered else 520,
                    "content_length_bytes": 1024 if answered else None,
                    "error": None if answered else "TimeoutError",
                    "content_bytes_read": False, "credentials_used": False}

        first = build(cache=cache, head=flaky)
        after_first = asked["n"]
        second = build(cache=cache, head=flaky)
        self.assertEqual(without_probe_mode(first), without_probe_mode(second))
        self.assertEqual(first["sources"]["probed_from"], "network")
        self.assertEqual(second["sources"]["probed_from"], "cache")
        self.assertEqual(asked["n"], after_first, "the second round asked the network again")
        self.assertTrue(second["gates"]["source_probe_answers_are_reusable"])
        self.assertEqual(len(str(second["sources"]["probed_from_cache_sha256"])), 64)
        # a source that was never asked must not be reported as if it had been
        skipped = [entry for entry in second["sources"]["probed"]
                   if entry.get("probe") == "skipped"]
        self.assertTrue(skipped)
        self.assertEqual({entry["probed_from"] for entry in skipped}, {"not_probed"})


@require_workspace
class CommittedArtifactTests(unittest.TestCase):
    def test_the_committed_report_matches_a_fresh_run_and_says_what_was_measured(self) -> None:
        document = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        self.assertTrue(document["gates"]["all_passed"])
        self.assertEqual(document["summary"]["groups_probed"], 7)
        self.assertGreater(document["summary"]["candidates_found"], 4000)
        self.assertEqual(document["summary"]["study_points"], 9793)
        self.assertGreater(document["summary"]["downloaded_bytes"], 1_000_000)
        self.assertTrue(document["findings"]["lab_plan_needs_amendment"])
        self.assertEqual(document["preregistration"]["spec_sha256"],
                         document["preregistration"]["spec_sha256_pin"])
        self.assertEqual(probe_module.render_markdown(document),
                         MARKDOWN_PATH.read_text(encoding="utf-8"))

    def test_check_only_rejects_a_mutated_committed_report(self) -> None:
        completed = subprocess_run(["--check-only"])
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("unchanged", completed.stdout)
        document = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        document["summary"]["candidates_accepted"] = 999_999
        tampered = ROOT / "reports/.lab-prerequisites-tampered.json"
        tampered.write_bytes(probe_module.canonical_bytes(document))
        try:
            completed = subprocess_run(["--check-only", "--out",
                                        str(tampered.relative_to(ROOT))])
            self.assertEqual(completed.returncode, 1, completed.stdout)
            self.assertIn("CHANGED", completed.stdout)
        finally:
            tampered.unlink(missing_ok=True)


def subprocess_run(extra: list[str]) -> "subprocess.CompletedProcess[str]":
    import subprocess
    return subprocess.run([UV, "run", "--quiet", *DEPENDENCIES, "python",
                           "scripts/probe_lab_prerequisites.py", *extra],
                          cwd=ROOT, capture_output=True, text=True, timeout=1800)


if __name__ == "__main__":
    unittest.main()
