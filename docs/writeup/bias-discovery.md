## Finding 1 — Camp Fire-area building-count mismatch; DINS mechanism check negative

### Impact and novelty

In six focal Camp Fire-area Butte County tracts, the provided USFS Wildfire Risk to Communities building count—a Microsoft-footprint derivative—is above the provided Overture count: the Overture/USFS ratios range from 0.620 to 0.747 (`reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`; `docs/decisions/building-component.md`). This is a geographic and temporal association, not evidence that fire damage caused a stale-reference discrepancy: the external DINS mechanism check below is negative. If a recovery or dispatch team treats the larger count as current, it could misdirect field-verification effort; that is a plausible decision risk, not an observed dispatch failure. This temporal mismatch is not a restatement of any marginal rural, SVI, tribal, or hazard-exposure scorecard cell.

### Evidence

All six GEOIDs have a provided MTBS last-wildfire year of 2018. The table compares challenge summaries with incident-filtered public CAL FIRE Camp Fire DINS inspection points. The California catalogue records CC BY (version unspecified); retrieved 2026-10-29 from [the source page](https://data.ca.gov/dataset/cal-fire-damage-inspection-dins-data). Only incident, damage class, county, object ID, and point geometry were requested; raw data and manifest remain outside git.

| GEOID | Overture / USFS | USFS − Overture | DINS destroyed | DINS major | Relative error |
|---|---:|---:|---:|---:|---:|
| `06007001800` | 0.620 | 666.4 | 3,314 | 3 | 0.799 |
| `06007002100` | 0.643 | 588.3 | 2,337 | 4 | 0.748 |
| `06007002000` | 0.662 | 579.5 | 2,870 | 4 | 0.798 |
| `06007001900` | 0.677 | 390.2 | 1,887 | 3 | 0.793 |
| `06007002300` | 0.720 | 668.9 | 2,386 | 2 | 0.720 |
| `06007002200` | 0.747 | 766.1 | 2,472 | 6 | 0.690 |

Ratio is Overture / `usfs_BuildingCount_sum`; relative error is `abs((USFS − Overture) − DINS destroyed) / DINS destroyed`. Values are in `reports/discovery-postfire.json#/focal_butte_2018_burn_geoids` and `reports/discovery-dins-mechanism.json#/focal_butte_tracts`; the workspace manifest pins the raw file.

Burn classes follow the report's 0–100% bins; tracts reported above 100% are omitted rather than clipped (`reports/discovery-postfire.json#/burn_definition`).

| Provided NorCal burn class | Tracts | Overture / USFS < 0.8 | Share below 0.8 |
|---|---:|---:|---:|
| 0% (unburned) | 449 | 7 | 1.6% |
| >0–5% | 28 | 0 | 0.0% |
| >5–20% | 32 | 0 | 0.0% |
| >20–<50% | 32 | 0 | 0.0% |
| 50–100% | 36 | 4 | 11.1% |

These provided-data class counts and proportions are at `reports/discovery-postfire.json#/northern_ca_burn_class_table`. The one map shows the burn geography and the six focal tracts:

![NorCal/Butte tract map: fill is provided MTBS burned percentage; the six focal tracts are marked.](../../reports/discovery-postfire-map.png)

The tract-bootstrap difference is +9.55 percentage points (95% CI [+0.55, +20.89]; 50,000 replicates; valid-replicate fraction 1.0). The county-cluster bootstrap 95% interval [-1.65, +16.67] includes zero, so the magnitude remains exploratory (`reports/discovery-postfire-county-bootstrap.json#/county_cluster_bootstrap/cluster_95_percentile_interval`).

### What this does and does not show

The preregistered DINS mechanism test is **negative**: 0/6 focal tracts met the relative-error threshold of ≤0.30 (the rule required at least 4/6), and Spearman was 0.4626068 across all 28 last-burn-2018 tracts, below the 0.60 threshold (`reports/discovery-dins-mechanism.json#/decision`, `#/spearman`). DINS therefore does not substantiate the hypothesised fire-damage/rebuilding mechanism. Across 591 NorCal tracts, 576 have positive Overture counts but no Camp Fire DINS record, totaling 1,139,778 buildings; because this is an inspection-limited, incident-specific source, a missing record cannot establish absence or rebuilding (`reports/discovery-dins-mechanism.json#/opposite_direction`). Focal MTBS percentages are cumulative (64.4–153.8%), and the count field's exact vintage is unverified. DINS is discovery-only; no removed reference was accessed. No external public data was used outside this DINS check; source-mix and strata checks use provided data only.

### Reproducibility

Rebuild the map/report with `python scripts/discovery_postfire_stale_reference.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire.json --map reports/discovery-postfire-map.png`; rebuild the county interval with `python scripts/audit_discovery_postfire_county_bootstrap.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire-county-bootstrap.json`. Reproduce DINS with `python scripts/discovery_dins_mechanism.py --dins-file "$COMPETITION_WORKSPACE/external-discovery-2026-10-29/camp-fire-dins.geojson" --out reports/discovery-dins-mechanism.json --markdown reports/discovery-dins-mechanism.md --scatter reports/discovery-dins-mechanism.png`. Inputs, filters, hashes, tract values, and the opposite-direction check are recorded in the report/manifest.

## Finding 2 — Rural Overture buildings are less often OSM-sourced

### Impact and novelty

In Maricopa, Northern California, Eastern Oklahoma, and South-Central Texas, a footprint that contains only geometry and lacks a reliable address, name, or use cannot by itself geocode an emergency dispatch. Lower community-sourced representation could therefore justify prioritising local address/attribute verification; that is a plausible audit risk, not an observed service failure. This is not a restatement of a marginal rurality/SVI scorecard cell.

### Evidence

Across Maricopa, Northern California, Eastern Oklahoma, and South-Central Texas, rural tracts have 16.83–38.68 percentage points lower OSM-touched building shares; all four tract-bootstrap intervals are below zero (`reports/source-provenance.json#/statistics`). SVI-quartile contrasts were smaller and less consistent (`#/uncertainty_and_limitations`).

### What this does and does not show

The source tags describe provenance, not correctness, completeness, or age. Rurality is not an independently estimated cause; SVI gradients vary and may be confounded. The report uses supplied Overture `sources`/`bbox` and provided tract geometry only, with no public external data or candidate changes (`reports/source-provenance.json#/source_definitions`, `#/data_and_restrictions`, `#/uncertainty_and_limitations`).

### Reproducibility

Rebuild with `python scripts/discovery_source_provenance.py --workspace "$COMPETITION_WORKSPACE" --out reports/source-provenance.json --figure reports/source-provenance.png`; estimates, intervals, source definitions, and hashes are in the report.

## Finding 3 — The tribal/non-tribal pooled comparison is region-confounded

### Impact and novelty

In Eastern Oklahoma and South-Central Texas, a pooled tribal/non-tribal scorecard contrast can misdirect a mapping audit if treated as a general tribal pattern when the groups have different regional mixes. This is a plausible audit risk, not an observed tribal disparity or causal effect. It is not a restatement of a marginal tribal scorecard cell.

### Evidence

In the official sample, 805 of 858 tracts marked `tribal_any` (93.82%, approximately 94%) are in Eastern Oklahoma, while 6,002 of 8,521 non-tribal tracts (70.44%, approximately 70%) are in South-Central Texas (`reports/discovery-strata-confound.json#/stratum_composition`). The breakdown is reproducible from `reports/discovery-strata-confound.json#/regional_counts_and_prevalence`.

### What this does and does not show

These counts establish sample composition, not an equity disparity, causal effect, or difference in hidden target scores. The `tribal_any` field is a provided overlap indicator, not a legal-status classification. The comparison should be region-stratified before operational conclusions (`reports/discovery-strata-confound.json#/regional_counts_and_prevalence`, `#/missing`).

### Reproducibility

Rebuild with `python scripts/discovery_strata_confound.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-strata-confound.json`; it joins official GEOIDs to provided strata and checks cohort accounting.
