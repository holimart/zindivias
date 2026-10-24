# Methodology draft — provided-data coverage-gap screen

**Status:** reproducible draft for Best Documentation and Best Bias Discovery; not a submission-ready accuracy claim. No training labels or per-tract organiser scores are available. The work below describes a provided-data proxy screen only.

## Data and constraints

The official rules and sample contract are summarized in `docs/competition.md`. The scored IDs are the official per-region samples, read as strings. The discovery script uses only the South-Central Texas official sample IDs, the provided South-Central Texas tract strata table, the existing C2 estimate output, and the pre-registered C3 denominator sensitivity output. It reads no values from the sample's placeholder score columns. No external public data, removed reference layers, or equivalent copies were used. Additional public data is permitted only for the Best Bias Discovery writeup; none was needed here. The scored CSVs were not changed.

Inputs and hashes are recorded in `reports/discovery-rural-svi-minority.json`. In particular:

- Official South-Central TX sample: `$COMPETITION_WORKSPACE/samples/south-central-tx-sample-submission.csv` (6,003 GEOIDs, SHA-256 `afa59c0eb30c40be25ce82098f144447591b79cb9692e669b2f2fe7b7b1ee58e`).
- Provided strata: `$COMPETITION_WORKSPACE/defined-flags-inputs/south-central-tx-strata-tract-table.parquet`; only `GEOID`, `pct_urban`, `svi_minority`, and `epht_heat_days_summer` were read. Seven extra Texas strata rows outside the authoritative sample are excluded.
- C2 proxy estimates: `reports/gap-estimates.csv` (SHA-256 in the report); C3 is the already preregistered expected-denominator ×1.25 candidate, `reports/candidates/C3_denominator_x125.csv`.

The code validates exact sample/strata membership for the regional sample and uses string GEOIDs for all joins. The discovery stratum field `svi_minority` is not an input to C2 or C3; the estimator uses provided Overture feature summaries plus the permitted ALAND, population, and urbanity covariates. The `svi_minority` and heat fields are used only to describe/slice the provided sample after prediction.

## Proxy estimator context

C2 is a deterministic screening heuristic, not a fit to hidden labels. It uses provided Overture road, building, and POI summaries. Road geometry is clipped to authoritative sample tract polygons and measured in EPSG:5070; building/POI bbox centers are assigned to scored tracts. ACS data is used only for tract geometries, not housing-unit counts. The source restrictions, per-component defined-flag logic, missing-data handling, hashes, and complete formulas are in `reports/gap-estimator-audit.json`.

For context, the version-0 expected-reference proxies are:

| Quantity | C2 v0 proxy |
|---|---|
| Named-road km | `0.35 + 0.55*sqrt(ALAND_km2) + 0.0007*pop_total + 2*urban_fraction` |
| Buildings | `max(0.5, 0.30*pop_total + 4*sqrt(ALAND_km2) + 5*urban_fraction)` |
| Fire / EMS / schools | respectively `0.15 + pop/30000 + 0.45*u`; `0.15 + pop/40000 + 0.45*u`; `0.20 + pop/2500 + 0.20*u` |
| Establishments | `0.20 + pop/70 + 0.50*u` |

Defined-flag heuristics in the audit are: transport expected length at least 6 km; buildings observed count > 0 or expected count at least 1; fire/EMS/school observed category count > 0 or expected count at least 0.5; CBP observed total places > 0 or expected establishments at least 1. For a defined term, the proxy gap is clipped `1 - observed/expected` in `[0,1]`. An undefined gap's serialized zero placeholder is excluded from every mean; if no top-level components are defined, no composite is emitted. POI is composed from a facilities half (mean of defined fire/EMS/school estimates) and an establishments half, with equal weight when both halves exist. The tract composite is the mean of defined transport, building, and POI terms. Unknown-category POIs are apportioned using in-sample observed category shares, not treated as category-specific zeroes. No component or composite proxy is the hidden organiser score.

For missing urbanity, use `pct_urban` when finite, otherwise `pop_urban/pop_total` where population is positive, otherwise the disclosed neutral 0.5 fallback. Land area is floored at 0.01 km² in expected-reference proxies. Tracts with zero Overture observations can still have a positive proxy deficit when a component is defined; they are not automatically removed. There is no dedicated water-dominated-tract correction, so water-dominated or very low-population tracts are an acknowledged proxy limitation. ACS housing-unit values are not read; ACS geometries only support tract assignment. Rail, hospitals, and removed reference sources are not used to approximate scored targets.

## Route-reference transport v2 (provided Overture only)

The opt-in route audit is `scripts/estimate_gaps.py --route-audit-only`. It reads only the provided Overture roads `class`, `routes`, and `geometry` columns, official sample GEOIDs, the provided ACS tract `GEOID`/`geometry` solely for clipping, and the existing v0 component flags for an any-undefined comparison. It reads no TIGER or other road reference data, labels, sample score placeholders, or leaderboard values. Named classes remain `motorway`, `trunk`, `primary`, and `secondary`; the v0 `overture_road_km` field is preserved. The v2 `L_named` is independently clipped in EPSG:5070 as `overture_named_road_km_5070`; the v2 `L_route` is the length of each segment once if it carries at least one accepted route network, also clipped in EPSG:5070. The score term is `clip(1 - L_named/L_route, 0, 1)`, defined only for `L_route > 0` (undefined rows serialize gap 0 with `defined=0`).

The route filter accepts interstate (`US:I*`), U.S. (`US:US*`), state-level (`US:<state>` plus explicit SR, loop, spur, beltway, future, alternate, and bypass variants), and county `US:<state>:CR*` networks. It rejects historic/business and scenic networks (`NHT`, `NFSR`/`NFRS`, scenic tokens), missing network values, and unsupported non-route systems. County-name namespaces such as `US:OK:ADAIR` are listed but not treated as numbered county routes without an explicit `:CR` class. A segment with multiple accepted route networks contributes once to `L_route`; per-family length columns can overlap. `overture_route_core_km` and `overture_route_plus_fm_rm_km` are cached separately so the declared Texas FM/RM sensitivity can be measured without re-reading/reclassifying target data. Since the existing repository already used aggregate cache schema v5 for source-mix features, route caches use aggregate schema v6 (base) or v7 (with source mix) and `route_schema_version=1`.

| Region | `sum(L_named)/sum(L_route)` | `L_named` / `L_route` km | `L_route=0` share | Route-v2 + v0 building/POI any-undefined share | Organizer disclosed any-undefined | v0 any-undefined |
|---|---:|---:|---:|---:|---:|---:|
| Maricopa AZ | 1.929 | 22,113.7 / 11,466.6 | 0.566 | 0.566 | 0.55 | 0.365 |
| Northern CA | 1.504 | 12,203.6 / 8,116.7 | 0.308 | 0.308 | 0.37 | 0.305 |
| Eastern OK | 1.161 | 27,944.0 / 24,071.9 | 0.240 | 0.240 | 0.21 | 0.407 |
| South-Central TX | 1.183 | 118,176.4 / 99,894.1 | 0.212 | 0.212 | 0.28 | 0.369 |

All four route-v2 transport + v0 building/POI any-undefined shares are within 0.10 of the disclosed region shares; they are a hybrid check, not a full v2 composite. Three of four length ratios meet the lane's broad 0.6–1.7 check; Maricopa is 1.929 and remains out of band even with all accepted state, U.S., Interstate, loop, and explicit county-route networks. The report identifies this as a denominator-coverage failure rather than adding prohibited scenic/business, missing-network, or unclassified county-name routes to force a ratio. In Northern California the observed route-entry counts reproduce `US:CA` 6,055, `US:CA:CR` 2,190, `US:US` 1,024, and `US:I` 861; the report lists all kept/dropped networks and counts for each region, per-tract `L_route` quantiles/bins, and v0 comparisons. The disclosed ratio band (0.71–1.59) and undefined shares are aggregate validation context only, not calibration targets. This is a route mechanism audit, not a validated accuracy result or assembled candidate.

Reproduce from the repository (the command refreshes only route-derived columns in the authorized workspace feature cache and writes aggregate diagnostics):

```sh
/home/martin/hddProjects/langywrap/uv run \
  --with pyarrow==26.0.0 --with numpy==2.5.4 --with shapely==2.2.0 \
  --with pyproj==3.8.0 python scripts/estimate_gaps.py \
  --workspace "$COMPETITION_WORKSPACE" --route-audit-only \
  --route-audit-out reports/transport-route-ref-audit.json
```

## POI numerator audit and selected category definitions (2026-10-24)

The provided-data POI audit is reproducible with
`scripts/audit_poi_categories.py`; its full per-region frequency table for all
matching `categories.primary` and `categories.alternate` values is
`reports/poi-category-audit.json`. The report includes explicit zero rows for
categories absent from a region, primary/alternate counts, name-regex hits,
source-dataset mix, confidence quantiles, and tract-zero sensitivity. Counts
in the category-frequency table overlap: a single POI can have more than one
primary/alternate category, so those rows are not additive.

The audit read only official `GEOID` cells from the per-region sample files;
sample score placeholders were not used. For each provided Overture POI file,
Parquet projection included `categories`, `sources`, `confidence`, and `names`,
plus `bbox` solely to assign POI centers to tracts for the required zero-tract
sensitivity. It did **not** read the Overture `geometry` column, other POI
properties, external facility lists, or removed reference layers. Provided ACS
`GEOID` and tract `geometry` columns were used only as the tract spatial index;
POIs outside the 9,379 official sample IDs were excluded. Three Texas POI
centers did not intersect an official sample tract. The output records exact
selected-column compressed-byte counts, object sizes, and the ordered GEOID
sequence hashes; there was no full-object download. The selected POI column
chunks total 132,133,741 compressed bytes across the four regions.

The selected sets are now centralized in
`config/poi-category-definitions.json`; `scripts/estimate_gaps.py` reads that
same config, and a test asserts its FIRE/EMS/SCHOOL sets remain aligned. The
config requires the forthcoming estimator-v2 code to load `selected_sets`
from this file rather than duplicate categories or derive them from leaderboard
scores. A POI is counted once per component when its primary **or any alternate** category
matches one of these exact sets:

| Component | Selected categories | Decision |
|---|---|---|
| Fire | `fire_department`, `fire_protection_service` | Retain the two station categories used by the existing estimator. A broad `fire|rescue` match incorrectly adds firework retail, firearm training, fire/water restoration, fireplace service, firewood, and animal rescue. |
| EMS | `ambulance_and_ems_services` | Retain. Report `emergency_medicine` as a sensitivity only; exclude emergency rooms and roadside services because they are not EMS stations. |
| Schools | `school`, `elementary_school`, `middle_school`, `high_school`, `public_school`, `private_school`, `charter_school`, `religious_school`, `preschool`, `montessori_school` | Retain the canonical school set. Exclude dance, driving, music, cosmetology, flight, sports, vocational/specialty schools, administrative/retail education categories, and `day_care_preschool` absent evidence these match the organizer's school reference definition. |
| Hospitals | excluded | Follow the organizer's explicit exclusion; report category/name hits but never count hospitals in the facility numerator. |

Selected facility row counts below are unique POI rows per component (a POI may
match multiple categories); source counts count POI rows carrying each dataset
and therefore overlap across datasets.

| Region | Official tracts | Selected fire / EMS / school POI rows | `meta` / Microsoft / BrightQuery / Foursquare POI rows | Confidence q10 / q50 / q90 | Selected-facility confidence `<0.5` |
|---|---:|---:|---:|---:|---:|
| Maricopa AZ | 1,593 | 716 / 304 / 5,582 | 164,553 / 54,746 / 50,288 / 24,120 | 0.502 / 0.920 / 0.990 | 459/6,408 (7.16%) |
| Northern CA | 591 | 491 / 177 / 2,773 | 69,601 / 18,427 / 17,695 / 8,992 | 0.489 / 0.920 / 0.990 | 244/3,316 (7.36%) |
| Eastern OK | 1,192 | 866 / 471 / 4,836 | 132,819 / 31,847 / 23,601 / 13,985 | 0.503 / 0.920 / 0.987 | 416/5,881 (7.07%) |
| South-Central TX | 6,003 | 3,169 / 1,787 / 26,247 | 794,977 / 211,635 / 172,148 / 92,173 | 0.402 / 0.920 / 0.990 | 2,155/30,146 (7.15%) |

`Sources.dataset` is a multi-valued property: dataset row counts are not a
partition and are descriptive provenance, not weights or coverage labels. The
name-only regex audit also finds many POIs outside the selected exact category
sets (fire/EMS/school/hospital respectively: Maricopa 1,031/1,142/1,184/772;
Northern CA 344/322/573/355; Eastern OK 865/588/1,150/780; South-Central TX
4,019/4,541/4,873/3,733). Those are review flags only. A name regex is too
noisy to add a record to the score numerator; for example, it can match retail,
training, emergency-room, and administrative records.

| Region | Core zero tracts fire / EMS / school | All fire/rescue categories (fire zero) | Add `emergency_medicine` (EMS zero) | Add `day_care_preschool` (school zero) | All school/education categories (school zero) |
|---|---:|---:|---:|---:|---:|
| Maricopa AZ | 1,105/1,593 (69.37%) / 1,346/1,593 (84.49%) / 254/1,593 (15.94%) | 908/1,593 (57.00%, −12.37 pp) | 1,331/1,593 (83.55%, −0.94 pp) | 244/1,593 (15.32%, −0.63 pp) | 143/1,593 (8.98%, −6.97 pp) |
| Northern CA | 316/591 (53.47%) / 451/591 (76.31%) / 49/591 (8.29%) | 268/591 (45.35%, −8.12 pp) | 444/591 (75.13%, −1.18 pp) | 45/591 (7.61%, −0.68 pp) | 29/591 (4.91%, −3.38 pp) |
| Eastern OK | 646/1,192 (54.19%) / 864/1,192 (72.48%) / 130/1,192 (10.91%) | 495/1,192 (41.53%, −12.67 pp) | 853/1,192 (71.56%, −0.92 pp) | 121/1,192 (10.15%, −0.76 pp) | 67/1,192 (5.62%, −5.29 pp) |
| South-Central TX | 3,825/6,003 (63.72%) / 4,621/6,003 (76.98%) / 655/6,003 (10.91%) | 3,130/6,003 (52.14%, −11.58 pp) | 4,565/6,003 (76.05%, −0.93 pp) | 598/6,003 (9.96%, −0.95 pp) | 328/6,003 (5.46%, −5.45 pp) |

The sensitivity columns change only the named component and hold the other two
at the selected core sets. The broad regex options materially shrink zero
shares by admitting unrelated category types; they are not defensible facility
numerators. Adding `emergency_medicine` shifts the EMS zero share by only about
one percentage point, but it remains the wrong facility concept (clinical
emergency care rather than EMS stations). Adding `day_care_preschool` shifts
the school zero share by less than one point across regions; it remains
excluded pending reference-definition evidence. Thus the prior exact category
sets remain the chosen provided-data definitions. These are count/definition
audits, not evidence of target accuracy; no LB values or hidden targets were
used.

Reproduce the audit from the competition repository:

```sh
# Set COMPETITION_WORKSPACE to the authorized workspace before running.
  /home/martin/hddProjects/langywrap/uv run \
  --with pyarrow==26.0.0 --with numpy==2.5.4 --with shapely==2.2.0 \
  python scripts/audit_poi_categories.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/poi-category-audit.json
```

## Facility-miss POI v2 (fixed prior; model-only audit)

The opt-in implementation is `scripts/estimate_gaps.py --poi-model facility-miss`; the default remains v0. It writes `reports/poi-facility-miss-audit.json` only and does not overwrite v0 estimates, manifests, or candidate files. The audited numerator uses the exact fire/EMS/school category sets in `config/poi-category-definitions.json`. It reads only official sample GEOIDs, provided `pop_total`, `ALAND`, `pop_urban`, `pct_urban`, the cached provided Overture type/all-place counts, and the existing v0 estimates needed for a hybrid-composite comparison. It reads no sample scores, labels, removed references, external datasets, or leaderboard values, and writes no per-GEOID output.

The model returns an expected type gap, not an imputed reference count. Its fully pooled, three-coefficient logistic prior is

```text
x_density = log1p((pop_total / max(ALAND / 1,000,000, 0.01)) / 100)
P(ref_t present) = sigmoid(intercept_t + beta_density_t * x_density
                            + beta_urban_t * urban_fraction)
conditional_gap_t = clip(1 - n_overture_t / (n_overture_t + 1), 0, 1)
E[gap_t] = P(ref_t present) * conditional_gap_t
```

Here `n_overture_t` is the selected exact-category count plus the provided summary's unclassified POI count multiplied by its in-sample selected-category share, matching the v0 treatment of unknown types rather than treating them as category zeroes. The `n_overture + 1` denominator is a one-count-ahead model assumption, not a measured reference denominator; it makes the conditional gap 1 at Overture zero and lets the positive-count gap decay as counts increase. When `P(ref_t present) == 0`, the reference is certainly absent and the component is undefined (numeric placeholder zero excluded from means). The facilities half is the mean of the defined expected type gaps. The POI component is the equal mean of the facilities and establishments halves when both are defined. For establishments this analysis uses the prior turn's task-specified `k_pop=0.024` anchor only as a sensitivity assumption; it is explicitly not authorized for scored use without a separately compliant prior and approval. Zero-population tracts with no Overture places have an undefined establishments half; positive observed places at zero population imply a zero, defined deficit under the clipped formula.

The coefficients are fixed qualitative domain priors, not supervised estimates and not fitted to organizer region/cell moments or leaderboard feedback. No region-specific intercept is fit: the model is pooled across regions because no allowed reference evidence supports regional calibration. Density and urbanity are expected to increase the probability that a reference facility exists; the school prior is higher than the fire/EMS priors because schools are a broader, more common facility type. The explicit coefficients are:

| Type | Intercept | Density slope | Urbanity slope |
|---|---:|---:|---:|
| Fire | −2.80 | 0.30 | 0.70 |
| EMS | −3.20 | 0.25 | 0.60 |
| Schools | −1.00 | 0.20 | 0.35 |

With the prior frozen, the audit reports the observed Overture zero counts by region × rural/urban cell alongside expected POI gaps. Rural is `pct_urban < 0.5`; urban is `>= 0.5`. Missing `pct_urban` falls back to `pop_urban/pop_total` for positive population, otherwise to a neutral 0.5, with each fallback counted. The per-region expected POI means are Maricopa 0.15788, Northern California 0.12576, Eastern Oklahoma 0.08459, and South-Central Texas 0.11873—all inside the backlog's 0.02–0.20 range. Across 9,379 official tracts, v0 POI mean is 0.25704 versus v2 0.12148; v2 `E[p²]` is 0.030688 and 15.84% of tracts have POI v2 > 0.25. The full per-region and per-cell table (`n`, `fire0`, `ems0`, `sch0`, POI mean, `E[p²]`, and >0.25 share) is in the JSON report. The hybrid composite (v0 transport/building, v2 POI) has mean 0.11301 and `E[p²]` 0.031126; this is not an assembled v2 candidate.

The report intentionally leaves region-mean-constrained predicted RMSE null. There are no labels, and the operator amendment prohibits fitting to region/cell target moments. All displayed cell means are diagnostics computed after fixing the prior; they do not feed back into its parameters. These estimates are unvalidated model outputs, not hidden-score or accuracy claims. The default v0 scoring path and the previously committed candidate artifacts remain unchanged. Reproduce with:

```sh
/home/martin/hddProjects/langywrap/uv run \
  --with pyarrow==26.0.0 --with numpy==2.5.4 --with shapely==2.2.0 \
  --with pyproj==3.8.0 python scripts/estimate_gaps.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --poi-model facility-miss \
  --poi-audit-out reports/poi-facility-miss-audit.json
```

## Overture POI confidence by SVI and urbanity

The deterministic diagnostic in `scripts/discovery_confidence_by_stratum.py`
computes per-tract Overture confidence quantiles in the authorized workspace
cache `gap-estimator/features/poi-confidence-by-tract.parquet`; stratum results
and 95% confidence intervals are in `reports/confidence-by-stratum.json`.
Only official sample GEOIDs, provided `svi_overall`/`pct_urban`/`svi_covered`,
Overture `bbox`/`confidence`, and provided ACS tract geometry for spatial
assignment were read. Overture geometry, scores, removed reference layers, and
external data were not used. The metric is the place-weighted share with the
provided confidence value `<0.5`; it is not a count of stale pages or an
accuracy measure. No source attribution was read, so the Facebook/staleness
explanation remains untested.

Quartiles are region-specific over official scored tracts with finite
`svi_overall`; equal values at cut points stay together in the higher quartile.
`pct_urban <0.5` is rural and `>=0.5` urban; missing values are excluded from
the cross-tab but remain in the per-tract cache. Intervals resample tracts
within each region × urbanity × SVI-quartile cell (10,000 seeded replicates)
and recompute the pooled low/valid-place ratio. They reflect tract resampling
only, not spatial dependence; the Q4−Q1 contrasts below are unadjusted
exploratory comparisons.

| Region | All-place confidence q10 / q25 / median | All places `<0.5` | Urban Q4−Q1 share difference (95% CI) | Rural Q4−Q1 share difference (95% CI) |
|---|---:|---:|---:|---:|
| Maricopa AZ | 0.502 / 0.768 / 0.920 | 9.95% | −2.24 pp [−2.86, −1.64] | −4.02 pp [−7.80, −0.39] |
| Northern CA | 0.489 / 0.770 / 0.920 | 10.26% | −1.38 pp [−2.35, −0.46] | +0.53 pp [−1.78, +3.19] |
| Eastern OK | 0.503 / 0.770 / 0.920 | 9.91% | −0.80 pp [−1.44, −0.18] | −3.47 pp [−4.92, −2.06] |
| South-Central TX | 0.402 / 0.750 / 0.920 | 12.38% | −6.06 pp [−7.71, −4.56] | −3.60 pp [−4.56, −2.60] |

The observed pattern is opposite the initial hypothesis that higher-SVI tracts
would have a larger low-confidence share: Q4−Q1 is negative in seven of eight
region × urbanity cells, with the Northern California rural interval spanning
zero. The largest contrast is South-Central Texas urban (Q1 16.02%, Q4 9.96%,
difference −6.06 pp). This consistent inverse pattern is material enough to
record as a descriptive quality caveat, not as evidence that vulnerable
communities have better maps: confidence distributions may reflect POI mix,
urban form, or other unmeasured factors, and confidence does not validate
feature correctness. It neither validates hidden scores nor supports a causal
or source-vintage claim.

Reproduce from the competition repository with the authorized workspace:

```sh
/home/martin/hddProjects/langywrap/uv run \
  --with pyarrow==26.0.0 --with numpy==2.5.4 --with shapely==2.2.0 \
  python scripts/discovery_confidence_by_stratum.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/confidence-by-stratum.json
```

## Discovery screen

The reproducible script is `scripts/discovery_rural_svi_minority.py`. It constructs a within-region intersection not specified in the official description of the automated scorecard:

1. Restrict to the authoritative South-Central TX sample.
2. Define the rural screen as provided `pct_urban < 0.5`.
3. Among rural tracts with nonmissing `svi_minority`, calculate the empirical 75th percentile (`0.6549`). The high-field group is `svi_minority >= 0.6549`; the comparison group is below it. Four rural rows lack that field and are excluded. This is a domain-field-by-rural intersection, not the provided overall `svi_overall` field and not an ethnic/legal classification.
4. Compare mean C2 predicted `coverage_gap_score` across those groups. Also report C2 component gaps conditional on each component being defined. As a model-specification check, repeat the composite comparison under preregistered C3 (all expected-reference proxies multiplied by 1.25).
5. Compute 95% percentile intervals from 50,000 bootstrap resamples of tracts independently within each group. Seeds are fixed and saved in the JSON report. These intervals quantify tract-resampling variability only; they do not account for spatial dependence, proxy-model error, or hidden-target error.

The screen found 248 high-field rural tracts and 739 other rural tracts. Mean C2 proxy scores were 0.06522 and 0.03034, a difference of +0.03488 (bootstrap 95% interval `[0.01676, 0.05509]`). Under C3, the difference was +0.04647 (interval `[0.02678, 0.06808]`). For C2, defined-only building-gap means differed by +0.02856 and defined-only POI-gap means by +0.04630; defined-only transport-gap means differed by +0.01820 with interval `[-0.00192, 0.04114]`. The component case counts and defined rates are in the audit JSON.

## Reproduction

From the competition repository, with the authorized workspace available:

```sh
/home/martin/hddProjects/langywrap/uv run --with pyarrow --with numpy \
  python scripts/discovery_rural_svi_minority.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/discovery-rural-svi-minority.json
```

The script produces a deterministic JSON report with input hashes, definitions, group counts, point estimates, bootstrap intervals, and the highest-scoring named GEOIDs. A repeated run produced byte-identical JSON. It does not download data, access credentials, alter candidate CSVs, or upload anything.

## Limitations and interpretation

This analysis is exploratory. C2/C3 are unvalidated estimates, and the contest provides no reference labels with which to establish their accuracy. A bootstrap interval around predictions is not an interval for true coverage gaps. Tract-level independence is especially questionable in spatial data; the bootstrap may understate uncertainty. The group cut is a transparent analyst-defined threshold; test other cutpoints only as separately documented sensitivity checks, not leaderboard tuning. The analysis is descriptive, not causal. `svi_minority` is used as supplied and should not be reinterpreted as an individual-level demographic label. The writeup identifies a subgroup for follow-up, not a proven disparity in organiser scores or real-world service outcomes.
