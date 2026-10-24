# Best Bias Discovery: post-fire building-count disagreement may look like under-mapping

**Status:** reproducible descriptive finding from challenge-provided inputs; not a claim about hidden organiser scores, the exact reference vintage, or measured emergency outcomes. No additional public dataset was used.

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

Two additional supplied examples are `06089010603` (ratio 0.691; MTBS last burn 2018) and `06033000704` (0.798; last burn 2017). The provided cumulative burned-area percentage can exceed 100 after repeated burns; values above 100 are kept in the named examples but excluded from the mutually exclusive 0–100% burn-class comparison.

| Provided NorCal burn class | Tracts | Ratio <0.8 | Share below 0.8 |
|---|---:|---:|---:|
| 0% (unburned) | 449 | 7 | 1.6% |
| >0–5% | 28 | 0 | 0.0% |
| >5–20% | 32 | 0 | 0.0% |
| >20–<50% | 32 | 0 | 0.0% |
| 50–100% | 36 | 4 | 11.1% |

Fourteen of 591 tracts have provided burned percentages above 100 and are excluded from these bins; none are missing a burn percentage. The high-burn minus unburned difference is **+9.55 percentage points**, with a stratified 50,000-draw tract-bootstrap 95% percentile interval of **[+0.55, +20.89] percentage points**. This interval is wide and quantifies only tract-resampling variation in the binary ratio indicator; it does not capture spatial dependence, measurement error, source-vintage uncertainty, or hidden-target error. Butte County has 54 sample tracts, six below the ratio cutoff; their summed `pop_total` is 6,977.

![NorCal and Butte tract map: fill is provided MTBS burn percentage; purple outline marks Overture/USFS ratio below 0.8. The six numbered Butte markers follow the GEOID table order.](../../reports/discovery-postfire-map.png)

**Source/vintage caution:** the compared count is the provided strata field `usfs_BuildingCount_sum`; its supplied edition metadata is recorded in `reports/discovery-postfire.json`. The observed ratios support the hypothesis that a 2018-vintage Microsoft-footprint derivative may be stale after wildfire, leaving a risk-building count above the Overture count. The tract-level analysis does not independently verify the supplied count's exact vintage. It does **not** claim that the organiser used a 2018 Microsoft release or this USFS field to construct its hidden target.

## Reproducibility

The script `scripts/discovery_postfire_stale_reference.py` reads the provided NorCal Overture feature cache, only selected fields from the provided strata table, GEOID values from the official sample, and GEOID/geometry columns from the challenge-provided ACS geometry object. It does not read placeholder scores, labels, removed reference layers, or DINS/external evidence. The script reproduces the ratio table, burn-class table, bootstrap CI, Butte population sum, and map; the deterministic report is `reports/discovery-postfire.json`, and the map is `reports/discovery-postfire-map.png`. No scored candidate CSV was changed; their hashes are captured in the report.

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

A repeated run produced byte-identical JSON and PNG in the recorded environment. No external public data was used, so no DINS manifest or external-source licence/retrieval record is applicable. All statements about emergency-planning consequences are hypothetical, and the pattern is descriptive rather than causal.

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
