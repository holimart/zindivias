import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_cdc_places_result_supports_border_population_wording_only():
    report = json.loads((REPO / "reports/who-is-affected-cdc-places.json").read_text())
    assert report["inputs"]["tx_scored_tracts_with_buildings"] == 6001
    assert report["inputs"]["pl_places_joined_tracts"] == 5970
    assert report["inputs"]["county_clusters"] == 185
    border = report["contrasts"]["ten border counties vs rest of South-Central Texas"]
    access = border["measures"]["ACCESS2"]
    assert round(access["group1_weighted_prevalence_pct"], 1) == 36.9
    assert round(access["group0_weighted_prevalence_pct"], 1) == 18.1
    assert round(access["difference_pp"], 1) == 18.8
    assert [round(x, 1) for x in access["county_cluster_ci95_pp"]] == [16.6, 22.1]

    # The low-OSM contrast is weak and its CI includes zero; it must not be
    # repackaged as evidence about the health of low-OSM tracts.
    low = report["contrasts"]["OSM share <5% vs >=5%, all Texas scored tracts"]["measures"]["ACCESS2"]
    assert round(low["difference_pp"], 1) == 5.1
    assert low["county_cluster_ci95_pp"][0] < 0 < low["county_cluster_ci95_pp"][1]
    assert round(report["within_county_low_osm_vs_other_pp"]["ACCESS2"]["difference_pp"], 1) == -0.5
    assert "not evidence about low-OSM tracts" in report["scope"]
    assert report["cdc_manifest"]["licence"].startswith("Public Domain")


def test_final_writeup_registers_source_and_limits_interpretation():
    text = (REPO / "docs/writeup/final-writeup.md").read_text()
    assert "CDC PLACES 2025, Texas census tracts" in text
    assert "36.9%" in text and "18.1%" in text
    assert "not evidence that low-OSM tracts are sicker" in text
    assert "Public Domain per Socrata metadata" in text
