## Finding 1 — Rural tracts have lower OSM-attributed shares of Overture buildings

### Why it matters

Across all four regions, the tract-level mean share of Overture buildings with an OSM source attribution is lower in rural than urban tracts. This is an upstream-source pattern in the provided data—not an organizer score comparison or evidence that a building is missing or wrong.

### Evidence

| Region | Rural mean | Urban mean | Rural − urban (percentage points) | 95% tract-bootstrap CI (pp) | n tracts (rural, urban) |
|---|---:|---:|---:|---:|---:|
| Eastern Oklahoma | 9.87% | 26.70% | −16.83 | [−19.24, −14.71] | 1,191 (411, 780) |
| Maricopa, Arizona | 49.20% | 87.38% | −38.17 | [−43.15, −33.28] | 1,582 (163, 1,419) |
| Northern California | 24.70% | 61.57% | −36.87 | [−41.94, −32.01] | 591 (147, 444) |
| South-Central Texas | 25.15% | 63.83% | −38.68 | [−40.74, −36.56] | 5,989 (991, 4,998) |

Values are copied from `reports/source-provenance.json` (SHA-256 `e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565`). Rural means `pct_urban < 0.5`, urban means `>= 0.5`; missing urbanity is excluded. Each group mean is the unweighted mean of tract OSM-attributed shares; the 95% intervals are 2,000 independent tract-bootstrap resamples of the rural-minus-urban contrast. All four intervals are below zero. This is descriptive and does not account for spatial dependence.

### Named high-SVI examples

The report's `named_lowest_osm_high_svi` lists contain the ten lowest non-null OSM-attributed shares among finite-SVI Q4 tracts in each region; Q4 is region-specific and examples are not all rural. The full GEOID, county, Census place, and report-derived share list is in [Appendix I](#appendix-i-named-high-svi-tracts-with-lowest-osm-attribution). Its metric values come from the source report cited above; county and place labels are geographic names only and do not change selection or metrics.

The potentially affected people are residents of rural, higher-vulnerability communities who may rely on local building context for heat, drought, wildfire, and evacuation planning; these data do not show missing buildings, inaccurate maps, or service harm, and dispatch/evacuation outcomes were not measured. Mappers should treat the named tracts as partner-led review priorities, consult local and tribal organizations, and verify proposed edits with local knowledge before changing map data.

**Limits and reproduction.** OSM attribution is provenance, not accuracy, completeness, age, or proof of local mapping activity; provider memberships may overlap. Metric inputs are the provided Overture source fields and provided strata; no removed reference layers, public scores, or scored-candidate inputs were used (`reports/source-provenance.json`, cited SHA above). Rebuild the source report with `uv run --python 3.12 --no-project --isolated --with-requirements requirements.txt python scripts/discovery_source_provenance.py --workspace "$COMPETITION_WORKSPACE" --out reports/source-provenance.json --figure reports/source-provenance.png`.

## Finding 2 — Rio Grande border metros: Google-sourced footprints mask a community-source gap

### Impact and novelty

Ten South Texas counties include 1.77 million residents and colonias. Prioritise partner-led verification; dispatch outcomes and OSM completeness were not measured.

### Evidence and mechanism

Across 460 border tracts, mean OSM-touched share was 9.18%, versus 61.48% in 5,541 others (68.66% in 4,600 urban others); Microsoft-only share averaged 67.48%. “Other” tags cover 254,346/886,361 footprints (28.70%); 290 tracts (1,183,693 people) are below 5% OSM. Difference: −52.30 pp (county-cluster 95% CI [−61.24, −36.99]); adjusted association −0.446 (SE 0.070; n=5,958, 185 counties), descriptive—not causal.

Border “Other” (254,346) nearly matches Google Open Buildings (254,872); non-border “Other” (977,049) nearly matches USGS lidar (976,523), a ±526 reconciliation supporting—but not proving—spill-over. County details are in [Appendix J](#appendix-j-supporting-discovery-tables). Webb/Starr/Maverick Overture/USFS medians (1.56/1.75/1.71) are context, not a Microsoft target proxy.

The [Appendix J](#appendix-j-supporting-discovery-tables) county table gives population, tract means/CIs and provided USFS context; small-county intervals are unstable and Google attribution remains regional.

Hidalgo `48215021906`, `48215023525`, and `48215024404` have zero OSM share and 1,727, 1,765, and 2,212 footprints; Cameron `48061012002` has 3,084 and 0.03% OSM. Heat averages 53.5 vs 7.3 WBGT>30°C days/year (218 vs 3,654 covered tracts). Prioritise local verification; dispatch effects are unmeasured.

Related entries: 34744 (colonias/dispatchability), 35071 (rural/tribal-SVI), 34972 (stale road edits), and 35333 (phantom roads). This analysis isolates Google building spill-over against the Microsoft comparator.

Report/input hashes: `reports/discovery-border-metros.json` (`27779986d787594abdadf741c983db13dedf25fb476cc4ff2ae096a1a4260244`); source report `reports/source-provenance.json` (`e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565`). SVI terms are in Appendix F. Rebuild: `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 python scripts/discovery/border_metros.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-border-metros.json`.

## Finding 3 — Paradise/Magalia building-count discrepancy

### Impact and evidence

In six Paradise/Magalia Butte County tracts, provided Wildfire Risk to Communities counts exceed Overture (ratios 0.620–0.747); the geographic/vintage discrepancy does not establish fire damage as its cause. Treating larger counts as current could misdirect field checks. Provided MTBS last-burn year is 2018; public CAL FIRE DINS is a discovery-only mechanism check (retrieved 2026-10-29; catalogue CC BY, version unspecified). Tract-level rows are in [Appendix J](#appendix-j-supporting-discovery-tables).

Inputs and focal GEOIDs are in `reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`; DINS tract rows are in `reports/discovery-dins-mechanism.json#/focal_butte_tracts` (both tables in [Appendix J](#appendix-j-supporting-discovery-tables)). The tract-bootstrap difference is +9.55 pp (95% CI [+0.55, +20.89]); the county-cluster CI [−1.65, +16.67] includes zero, so magnitude is exploratory (`reports/discovery-postfire-county-bootstrap.json#/county_cluster_bootstrap/cluster_95_percentile_interval`). The map marks the six tracts:

![NorCal/Butte tract map: fill is provided MTBS burned percentage; the six focal tracts are marked.](../../reports/discovery-postfire-map.png)

**Negative mechanism check.** DINS supports neither the proposed fire-damage nor rebuilding mechanism: 0/6 tracts met the ≤0.30 relative-error rule (required ≥4/6); Spearman was 0.463 across 28 2018-burn tracts, below 0.60. Among 591 NorCal tracts, 576 positive-count tracts have no DINS record (1,139,778 buildings); missing records do not establish absence or rebuilding. MTBS percentages are cumulative (64.4–153.8%); reference-count vintage is unverified. The border finding's SVI control is documented in Appendix F.

Reproduction: `scripts/discovery_postfire_stale_reference.py`, `scripts/audit_discovery_postfire_county_bootstrap.py`, and `scripts/discovery_dins_mechanism.py`; input paths, filters and hashes are recorded in the reports and workspace manifest.

## Finding 4 — The tribal/non-tribal pooled comparison is region-confounded

### Impact and novelty

A pooled tribal/non-tribal contrast in Eastern Oklahoma and South-Central Texas can reflect different regional mixes, not a general tribal pattern. This is a composition caveat, not a disparity or causal finding.

### Evidence

Of 858 `tribal_any` tracts, 805 (93.82%) are in Eastern Oklahoma; 6,002 of 8,521 non-tribal tracts (70.44%) are in South-Central Texas (`reports/discovery-strata-confound.json#/stratum_composition`).

### What this does and does not show

These are composition counts, not equity or target-score evidence. `tribal_any` is an overlap indicator, not legal status; comparisons should be region-stratified (`reports/discovery-strata-confound.json#/regional_counts_and_prevalence`, `#/missing`).

### Reproducibility

Rebuild: `python scripts/discovery_strata_confound.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-strata-confound.json`.
