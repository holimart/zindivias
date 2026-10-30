## Finding 1 — Rural tracts have lower OSM-attributed shares of Overture buildings

### Why it matters

What we found: in every scored region, rural buildings are far less often attributed to OpenStreetMap than urban buildings. Who it affects: rural residents in all four regions, including the high-vulnerability tracts named in Appendix I.

Across all four regions, rural tracts have lower mean OSM-attributed shares of Overture buildings than urban tracts. This is a provided-data source pattern, not an organizer-score comparison or evidence of missing or incorrect buildings.

### Evidence

Read the “Rural − urban” column: negative in every region, with every interval excluding zero.

| Region | Rural mean | Urban mean | Rural − urban (percentage points) | 95% tract-bootstrap CI (pp) | n tracts (rural, urban) |
|---|---:|---:|---:|---:|---:|
| Eastern Oklahoma | 9.87% | 26.70% | −16.83 | [−19.24, −14.71] | 1,191 (411, 780) |
| Maricopa, Arizona | 49.20% | 87.38% | −38.17 | [−43.15, −33.28] | 1,582 (163, 1,419) |
| Northern California | 24.70% | 61.57% | −36.87 | [−41.94, −32.01] | 591 (147, 444) |
| South-Central Texas | 25.15% | 63.83% | −38.68 | [−40.74, −36.56] | 5,989 (991, 4,998) |

Values are copied from `reports/source-provenance.json` (SHA-256 `e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565`). A tract is rural when the provided `pct_urban < 0.5` and urban when `>= 0.5`; tracts with missing `pct_urban` are excluded. Means are unweighted across tracts; intervals use 2,000 tract-bootstrap resamples. All CIs are below zero; no spatial adjustment was applied.

### Named high-SVI examples

The report lists, per region, the ten tracts with the lowest non-null OSM-attributed share among the most vulnerable quarter of tracts (finite SVI, Q4); not all are rural. Appendix I gives their GEOIDs, county and place names, and shares; the names only annotate the preselected tracts. Appendix K shows OSM share across provided-SVI quartiles.

Rural, higher-vulnerability residents may rely on building context for heat, drought, wildfire, and evacuation planning; missing buildings, inaccurate maps, service harm, and dispatch/evacuation outcomes were not measured. Mappers should treat the named tracts as partner-led review priorities and verify proposed edits with local and tribal knowledge.

**Limits and reproduction.** OSM attribution is provenance: it does not measure accuracy, completeness, age, or local mapping activity, and one footprint may list several providers. Only provided Overture source fields and strata were used; no removed reference layers, scores, or candidate outputs (`reports/source-provenance.json`, cited SHA). Rebuild with `uv run --python 3.12 --no-project --isolated --with-requirements requirements.txt python scripts/discovery_source_provenance.py --workspace "$COMPETITION_WORKSPACE" --out reports/source-provenance.json --figure reports/source-provenance.png`.

## Finding 2 — Rio Grande border metros: Overture's machine-only source mix

### Impact and novelty

What we found: in the Rio Grande border metros, Overture's building footprints are overwhelmingly machine-sourced, and a third provider tag (read as Google Open Buildings) appears there but not in the non-border controls. Who it affects: the mostly urban residents of ten Texas border counties, where about one adult in three is uninsured; dispatch and evacuation outcomes were not measured.

Low OSM share alone is not the finding: the non-border control Ector has an even lower OSM share than El Paso (6.7% vs 9.3%) and 47.7% Esri, but zero share of the border provider tag. We measure source, not accuracy.

CDC PLACES 2025 estimates that 36.9% of adults aged 18–64 in these ten border counties are uninsured (about one adult in three), versus 18.1% elsewhere in the South-Central Texas scored region (difference +18.8 pp; county-cluster 95% CI +16.6 to +22.1 pp; 455 joined border tracts). This describes the border population where the place effect occurs; it is not evidence that low-OSM tracts are sicker. Health outcomes and dispatch/evacuation impacts were not measured (`reports/who-is-affected-cdc-places.json#/contrasts/ten border counties vs rest of South-Central Texas`).

### Evidence and mechanism

Across the 460 border tracts the mean OSM share is 9.18%, against 61.48% elsewhere in the region (68.66% among urban tracts elsewhere); difference −52.30 pp (95% county-cluster CI −61.24 to −36.99). Adjusted for Hispanic share and urbanity, the border coefficient is β=−0.446 (SE 0.070; n=5,958, 185 counties); descriptive, not causal. County counts are in Appendix J.

`Other` is an Overture `sources` label, not removed Google Open Buildings or USGS reference data. By count matching, the border `other` count matches the first provider and the non-border Texas `other` count the second (both within ±526; Bexar cache share 0.336). This inference is supporting evidence, not proof (Appendix J). The Hispanic-share control comes from the CDC/ATSDR SVI layer in Appendix F.

### Does it transfer?

We repeated the source-mix count in five counties outside the scored regions. “Google-only” denotes an Overture provider tag, not a removed reference layer: 10.8–33.0% of footprints in the three unscored border counties, zero in both non-border controls. El Paso's OSM and provider-tag shares (9.3%/33.0%) match the scored Rio Grande OSM range (4.2–12.0%); Doña Ana has 10.8% provider tag and 26.1% Esri. No interval is reported.

Footprint shares by source; the bold third-provider column is positive in the border counties and zero in both controls.

| County | OSM | Third-provider tag | Esri |
|---|---:|---:|---:|
| **El Paso TX** | 0.093 | **0.330** | 0.000 |
| **Imperial CA** | 0.200 | **0.292** | 0.000 |
| **Doña Ana NM** | 0.179 | **0.108** | 0.261 |
| Ector TX (control) | 0.067 | **0.000** | 0.477 |
| Bernalillo NM (control) | 0.809 | **0.000** | 0.000 |

#35031/#35474 describe an Esri county data-sharing mechanism in Oklahoma; it coexists with ours: Esri is 47.7% in Ector and 26.1% in Doña Ana.

Full results and the rebuild command: [Appendix J](#appendix-j-supporting-discovery-tables). Manifest: `$COMPETITION_WORKSPACE/external-discovery-2026-10-29-fable/MANIFEST.json`; rerun report `reports/discovery-border-transfer.json` (SHA-256 `64037e7360dab85eae3f748ec5d0ec14155999587302ae12e83bd3f9a893b795`). No scores or removed references.

## Finding 3 — Paradise/Magalia building-count discrepancy

### Impact and evidence

What we found: in six burned Paradise/Magalia tracts the provided reference building count is well above Overture's, and both counts share a Microsoft-derived lineage, so the gap looks like a vintage difference within one provider, not missing OpenStreetMap mapping. Who it affects: planners who treat the larger count as current could misdirect field checks; we could not confirm Camp Fire damage as the cause.

In six Paradise/Magalia tracts (Butte County), the provided Wildfire Risk to Communities (WRC) building count exceeds the Overture count: Overture/WRC ratios are 0.620–0.747. This geographic and vintage discrepancy does not establish fire damage as its cause. The provided MTBS last-burn year is 2018. The public CAL FIRE Damage Inspection (DINS) dataset is used only as a discovery-stage mechanism check (retrieved 2026-10-29; catalogue CC BY, version unspecified).

Tract-level rows (focal GEOIDs, ratios and DINS counts) are in [Appendix J](#appendix-j-supporting-discovery-tables) (`reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`; `reports/discovery-dins-mechanism.json#/focal_butte_tracts`). The tract-bootstrap difference is +9.55 pp (95% CI [+0.55, +20.89]), where the difference is the share of high-burn Northern California tracts (50–100% MTBS burned) whose Overture/WRC count ratio is below 0.8 minus the same share among unburned (0% burned) tracts; the county-cluster bootstrap 95% CI [-1.65, +16.67] includes zero, so the magnitude is exploratory (`reports/discovery-postfire-county-bootstrap.json#/county_cluster_bootstrap/cluster_95_percentile_interval`). The map marks these six tracts:

![NorCal/Butte tract map: fill is provided MTBS burned percentage; the six focal tracts are marked.](../../reports/discovery-postfire-map.png)

Map: fill is the provided MTBS burned percentage; the six focal tracts are marked.

**Negative mechanism check.** The DINS damage inspections support neither the fire-damage nor the rebuilding explanation: 0/6 focal tracts met the ≤0.30 relative-error rule (pass mark ≥4/6), and the Spearman correlation was 0.463 across 28 2018-burn tracts, below the 0.60 threshold. Among 591 NorCal tracts, 576 tracts with positive counts have no DINS record (1,139,778 buildings); a missing record does not establish absence or rebuilding. MTBS burned percentages are cumulative (64.4–153.8%); the reference count's vintage is unverified.

Why a vintage gap inside one provider: focal Microsoft-only shares are 0.430–0.990 versus OSM-only 0.001–0.089, and the Overture/WRC ratios of 0.620–0.747 are measured against a WRC count that is itself Microsoft-derived, so the gap is not OSM conflation (`reports/postfire-lineage-recheck.json#/focal_share_ranges`; `reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`). Pooled OLS finds no OSM-only effect: β(OSM-only)=−0.016 (county-clustered SE 0.041; n=9,337; controls: urbanity, log density, region FE). Seven unburned NorCal tracts also have ratio <0.8, all urban (six Sacramento, one Placer; 0.711–0.775), so low ratios are not confined to burned tracts or to Sacramento (`reports/postfire-lineage-recheck.json#/pooled_ols`, `#/unburned_northern_ca_ratio_below_0_8`).

Entries #35520 and #35527 (23 Oct) provide stronger DINS-matched wildfire evidence; we acknowledge that work and limit Finding 3 here to the separate provider-lineage result and negative mechanism check ([related-entry omissions ledger](related-entries.md)).

Reproduce with `scripts/discovery_postfire_stale_reference.py`, `scripts/audit_discovery_postfire_county_bootstrap.py`, and `scripts/discovery_dins_mechanism.py`; inputs, filters and hashes are recorded in reports/manifests.

## Finding 4 — The tribal/non-tribal pooled comparison is region-confounded

### Impact and novelty

What we found: the pooled tribal/non-tribal comparison mostly compares Eastern Oklahoma with Texas, because almost all tribal-flagged tracts are in Oklahoma. Who it affects: anyone reading the scorecard's tribal row as a general tribal disparity; compare within regions instead.

Regional composition can explain the pooled tribal/non-tribal contrast; this is not evidence of a general tribal disparity or causation.

### Evidence

Of 858 `tribal_any` tracts, 805 (93.82%) are in Eastern Oklahoma; 6,002 of 8,521 non-tribal tracts (70.44%) are in South-Central Texas (`reports/discovery-strata-confound.json#/stratum_composition`).

### What this does and does not show

These are composition counts, not equity or target-score evidence. `tribal_any` is an overlap indicator, not legal status; comparisons should be region-stratified (`reports/discovery-strata-confound.json#/regional_counts_and_prevalence`, `#/missing`).

### Reproducibility

Rebuild: `python scripts/discovery_strata_confound.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-strata-confound.json`.
