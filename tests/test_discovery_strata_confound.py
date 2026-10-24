from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import discovery_strata_confound as discovery  # noqa: E402


def test_stratum_composition_is_region_weighted_and_missing_urbanity_excluded() -> None:
    rows = {region: [] for region in discovery.REGIONS}
    rows["eastern-ok"] = [
        {"GEOID": "a", "tribal_any": True, "rural": True},
        {"GEOID": "b", "tribal_any": False, "rural": False},
        {"GEOID": "c", "tribal_any": True, "rural": None},
    ]
    rows["south-central-tx"] = [
        {"GEOID": "d", "tribal_any": False, "rural": True},
    ]

    summary = discovery.summarize_composition(rows)
    compositions = summary["stratum_composition"]
    assert summary["total_tracts"] == 4
    assert summary["valid_tribal_tracts"] == 4
    assert summary["valid_urbanity_tracts"] == 3
    assert compositions["tribal_any"]["total_tracts"] == 2
    assert compositions["tribal_any"]["dominant_region"] == "eastern-ok"
    assert compositions["tribal_any"]["dominant_region_share"] == 1.0
    assert compositions["rural"]["total_tracts"] == 2
    assert compositions["rural"]["by_region"]["south-central-tx"]["share_of_stratum"] == 0.5
    assert summary["missing"]["pct_urban_by_region"]["eastern-ok"] == 1


def test_compositions_reconcile_region_and_stratum_margins() -> None:
    rows = {region: [] for region in discovery.REGIONS}
    rows["eastern-ok"] = [
        {"GEOID": "a", "tribal_any": True, "rural": True},
        {"GEOID": "b", "tribal_any": False, "rural": False},
    ]
    rows["maricopa-az"] = [
        {"GEOID": "c", "tribal_any": False, "rural": True},
    ]
    summary = discovery.summarize_composition(rows)
    regional = summary["regional_counts_and_prevalence"]
    compositions = summary["stratum_composition"]
    assert sum(item["official_sample_tracts"] for item in regional.values()) == 3
    assert sum(item["total_tracts"] for item in compositions.values()
               if item["label"] in ("tribal_any = true", "tribal_any = false")) == 3
    assert compositions["all_sample"]["total_tracts"] == 3
