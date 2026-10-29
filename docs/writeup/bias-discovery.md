## Finding 1 — Rural tracts have lower OSM-attributed shares of Overture buildings

### Why it matters

Across all four regions, rural tracts have lower mean OSM-attributed shares of Overture buildings than urban tracts. This is a provided-data source pattern, not an organizer-score comparison or evidence of missing or incorrect buildings.

### Evidence

| Region | Rural mean | Urban mean | Rural − urban (percentage points) | 95% tract-bootstrap CI (pp) | n tracts (rural, urban) |
|---|---:|---:|---:|---:|---:|
| Eastern Oklahoma | 9.87% | 26.70% | −16.83 | [−19.24, −14.71] | 1,191 (411, 780) |
| Maricopa, Arizona | 49.20% | 87.38% | −38.17 | [−43.15, −33.28] | 1,582 (163, 1,419) |
| Northern California | 24.70% | 61.57% | −36.87 | [−41.94, −32.01] | 591 (147, 444) |
| South-Central Texas | 25.15% | 63.83% | −38.68 | [−40.74, −36.56] | 5,989 (991, 4,998) |

Values are copied from `reports/source-provenance.json` (SHA-256 `e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565`). Rural/urban use `pct_urban < 0.5`/`>= 0.5`; missing is excluded. Means are unweighted across tracts; intervals use 2,000 tract-bootstrap resamples. All CIs are below zero; no spatial adjustment.

### Named high-SVI examples

The report's `named_lowest_osm_high_svi` lists contain the ten lowest non-null OSM-attributed shares among finite-SVI Q4 tracts in each region; examples are not all rural. Full GEOIDs, county/place labels, and shares are in [Appendix I](#appendix-i-named-high-svi-tracts-with-lowest-osm-attribution); labels do not change selection or metrics. The provided-SVI gradient is detailed in [Appendix K](#appendix-k-san-antonio-area-svi-gradient).

Rural, higher-vulnerability residents may rely on building context for heat, drought, wildfire, and evacuation planning; missing buildings, inaccurate maps, service harm, and dispatch/evacuation outcomes were not measured. Mappers should treat the named tracts as partner-led review priorities and verify proposed edits with local and tribal knowledge.

**Limits and reproduction.** OSM attribution is provenance, not accuracy, completeness, age, or local mapping activity; provider memberships may overlap. Only provided Overture source fields and strata were used; no removed reference layers, scores, or candidate outputs (`reports/source-provenance.json`, cited SHA). Rebuild with `uv run --python 3.12 --no-project --isolated --with-requirements requirements.txt python scripts/discovery_source_provenance.py --workspace "$COMPETITION_WORKSPACE" --out reports/source-provenance.json --figure reports/source-provenance.png`.

## Finding 2 — Rio Grande border metros: machine-only buildings with a Google third-provider spill-over

### Impact and novelty

Lead: border metros have machine-only footprints with Google Open Buildings as a cross-border third provider—not merely low OSM. Non-border Ector has lower OSM than El Paso (6.7% vs 9.3%), 47.7% Esri and no Google-only; source, not accuracy, is measured.

Ten border counties, 1.77M residents; impacts unmeasured.

### Evidence and mechanism

Across 460 border tracts, OSM mean is 9.18% vs 61.48% elsewhere (68.66% urban); difference −52.30 pp (95% county-cluster CI −61.24 to −36.99), adjusted β=−0.446 (SE 0.070; n=5,958, 185 counties), descriptive; full counts in Appendix J.

`Other` means Google Open Buildings in the border reconciliation and USGS lidar elsewhere (Bexar cache share 0.336), as suggested by ±526 count matches; this is not proof (Appendix J).

### Does it transfer?

Google-only is 10.8–33.0% in all three unscored border counties and zero in both controls. El Paso OSM/Google (9.3%/33.0%) matches the scored Rio Grande OSM range (4.2–12.0%); Doña Ana fades to 10.8% Google/26.1% Esri. Five counties; no interval.

| County | OSM | Google-only | Esri |
|---|---:|---:|---:|
| **El Paso TX** | 0.093 | **0.330** | 0.000 |
| **Imperial CA** | 0.200 | **0.292** | 0.000 |
| **Doña Ana NM** | 0.179 | **0.108** | 0.261 |
| Ector TX (control) | 0.067 | **0.000** | 0.477 |
| Bernalillo NM (control) | 0.809 | **0.000** | 0.000 |

Full results and reproduction: [Appendix J](#appendix-j-supporting-discovery-tables).

#35031/#35474's Oklahoma Esri county-sharing mechanism coexists: Esri is 47.7% in Ector and 26.1% in Doña Ana.

Manifest: `$COMPETITION_WORKSPACE/external-discovery-2026-10-29-fable/MANIFEST.json`; rerun report `reports/discovery-border-transfer.json` (SHA-256 `64037e7360dab85eae3f748ec5d0ec14155999587302ae12e83bd3f9a893b795`); command in Appendix J. No scores or removed references.

## Finding 3 — Paradise/Magalia building-count discrepancy

### Impact and evidence

In six Paradise/Magalia Butte County tracts, provided Wildfire Risk to Communities counts exceed Overture (ratios 0.620–0.747); the geographic/vintage discrepancy does not establish fire damage as its cause. Treating larger counts as current could misdirect field checks. Provided MTBS last-burn year is 2018; public CAL FIRE DINS is a discovery-only mechanism check (retrieved 2026-10-29; catalogue CC BY, version unspecified). Tract-level rows are in [Appendix J](#appendix-j-supporting-discovery-tables).

Focal GEOIDs and DINS rows appear in [Appendix J](#appendix-j-supporting-discovery-tables) (`reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`; `reports/discovery-dins-mechanism.json#/focal_butte_tracts`). The tract-bootstrap difference is +9.55 pp (95% CI [+0.55, +20.89]); county-cluster CI [−1.65, +16.67] includes zero, so magnitude is exploratory (`reports/discovery-postfire-county-bootstrap.json#/county_cluster_bootstrap/cluster_95_percentile_interval`). The map marks these six tracts:

![NorCal/Butte tract map: fill is provided MTBS burned percentage; the six focal tracts are marked.](../../reports/discovery-postfire-map.png)

**Negative mechanism check.** DINS supports neither the proposed fire-damage nor rebuilding mechanism: 0/6 tracts met the ≤0.30 relative-error rule (required ≥4/6); Spearman was 0.463 across 28 2018-burn tracts, below 0.60. Among 591 NorCal tracts, 576 positive-count tracts have no DINS record (1,139,778 buildings); missing records do not establish absence or rebuilding. MTBS percentages are cumulative (64.4–153.8%); reference-count vintage is unverified. The border finding's SVI control is documented in Appendix F.

Focal shares show Microsoft-only 0.430–0.990 versus OSM-only 0.001–0.089; Overture/WRC ratios 0.620–0.747 against the provided Microsoft-derived USFS WRC count support a within-provider vintage gap, not OSM conflation (`reports/postfire-lineage-recheck.json#/focal_share_ranges`; `reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`). Pooled OLS gives β(OSM-only)=−0.016 (county-clustered SE 0.041; n=9,337; controls: urbanity, log density, region FE); seven unburned NorCal tracts have ratio <0.8, all urban—six Sacramento, one Placer (0.711–0.775)—so not all Sacramento (`reports/postfire-lineage-recheck.json#/pooled_ols`, `#/unburned_northern_ca_ratio_below_0_8`).

Entries #35520 and #35527 (23 Oct) provide stronger DINS-matched wildfire evidence; we acknowledge that work and limit Finding 3 here to the separate provider-lineage result and negative mechanism check ([related-entry omissions ledger](related-entries.md)).

Reproduce with `scripts/discovery_postfire_stale_reference.py`, `scripts/audit_discovery_postfire_county_bootstrap.py`, and `scripts/discovery_dins_mechanism.py`; inputs, filters and hashes are recorded in reports/manifests.

## Finding 4 — The tribal/non-tribal pooled comparison is region-confounded

### Impact and novelty

Regional composition can explain the pooled tribal/non-tribal contrast; this is not evidence of a general tribal disparity or causation.

### Evidence

Of 858 `tribal_any` tracts, 805 (93.82%) are in Eastern Oklahoma; 6,002 of 8,521 non-tribal tracts (70.44%) are in South-Central Texas (`reports/discovery-strata-confound.json#/stratum_composition`).

### What this does and does not show

These are composition counts, not equity or target-score evidence. `tribal_any` is an overlap indicator, not legal status; comparisons should be region-stratified (`reports/discovery-strata-confound.json#/regional_counts_and_prevalence`, `#/missing`).

### Reproducibility

Rebuild: `python scripts/discovery_strata_confound.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-strata-confound.json`.
