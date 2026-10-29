# Best Bias Discovery: post-fire building-count disagreement may look like under-mapping

**Status:** the post-fire disagreement and Overture source-provenance findings below use challenge-provided inputs; additional public context layers are screened in Section 4 under the discovery-prize data policy. None is a claim about hidden organiser scores, the exact reference vintage, or measured emergency outcomes. The high-burn versus unburned direction is repeatable, but its quantitative size is exploratory once counties rather than tracts are resampled, as recorded below.

## Impact

In a set of six Butte County tracts in the Paradise/Magalia Camp Fire focus identified by the discovery brief, the provided Overture building count is 0.620–0.747 of the provided USFS Wildfire-Risk-to-Communities `usfs_BuildingCount_sum`. The provided MTBS attributes report a 2018 last-burn year for all six. The six tracts' provided `pop_total` sums to 6,977; this is a tract-population total, not a count of people confirmed displaced or affected.

If emergency or recovery planners interpret a building-count comparison as current map completeness when the reference still reflects pre-fire structures, destroyed structures can appear to be unmapped buildings. That mismatch could distort address inventories, dispatch geocoding, evacuation planning, or recovery-resource estimates. These are plausible risks, not observed dispatch failures or evidence about which source is operationally current. Fixed rural/wildfire strata alone cannot distinguish stale reference structures from genuine missing current map features.

## Novelty

This is a temporal source-provenance interaction: post-fire burn history is associated with disagreement between two provided building summaries. It is not simply a marginal rural, wildfire-risk, or SVI comparison. The result is consistent with the organizer's example class of post-disaster rebuilt areas and source behavior, but does not establish that the hidden score uses the USFS field or that it labels these tracts as coverage gaps.

## Evidence

The comparison is exactly `Overture overture_buildings / provided strata usfs_BuildingCount_sum`; ratio `<0.8` is a descriptive cutoff, not an organiser score. The table below comes from the authoritative 591-row NorCal sample. Camp Fire names are contextual labels supplied with the discovery lead; the provided columns themselves identify GEOIDs, county, burn percentage, and last-burn year, not fire incident names.

| Butte County GEOID | Overture / provided USFS count | Provided MTBS burned % | Last burn year | `pop_total` |
|---|---:|---:|---:|---:|
| `06007001800` | 0.620 | 100.0 | 2018 | 840 |
| `06007002100` | 0.643 | 100.1 | 2018 | 926 |
| `06007002000` | 0.662 | 100.0 | 2018 | 886 |
| `06007001900` | 0.677 | 100.3 | 2018 | 675 |
| `06007002300` | 0.720 | 64.4 | 2018 | 1,851 |
| `06007002200` | 0.747 | 153.8 | 2018 | 1,799 |

The stronger stale-reference interpretation was directly tested using public Camp Fire DINS evidence; it did not meet the registered mechanism criteria. The bounded DINS follow-up and its claim pointers are at the end of this discovery section.

Two additional supplied examples are `06089010603` (ratio 0.691; MTBS last burn 2018) and `06033000704` (0.798; last burn 2017). The provided cumulative burned-area percentage can exceed 100 after repeated burns; values above 100 are kept in the named examples but excluded from the mutually exclusive 0–100% burn-class comparison.

| Provided NorCal burn class | Tracts | Ratio <0.8 | Share below 0.8 |
|---|---:|---:|---:|
| 0% (unburned) | 449 | 7 | 1.6% |
| >0–5% | 28 | 0 | 0.0% |
| >5–20% | 32 | 0 | 0.0% |
| >20–<50% | 32 | 0 | 0.0% |
| 50–100% | 36 | 4 | 11.1% |

Fourteen of 591 tracts have provided burned percentages above 100 and are excluded from these bins; none are missing a burn percentage. The high-burn minus unburned difference is **+9.55 percentage points**, and its 95% percentile interval is **[+0.55, +20.89] percentage points** under the published stratified 50,000-draw tract bootstrap but **[-1.65, +16.67] percentage points** when whole counties are resampled instead of tracts; under the criterion preregistered before any result was seen, the difference is therefore **exploratory** and is not established at 95% confidence. The two intervals answer different questions and are not interchangeable: the tract interval measures sensitivity to tract-level resampling of the binary ratio indicator at a fixed county composition, and because tracts inside one county share Overture provider mix, terrain and reference vintage it understates county-level dependence, so it is not the interval to quote when counties rather than tracts are the independent units; it also captures none of the spatial dependence, measurement error, source-vintage uncertainty, or hidden-target error. Butte County has 54 sample tracts, six below the ratio cutoff; their summed `pop_total` is 6,977.

That published interval resamples tracts independently. Tracts inside one county share Overture provider mix, terrain and reference vintage, so it understates county-level dependence, and the same statistic was rechecked with a county-cluster bootstrap that was preregistered before any result was seen (`reports/discovery-postfire-county-bootstrap.json`, `scripts/audit_discovery_postfire_county_bootstrap.py`): 50,000 seeded replicates resample all 23 counties of the analysis population with replacement, every tract record of a selected county is kept, and the difference is recomputed inside each replicate. The point estimate is unchanged, every replicate contained both focal groups (valid-replicate fraction 1.0), and 95.99% of valid replicates point the same way, but the 95% percentile interval widens from [+0.55, +20.89] to [-1.65, +16.67] percentage points, so its lower bound is no longer above zero. Under the preregistered criterion the quantitative difference is therefore **exploratory**: the direction is repeatable in almost every county resample, while this sample does not support it at 95% confidence once counties, rather than tracts, are treated as the independent units. The widening is not the artefact of one influential county: dropping any single county one at a time leaves the point difference between +5.56 and +10.71 percentage points, and the 36 high-burn tracts sit in 15 of the 23 counties. This remains a descriptive statement about disagreement between two provided layers; it does not establish cause, and it is not evidence that either layer is wrong, that any map is stale, or that any response or service differs.

![NorCal and Butte tract map: fill is provided MTBS burn percentage; purple outline marks Overture/USFS ratio below 0.8. The six numbered Butte markers follow the GEOID table order.](../../reports/discovery-postfire-map.png)

**Source/vintage caution:** the compared count is the provided strata field `usfs_BuildingCount_sum`; its supplied edition metadata is recorded in `reports/discovery-postfire.json`. The observed ratios support the hypothesis that a 2018-vintage Microsoft-footprint derivative may be stale after wildfire, leaving a risk-building count above the Overture count. The tract-level analysis does not independently verify the supplied count's exact vintage. It does **not** claim that the organiser used a 2018 Microsoft release or this USFS field to construct its hidden target.

## Reproducibility

The script `scripts/discovery_postfire_stale_reference.py` reads the provided NorCal Overture feature cache, only selected fields from the provided strata table, GEOID values from the official sample, and GEOID/geometry columns from the challenge-provided ACS geometry object. It does not read placeholder scores, labels, removed reference layers, or DINS/external evidence. The script reproduces the ratio table, burn-class table, bootstrap CI, Butte population sum, and map; the deterministic report is `reports/discovery-postfire.json`, and the map is `reports/discovery-postfire-map.png`. The county-cluster recheck is a separate deterministic script with its own preregistered configuration, `scripts/audit_discovery_postfire_county_bootstrap.py`, whose report is `reports/discovery-postfire-county-bootstrap.json`:

```sh
COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" \
  /home/martin/hddProjects/langywrap/uv run --quiet \
  --with numpy==2.5.4 --with pyarrow==26.0.0 \
  python scripts/audit_discovery_postfire_county_bootstrap.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/discovery-postfire-county-bootstrap.json
```

Two runs of that command produced byte-identical reports, and the audit fails closed if a county is missing from the resampling pool, if the resampled clusters are not whole counties, or if the writeup fails to state the robustness verdict; it re-runs the published tract-level design as a reference control and reproduces its frozen interval exactly, and it never re-tunes the ratio cutoff or drops a county. No scored candidate CSV was changed; their hashes are captured in the report.

From the competition repository with the authorized workspace and network access to the provided challenge geometry object:

```sh
MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig \
  /home/martin/hddProjects/langywrap/uv run \
  --with pyarrow --with numpy --with matplotlib --with shapely --with pyproj \
  python scripts/discovery_postfire_stale_reference.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/discovery-postfire.json \
  --map reports/discovery-postfire-map.png
```

A repeated run produced byte-identical JSON and PNG in the recorded environment for this provided-data analysis; it uses no external data. The separate public DINS mechanism test above is documented in `reports/discovery-dins-mechanism.md` and its workspace manifest, and does not alter this base analysis or any scored candidate. All statements about emergency-planning consequences are hypothetical, and the pattern is descriptive rather than causal.

## Section 2 — Scorecard strata are strongly region-composition confounded

### Impact

An all-region tribal-versus-non-tribal or rural-versus-urban comparison can be mistaken for a coverage disparity when its groups have very different regional composition. That can misdirect mapping audits or investment: an apparent tribal “priority” could largely track Eastern Oklahoma rather than represent tribal communities across regions, while the much larger non-tribal group mostly represents South-Central Texas. If regional road-taxonomy differences affect the road metric, that artifact could likewise divert route-mapping work from communities with actual access needs. These are plausible decision risks, not observed dispatch failures or evidence of a true coverage gap.

### Novelty

The automated scorecard reports marginal tribal and rural comparisons but does not make region composition the comparison itself. The provided sample shows that `tribal_any` is nearly an Eastern Oklahoma indicator, while the non-tribal comparison is dominated by Texas. The official rules also warn that Overture/TIGER named-road length ratios vary from 0.71 to 1.59 across regions because primary/secondary conventions differ. Those facts make a pooled road-equity result vulnerable to both stratum-mix and taxonomy effects. They do not establish the size or direction of a hidden-score disparity.

### Evidence

The script joins only official sample GEOIDs to the provided `tribal_any` and `pct_urban` strata. Composition cells below are `tract count (% of that row's stratum)`; the sample row is the 9,379-tract denominator. Rural is strictly `pct_urban < 0.5`, urban is `>=0.5`.

| Stratum | Total | Eastern OK | Maricopa AZ | Northern CA | South-Central TX |
|---|---:|---:|---:|---:|---:|
| All official sample tracts | 9,379 | 1,192 (12.71%) | 1,593 (16.98%) | 591 (6.30%) | 6,003 (64.00%) |
| `tribal_any = true` | 858 | 805 (93.82%) | 37 (4.31%) | 15 (1.75%) | 1 (0.12%) |
| `tribal_any = false` | 8,521 | 387 (4.54%) | 1,556 (18.26%) | 576 (6.76%) | 6,002 (70.44%) |
| Rural, `pct_urban < 0.5` | 1,712 | 411 (24.01%) | 163 (9.52%) | 147 (8.59%) | 991 (57.89%) |
| Urban, `pct_urban >= 0.5` | 7,641 | 780 (10.21%) | 1,419 (18.57%) | 444 (5.81%) | 4,998 (65.41%) |

Within-region tribal prevalence is 67.53% in Eastern Oklahoma, 2.32% in Maricopa, 2.54% in Northern California, and 0.017% in South-Central Texas. The official sample contains 858 `tribal_any` tracts, of which 805 (93.82%) are in Eastern Oklahoma; 6,002 of 8,521 non-tribal tracts (70.44%) are in South-Central Texas. Thus a pooled tribal contrast is also a comparison of sharply different region mixtures. The rural group is less concentrated but still 57.89% Texas; 26 official tracts have missing `pct_urban` and were excluded rather than imputed.

The earlier backlog measurement listed rural counts of 412/174/147/1,005 for Oklahoma/Arizona/California/Texas. Those counts equal the strict threshold counts above plus the corresponding missing `pct_urban` rows (1/11/0/14). A missing value is not `<0.5`, so this report does not silently treat it as rural. Input paths, hashes, exact selected columns, counts and the region-composition table are in `reports/discovery-strata-confound.json`.

The rules disclose a 0.71–1.59 cross-region Overture/TIGER named-road ratio range but do not map the endpoints to individual regions. The completed provided-Overture route audit (`reports/transport-route-ref-audit.json`) measures `sum(L_named)/sum(L_route)` as 1.929 in Maricopa, 1.504 in Northern California, 1.161 in Eastern Oklahoma, and 1.183 in South-Central Texas; the lane's broader 0.6–1.7 acceptance band is met in three regions, with Maricopa remaining high under the declared route-network filter. These are aggregate taxonomy diagnostics only: they use no TIGER observations, labels, LB feedback, or per-tract hidden scores and do not identify which regions correspond to the organizer's undisclosed ratio endpoints. The earlier statement that route ratios were unavailable is superseded by this audit; the Maricopa filter/coverage limitation remains documented there.

### Reproducibility

`scripts/discovery_strata_confound.py` reads only `GEOID` from each official sample CSV and `GEOID`, `tribal_any`, and `pct_urban` from each provided strata Parquet. It excludes the seven Texas strata rows outside the official sample and does not use sample score-placeholder values, candidate scores, labels, external data, or removed road references. Reproduce with:

```sh
/home/martin/hddProjects/langywrap/uv run --with pyarrow==26.0.0 \
  python scripts/discovery_strata_confound.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/discovery-strata-confound.json
```

The script asserts 9,379 unique official IDs and deterministic cohort accounting. A repeated run produced byte-identical JSON. This establishes composition confounding in the provided sample; it does not estimate hidden coverage scores or validate the scorecard's accuracy.

## Secondary exploratory lead — rural × SVI minority-domain screen in South-Central Texas

**Draft for operator review; not a claim about true organiser scores.** The competition has no training labels, and the reference layers used to calculate the hidden target are removed. Every score below is a C2/C3 provided-data proxy. The analysis identifies a subgroup for follow-up; it does not establish an observed real-world mapping disparity or causation.

## Proposed pattern outside the published scorecard grid

The official scorecard description lists five equity metrics across separate dimensions such as rural/urban, SVI, CVI, tribal status, and hazard exposure. It does not specify a joint cell for rural tracts in the upper quartile of the provided `svi_minority` domain field. This draft examines that intersection within the South-Central Texas sample, rather than restating either marginal rural/urban or overall-SVI comparisons.

The analytic group is defined using only provided strata fields: `pct_urban < 0.5` defines the rural screen; among rural sample tracts with nonmissing `svi_minority`, the top quartile is `svi_minority >= 0.6549`. The threshold is computed within this region's rural tracts; it is not a published competition cutpoint. Four of 991 rural tracts lacked `svi_minority` and were excluded. This field is used as supplied and is not a claim about individual residents or legal tribal status.

## Quantified proxy evidence

| Group | Tracts | Mean C2 predicted composite |
|---|---:|---:|
| Rural, `svi_minority >= 0.6549` | 248 | 0.06522 |
| Other rural tracts with the field | 739 | 0.03034 |
| Difference (upper-quartile minus comparison) | — | **+0.03488** |

A 50,000-resample tract bootstrap gives a 95% percentile interval of **[+0.01676, +0.05509]** for the C2 difference. Under the preregistered C3 sensitivity candidate (all expected-reference proxies scaled by 1.25), the group means are 0.08578 vs 0.03931, difference **+0.04647**, interval **[+0.02678, +0.06808]**. These intervals reflect resampling variability among tract-level predictions only, not error in the estimator or uncertainty about the hidden targets. Spatial correlation is not modeled.

The C2 component comparison uses only tracts where that component is defined:

| Component | Upper quartile (n; mean) | Other rural (n; mean) | Difference | Bootstrap 95% interval |
|---|---:|---:|---:|---:|
| Transport | 210/248; 0.03353 | 657/739; 0.01533 | +0.01820 | [-0.00192, +0.04114] |
| Buildings | 248/248; 0.02856 | 739/739; 0.00000 | +0.02856 | [+0.01187, +0.04828] |
| POI | 248/248; 0.11783 | 739/739; 0.07153 | +0.04630 | [+0.01961, +0.07450] |

In this proxy screen, the difference is concentrated in building and POI gaps; the transport interval includes zero. The result is a hypothesis about the model's provided-data screen, not evidence that the organiser's score is higher in these tracts.

The highest C2 predictions in the upper-quartile group include these named GEOIDs (all have provided `pct_urban = 0.0`):

| South-Central TX GEOID | `svi_minority` | C2 composite | C2 transport | C2 building | C2 POI |
|---|---:|---:|---:|---:|---:|
| `48253020102` | 0.8039 | 0.9701 | 0.0000 | 0.9691 | 0.9711 |
| `48001950402` | 0.7942 | 0.9599 | 1.0000 | 0.9239 | 0.9558 |
| `48169980000` | 0.9353 | 0.9396 | 0.0000 | 0.9775 | 0.9018 |
| `48001950401` | 0.8334 | 0.9392 | 1.0000 | 0.8658 | 0.9517 |
| `48185180400` | 0.7483 | 0.8739 | 0.0000 | 0.8261 | 0.9217 |

The exact inputs, estimates, flags, group definitions, bootstrap seeds, and all top-10 candidate tracts are in `reports/discovery-rural-svi-minority.json`.

## Why this may matter; what it does not show

The official challenge describes South-Central Texas as a heat/drought region along I-35/I-37. If the building/place deficits suggested by this screen are present in operational maps, incomplete addressable-building and place information could make dispatch geocoding, locating services, evacuation planning, or relief routing less reliable for affected rural tracts. The named GEOIDs let organizers or local reviewers inspect these places using authorized data. No emergency incident records, dispatch outcomes, or observed routing failures were available or analyzed, so these consequences remain plausible risks—not measured impacts.

The predictor does not use `svi_minority`, but it is still a heuristic based on Overture summaries and population/land/urbanity covariates. It has no label validation; reported score values must not be interpreted as organiser targets. The interval does not capture estimator or spatial uncertainty. The group is descriptive, and the cutoff should be checked with stakeholders before making a stronger narrative. No additional public data was used, and nothing from this discovery was added to the scored candidate CSVs.

## Reproduction

```sh
/home/martin/hddProjects/langywrap/uv run --with pyarrow --with numpy \
  python scripts/discovery_rural_svi_minority.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/discovery-rural-svi-minority.json
```

The 50,000-draw bootstrap uses fixed seeds; a second run generated byte-identical JSON. See `docs/writeup/methodology.md` for data provenance, proxy formulas, and broader limitations. Before any prize-form submission, review the interpretation, context, and tract IDs; do not present the estimates as verified gaps.

## Section 3 — Overture source mix differs across tract strata

**Status: descriptive discovery lead, not a measured map-completeness or hidden-score finding.** The question was whether the share of buildings with an OpenStreetMap source and the share of places without a Meta source are lower in high-SVI, tribal-overlap, and rural tracts, and whether this pattern relates to the provided summer heat/drought fields.

### Method and source definitions

The analysis covers all 9,379 authoritative sample GEOIDs in Maricopa, Northern California, Eastern Oklahoma, and South-Central Texas. For Overture buildings and places, the source scan projected only `sources` and `bbox`; bbox centers were assigned to the official sample tracts using only the provided ACS GEOID/geometry layer. No Overture geometry, placeholder score, target label, removed reference layer, or additional external data was read. The per-tract source shares were appended to the workspace feature cache as discovery-only columns; `estimate_one` and the scored candidate outputs do not use them.

A building is OSM-touched when any source dataset is `OpenStreetMap` (or `OSM`). Place memberships are any `meta`, any `Microsoft`, and any other upstream dataset; the generic Overture and Overture-signals entries are excluded from “other upstream.” `non-Meta place share` means one minus the share of places with any Meta source. Provider shares are per-place membership indicators and may overlap; the separate source-class fields in the report provide a disjoint partition. These are provenance annotations—not evidence of source quality, age, feature completeness, or a unique provider contribution.

Quartiles are calculated within each region. Reported means weight tracts equally; 95% intervals resample tracts within groups (2,000 replicates). Contrasts are independent tract-bootstrap differences, group 1 minus group 0. Spearman correlations use average ranks and pairwise complete observations; they are descriptive and have no p-values or covariate adjustment.

### Findings

The top-quartile-minus-bottom-quartile (`Q4−Q1`) contrasts were negative for both source indicators in all four regions. However, some intervals include zero, and the magnitude varies considerably:

| Region | Building OSM share, Q4−Q1 (95% CI) | Non-Meta place share, Q4−Q1 (95% CI) |
|---|---:|---:|
| Eastern Oklahoma | −7.71 pp [−11.50, −3.75] | −2.73 pp [−4.43, −1.07] |
| Maricopa, Arizona | −2.92 pp [−6.41, +0.82] | −1.50 pp [−2.92, −0.03] |
| Northern California | −7.81 pp [−15.36, −0.13] | −1.82 pp [−4.24, +0.55] |
| South-Central Texas | −18.75 pp [−21.26, −16.04] | −7.91 pp [−8.66, −7.18] |

The within-region Spearman correlations with `svi_overall` were negative in every region: −0.215 to −0.108 for building OSM share and −0.273 to −0.038 for non-Meta place share. The rural-versus-urban contrast was more consistent: rural-minus-urban OSM share ranged from −16.83 to −38.68 pp, with all four bootstrap intervals below zero; rural-minus-urban non-Meta share ranged from −11.85 to −19.29 pp, also with all intervals below zero. This is an unadjusted contrast, not an effect of rurality independent of SVI or geography.

Tribal-overlap contrasts were also negative in the three regions with more than one `tribal_any` tract, but counts differ sharply (805 in Eastern Oklahoma, 37 in Maricopa, and 15 in Northern California). South-Central Texas had only one `tribal_any` tract, so its apparent contrast is not generalizable. The `tribal_any` field is the supplied overlap indicator; it is not a legal-status classification.

High-heat and high-drought source contrasts changed direction across regions. For example, the OSM share in the top heat quartile was higher in Maricopa and Eastern Oklahoma, but lower in Northern California and South-Central Texas. The top drought quartile was higher in Northern California and lower in South-Central Texas, with near-zero contrasts in the other two regions. These do not support a consistent hazard-specific source pattern.

The ten lowest-OSM-share tracts within each region's high-SVI quartile are listed in `reports/source-provenance.json`. Examples include `40109108803` (Eastern Oklahoma; 0.063% OSM-touched of 1,581 buildings; rural; `tribal_any=true`), `04003000700` (Maricopa; 2.25% of 2,755; urban), `06007003200` (Northern California; 0.062% of 3,252; urban), and `48201433505` (South-Central Texas; 0% of 189; urban). These are screening examples only; the source share does not establish that a tract is missing buildings or services.

![Overture source-provider shares across region-specific SVI quartiles. Points are equal-weight tract means and bars are 95% tract-bootstrap intervals; place-provider memberships may overlap.](../../reports/source-provenance.png)

### Interpretation and limits

The strongest repeatable descriptive pattern is lower OSM-source and non-Meta place membership in rural than urban tracts. The SVI gradient is smaller and less certain in some regions; heat/drought results are heterogeneous. This motivates provenance-aware review of map-source representation by rurality and vulnerability, but it does **not** establish that Overture coverage is incomplete, that one provider is more accurate, or that any operational service is absent. Relative to the published scorecard this is not a restatement of a marginal rural-versus-urban or SVI cell: the scorecard publishes those marginals, whereas this finding concerns the provider composition *inside* the same rural cell, a dimension the scorecard does not publish. For mapping programmes in Eastern Oklahoma, Northern California, Maricopa and South-Central Texas, a systematically lower OpenStreetMap-sourced share in rural tracts is a reason to weight source provenance when choosing where to send field verification or quality-assurance effort; that is a plausible audit-prioritisation consequence, not evidence that any service is absent from those tracts. The bbox-center assignment can misclassify features near tract borders; tract means are not population-weighted; spatial dependence, multiple comparisons, and confounding are not modeled. No score candidate or submission was changed.

### Reproduction

The deterministic JSON and figure are `reports/source-provenance.json` and `reports/source-provenance.png`. The default command reuses the extended feature cache when present; `--refresh-source` re-reads only the provided Overture `sources` and `bbox` columns to rebuild it.

```sh
MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig \
  /home/martin/hddProjects/langywrap/uv run \
  --with pyarrow==26.0.0 --with numpy==2.5.4 --with pyproj==3.8.0 \
  --with shapely==2.2.0 --with matplotlib==3.10.8 \
  python scripts/discovery_source_provenance.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/source-provenance.json \
  --figure reports/source-provenance.png
```

A repeated cache-based run produced byte-identical JSON and PNG. The report records source-object URIs, object/selected-column byte sizes, local input/cache hashes, and per-region source counts. This source-provenance analysis used no additional public source; the separate Camp Fire DINS mechanism check is documented above with its own URL, access date, licence metadata and raw-data manifest.

## Section 4 — External public context layers: one weak broadband lead, no robust incremental pattern

**Scope and interpretation.** This is a follow-on discovery-only screen using four additional public federal data layers staged outside git at `$COMPETITION_WORKSPACE/external-discovery-2026-10-27/`: CDC/ATSDR SVI 2022, USDA ERS RUCA 2020, Census Planning Database 2026, and Census 2020 AIANNH tribal-area polygons. The acquisition manifest records URLs, retrieval UTC times, byte counts and SHA-256 values; the analysis verifies each listed raw-file hash. The publisher pages did not state dataset-specific licences; because these are U.S. federal agency-produced datasets, we record the public-domain basis under 17 U.S.C. §105, subject to third-party material exceptions. This is a statutory assessment, not a quoted dataset licence; confirm exceptions before redistribution. Raw data remain outside git in the authorized workspace. FEMA NRI and FCC broadband were not included because their acquisition failed. Four frozen, label-free candidates (A1/A2/A3/V2) provide proxy outcomes, not organiser scores or accuracy labels. The fixed 28-test family covers A1 composite and components plus A2/A3/V2 composites. Region-adjusted effect sizes are partial correlations; uncertainty is a 1,000-draw county-cluster bootstrap stratified within the four regions. The residual screen conditions on region and supplied `pct_urban`, SVI, RUCA and tribal-share controls. OOF ΔR² compares that baseline to adding one external layer, using five deterministic region-stratified folds that hold out whole state-county groups. BH-adjusted q-values cover all 28 conditional residual-screen tests. All estimates describe frozen proxies only; they do not validate hidden targets, imply causation, or alter any scored candidate.

**CDC/ATSDR SVI 2022.** On the 9,294 complete cases (85 selected SVI values were missing), the region-adjusted correlation with A1 composite was −0.003 (county-bootstrap 95% CI [−0.045, +0.035]); county-grouped OOF ΔR² was effectively zero. Some proxy subcomponents differed: A1 POI had r=−0.122 [−0.181, −0.079], and V2 composite r=−0.130 [−0.174, −0.096], but the external SVI exposure had no residual variance after conditioning on the supplied `svi_overall` field, so an incremental effect was not estimable. This is a source/vintage cross-check, not evidence that SVI explains real-world or hidden coverage gaps; no independent predictive gain was observed.

**USDA ERS RUCA 2020.** RUCA joined for all 9,379 official GEOIDs; complete analytic N was 9,294 after the shared provided-control screen. The region-adjusted association varied across the provided estimators and components: RUCA code with A1 transport r=+0.322 [+0.269, +0.374], A1 POI r=−0.156 [−0.210, −0.122], A1 composite r=+0.029 [−0.001, +0.074], and A3 composite r=+0.342 [+0.286, +0.404]. Because the external RUCA code was represented by the provided RUCA control, its conditional residual effect was not estimable and county-grouped OOF ΔR² was effectively zero. RUCA primary codes are ordinal, so these are screening associations rather than interval-scale changes; the sign disagreement across proxies and lack of incremental holdout signal preclude a common coverage-gap claim.

**Census PDB 2026 broadband context.** The selected exposure was tract percentage of households with no internet; 9,297 exposure values were available, and 9,292 tracts entered the complete-case model. Its region-adjusted association with A1 building-gap proxy was r=−0.042 (95% CI [−0.075, −0.007]); after the supplied controls, partial r=−0.051 [−0.074, −0.030], with BH q=0.056 across the 28 residual tests. The region-adjusted direction matched in three of four regions, while whole-county OOF ΔR² was only +0.0033. Thus this is a small, borderline, diagnostic lead about a model proxy—not a robust multiplicity-controlled finding, not evidence that offline households have better mapped buildings, and not a basis for changing predictions. Other PDB composite and transport associations did not add consistent held-out signal.

**Census AIANNH 2020 tribal-area polygons.** We dissolved overlapping AIANNH polygons, projected them with 2024 Census tract geometry to EPSG:5070, and calculated tract area-share overlap; exact geometry joins covered all 9,379 official GEOIDs, with 1,042 positive overlaps. The Maricopa-labeled sample includes `35023970000`, whose GEOID is New Mexico; we retained its official sample region label and used its matching New Mexico tract geometry rather than reassigning or dropping it. For A1 composite the region-adjusted r was +0.038 (95% CI [−0.020, +0.073]) and grouped OOF ΔR² was effectively zero. After provided `tribal_pct` and other controls, A1 composite partial r was −0.007 [−0.023, +0.005]; no conditional result survived BH correction. The layer's unique exposure variation after supplied controls was only 0.2% of raw exposure SD, demonstrating strong redundancy rather than an independent tribal-area signal. Polygon overlap does not establish any resident's identity or legal status, and no coverage-disparity claim is supported.

Reproduce the analysis from the competition repository with the manifested raw files in the authorized workspace:

```sh
/home/martin/hddProjects/langywrap/uv run --quiet \
  --with numpy==2.5.4 --with pyarrow==26.0.0 --with shapely==2.2.0 \
  --with pyproj==3.8.0 --with pyshp \
  python scripts/analyze_discovery_external_layers.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/discovery-external-layers.json
```

The config is `config/discovery-external-layers-preregistered.json`; the complete 28-test results, missingness, source URLs/licence status/hashes, and tract-geometry provenance are in `reports/discovery-external-layers.json`. External features were used only in this discovery audit, never in scored candidate construction or uploads. Census TIGER2024 tract boundaries were used only as geography for the AIANNH polygon overlay; no TIGER roads, Microsoft footprints, USGS facilities, CBP data, or equivalent copies were read or used to rebuild the scored target.

## Section 5 — T1 named-stroke class-drop diagnostic and implementation limit

**Status: diagnostic only; not a gap-accuracy result or a promoted estimator.** T1 computes a provided-road class-drop fraction for identities that have named-class length. In the frozen implementation, identities use case-folded `names.primary`, falling back to route network/reference; class-drop length is counted only when the identity's named length lies mostly outside the tract. The verified implementation does **not** apply geometric-angle stroke merging: continuity is exact identity only, a conservative proxy that can miss class changes on otherwise continuous roads. It uses the supplied unfiltered Overture road layer and does not use labels or removed reference layers. These limitations prevent interpreting a T1 association with the frozen S1 disagreement residual as evidence of hidden-score accuracy.

| Check | Evidence/result | Direct report source (JSON pointer) | Reproduction |
|---|---|---|---|
| Implementation limit | Stroke-angle rule is not applied; exact identity continuity only. Synthetic geometry/clipping is stubbed in the hand-check, so that check verifies aggregation, not road I/O or geographic clipping. | `reports/t1-outside-share-threshold.json` `/provenance/feature_cache_metadata/0.7/maricopa-az/diagnostics/stroke_angle_rule`; `reports/t1-class-drop-handcheck.json` `/scope/frozen_T1_function_exercised` | [A], [C] |
| Synthetic aggregation hand-check | All 12/12 hand-calculated graph fixtures matched, including defined masks; maximum absolute fraction error was 0.0. This is a code/aggregation check, not a data or accuracy test. | `reports/t1-class-drop-handcheck.json` `/results/fixtures`, `/results/maximum_absolute_fraction_error`, `/results/all_defined_masks_match`, `/results/acceptance_pass` | [A] |
| Fixed-threshold T1 named-stroke residual screen | On 9,347 paired T1/S1-supported tracts, pooled county-held-out ΔR² was −0.0070216 (county-bootstrap 95% CI [−0.0180717, −0.0022856]); region ΔR² was negative in all four regions: Maricopa −0.0144431, Northern California −0.0130530, Eastern Oklahoma −0.0045437, and South-Central Texas −0.0040128. | `reports/t1-stroke-residual-screen.json` `/measure_after/pooled`, `/measure_after/county_block_bootstrap/pooled_delta_r2_ci_95`, `/measure_after/regional`, `/provenance/T1_artifact`; registered threshold 0.7 is recorded in `reports/t1-outside-share-threshold.json` `/measure_before/registered_threshold` | [B] |
| Nested threshold sensitivity | The inner counties selected threshold 0.9. On the fixed outer support (n=1,155), pooled ΔR² was −0.0023880 (county-bootstrap 95% CI [−0.0154058, +0.0031650]); Northern California's outer ΔR² was −0.2697082 (n=73). All three preregistered promotion gates failed. | `reports/t1-outside-share-threshold.json` `/preregistration/specification/thresholds`, `/inner_selection/selected_threshold`, `/outer_evaluation/paired_outer_county_delta_r2/pooled`, `/outer_evaluation/county_bootstrap_95/pooled_delta_r2_ci_95`, `/outer_evaluation/paired_outer_county_delta_r2/regional/northern-ca`, `/acceptance` | [C] |

The fixed-threshold and nested screens use a one-term correction to the frozen S1 estimator-disagreement residual, with county holdouts; S1 residuals are not organiser targets. The nested screen selects the threshold only on inner counties and evaluates it on fixed outer counties. Its poor Northern California result is retained rather than tuned away. T1 remains diagnostic-only: no candidate score was changed, no submission was made, and no claim is made that T1 improves coverage-gap accuracy.

### Reproduction commands

[A] Re-run the synthetic aggregation hand-check:

```sh
/home/martin/hddProjects/langywrap/uv run --quiet \
  --with numpy==2.5.4 --with shapely==2.2.0 \
  python scripts/audit_t1_class_drop_handcheck.py \
  --out reports/t1-class-drop-handcheck.json
```

[B] Re-run the fixed-threshold county-held-out screen:

```sh
/home/martin/hddProjects/langywrap/uv run --quiet \
  --with-requirements requirements.txt \
  python scripts/screen_t1-stroke-residual.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/t1-stroke-residual-screen.json \
  --manifest reports/t1-stroke-residual-county-folds.csv
```

[C] To rebuild the nested screen from raw provided inputs, run one feature-build invocation for each of the three registered thresholds (`0.5`, `0.7`, `0.9`) in each official region (`maricopa-az`, `northern-ca`, `eastern-ok`, `south-central-tx`); execute these 12 calls separately so each remains a bounded run. For example:

```sh
/home/martin/hddProjects/langywrap/uv run --quiet \
  --with-requirements requirements.txt \
  python scripts/screen_t1_outside_share_threshold.py \
  --workspace "$COMPETITION_WORKSPACE" --build-feature \
  --threshold 0.5 --region maricopa-az
```

After all 12 feature caches exist, reproduce the nested selection and outer evaluation:

```sh
/home/martin/hddProjects/langywrap/uv run --quiet \
  --with-requirements requirements.txt \
  python scripts/screen_t1_outside_share_threshold.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/t1-outside-share-threshold.json
```

Every new numeric T1 statement in this section has a direct report pointer in the evidence table and a corresponding command above. No removed reference data, sample score placeholders, labels, leaderboard feedback, or candidate outputs were used.

## External follow-up — Camp Fire DINS mechanism check

**Negative, not confirmed.** The public CAL FIRE DINS catalogue states Creative Commons Attribution (version not specified; https://data.ca.gov/dataset/cal-fire-damage-inspection-dins-data), retrieved 2026-10-29. In the six focal Butte tracts, DINS recorded 1,887–3,314 destroyed points per tract versus a provided USFS-minus-Overture difference of 390.2–766.1; the per-tract relative errors range from 0.690 to 0.799, so 0/6 pass the preregistered 0.30 limit. Across all 28 NorCal tracts whose last provided MTBS burn year is 2018, Spearman is 0.463, below the 0.60 decision threshold. Thus the measured test does not support rewriting the discovery as “the reference still counts structures DINS recorded destroyed.” The source warns DINS inspections may omit structures and are limited to its stated fire-perimeter/SRA scope; no record does not establish that a structure was absent or rebuilt. In the reverse direction, across all 591 official NorCal tracts, 576 have positive Overture counts but no Camp Fire DINS record; these tracts sum to 1,139,778 Overture buildings. This is not evidence that those buildings were rebuilt because the extract is Camp-Fire-specific and DINS is incomplete. Full tract rows, damage classes, opposite-direction accounting, scatter, source metadata and reproducibility are in `reports/discovery-dins-mechanism.md`; exact claim pointers are `reports/discovery-dins-mechanism.json#/focal_butte_tracts`, `#/decision`, `#/spearman`, `#/opposite_direction`, and `#/external_source`. This uses the incident-filtered DINS point layer only for the discovery prize, joins to provided tract geometry, and never changes or joins to scored candidate predictions.
