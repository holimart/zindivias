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
