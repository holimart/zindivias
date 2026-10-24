# Reproducible methodology for the Bias Bounty coverage-gap challenge

**Purpose and status.** This is a reproducible Best Documentation methodology and a record of exploratory Best Bias Discovery analyses. It describes provided-feature proxies and fixed, unvalidated model components; it does **not** reconstruct the organiser's hidden target. No training labels or per-tract organiser scores are available. No new candidate has been assembled from the v2 component audits, and the proposed building prior still needs operator approval before scored integration. This document makes no local or private accuracy claim.

## Rubric checklist and reading guide

**Coverage: 100% of the documentation items listed below.** Completion means the method, limitations, and reproduction path are documented; it is not a claim of model accuracy.

| Documentation rubric item | Status and location |
|---|---|
| Data sources, source footprints, URIs, byte sizes, hashes, exact scope | Complete below; source-level byte/row inventory and local SHA-256s are distinguished explicitly. |
| Component calculations and defined/undefined handling | Complete below; detailed POI categories, fixed facility priors, and route sensitivity follow. |
| Zero-population, water, no-Overture, missingness, and template edge cases | Complete in [`edge-cases.md`](edge-cases.md), backed by `reports/edge-cases.json`. |
| Alternative aggregation/weighting history | Complete in “Alternative components and aggregation choices.” |
| Historical leaderboard calibration, parameters, results, and inferred moments | Complete in the generated [`probe-log.md`](probe-log.md), including failed/cancelled probes and score IDs. |
| Accuracy claims | None absent a successful scored submission ID. Historical public RMSEs appear only in the ledger-derived probe log and do not validate v2 or a hidden/private score. |
| Reproduction, scope limits, caveats, final recommendations | Complete below and in the linked audit commands/reports. |

## Data sources, scope, and provenance

The official scoring domain is the union of four published per-region sample files: 9,379 GEOIDs, all retained as strings and joined exactly. The official sample columns are `GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score`; analysis reads only the `GEOID` field. **Sample score placeholders are never used as labels or feature values.** The platform's 9,794-row template has 415 additional eastern-WA rows; those rows are formatting-only, contain no supplied feature inputs, and do not extend the official scored-region scope. See [`docs/competition.md`](../competition.md) and the official captured rules at `$COMPETITION_WORKSPACE/RULES.md`.

Challenge inputs are public Source Cooperative objects under:

```text
s3://us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge/reference/{region}/{region}-sample-submission.csv
s3://us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge/reference/{region}/{region}-census-acs-housing.parquet
s3://us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge/reference/{region}/{region}-overture-{roads|buildings|pois}.parquet
s3://us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge/strata/{region}/{region}-strata-tract-table.parquet
```

The exact supplied source object sizes and source row counts recorded during the audits are:

| Region | Official sample | ACS geometry parquet bytes / rows | Overture roads bytes / rows | Overture buildings bytes / rows | Overture POIs bytes / rows | Strata parquet bytes / rows |
|---|---:|---:|---:|---:|---:|---:|
| Maricopa AZ | 1,593 | 3,457,173 / 1,593 | 80,052,206 / 494,492 | 324,720,762 / 2,908,224 | 40,987,154 / 300,046 | 793,492 / 1,593 |
| Northern CA | 591 | 3,037,540 / 591 | 43,515,963 / 191,836 | 130,554,079 / 1,164,724 | 16,168,720 / 116,897 | 354,711 / 591 |
| Eastern OK | 1,192 | 3,650,879 / 1,192 | 82,239,229 / 520,550 | 298,882,068 / 2,551,694 | 28,848,614 / 207,369 | 638,464 / 1,192 |
| South-Central TX | 6,003 | 13,758,433 / 6,010 | 299,751,270 / 1,937,009 | 1,269,949,045 / 11,463,801 | 179,937,096 / 1,300,334 | 2,800,838 / 6,010 |

The source inventory documents bytes and row counts but does not publish SHA-256 for the remote Overture/ACS objects. The following checksums identify the exact official sample and local strata files used. Feature-cache hashes are checksums of the **derived aggregate cache**, not of the remote source objects.

| Region | Official sample CSV bytes; SHA-256 | Local strata parquet SHA-256 | Derived Overture tract-cache bytes; SHA-256 |
|---|---|---|---|
| Maricopa AZ | 44,664; `1640b801d56ab308e68c65583b88cfb9e1555b05a6d0b922eaba22ac09960a31` | `f19ec2f5afd400827b2420a81916bdec95b08772badd85fdb250149c334a5fcd` | 239,311; `e6e4064ea3827b8262cf2c3fe96beaeecd40592a7ce9bf0976a37cfbca1bbc22` |
| Northern CA | 16,608; `f38017b5284667b80b451685fd146388c108e1b30308301289c7a96ecfe60902` | `7fc19dce5ca76ee94eb2006d9bd51bed71d5bfbcdf3ef756605e0998df940243` | 100,099; `37ccc687c45ce2ce19e75d378c1e68bebf678f19c45cb4fe95582d250506a87c` |
| Eastern OK | 33,436; `56cb40fba4008d646bc63c62c91e211871459224b3de8b3a886ff813803fd0d4` | `e82862b9d9db436410ad47df466e133df94df6328f6a138d5bd871b491ba0d54` | 194,636; `e5a06316c316f61bf25b80b48e89c1e7f0b1461a0b8cec5831aae24e0cf83fa6` |
| South-Central TX | 168,144; `afa59c0eb30c40be25ce82098f144447591b79cb9692e669b2f2fe7b7b1ee58e` | `55589565024414f244ccbcadcc45a90860a3840ec9d8a4bdfa8a126c2128a354` | 921,462; `51e36d01eec7b2e5a4f9b4986a5aabd70acd7ad10084fef040bc05ad88842746` |

Audit reports additionally record row-group/projection information and component-specific input hashes: `reports/sample-contract-audit.json`, `reports/gap-estimator-audit.json`, `reports/transport-route-ref-audit.json`, `reports/poi-category-audit.json`, and the source-traceable reports linked below. The Texas ACS/strata objects each have 7 extra rows relative to the official sample; joins explicitly filter those rows out. ACS `housing_units` is not read for score calculations and is never used as a denominator floor. The official challenge data is CC-BY-SA 4.0; no extra public data was required. No credentials, paid API, or non-public source is used.

## Organiser scoring context and our calculations

The task provides no labels. The organiser's stated score is the mean of defined transport, building, and POI components; an unavailable reference makes its component undefined and omitted from the divisor. Organiser component definitions are provided for context only. Removed TIGER/Line, Microsoft building-footprint, USGS facility, and Census CBP reference layers—or equivalent copies—must not be used to recreate or approximate the hidden target. No such references enter these analyses or scored predictions.

The deterministic C2/v0 screen uses only provided Overture feature summaries and allowed provided covariates (`ALAND`, `pop_total`, `pop_urban`, `pct_urban`). Geometry from the supplied ACS housing tract files is used solely for spatial clipping/tract assignment; no ACS housing count is read. Road geometry is clipped to authoritative sample tract polygons and measured in EPSG:5070; building/POI bbox centers are assigned to tracts. Exact formulas and audit lineage are in `reports/gap-estimator-audit.json`.

| Component | C2/v0 expected reference proxy | Definedness and component calculation |
|---|---|---|
| Transport | `0.35 + 0.55*sqrt(ALAND_km2) + 0.0007*pop_total + 2*urban_fraction` (expected named-road km) | Defined when expectation ≥6 km. Gap=`clip(1−overture_named_road_km/expected,0,1)`. |
| Buildings | `max(0.5,0.30*pop_total + 4*sqrt(ALAND_km2) + 5*urban_fraction)` (expected structures) | Defined when observed Overture buildings >0 or expectation ≥1. Gap=`clip(1−observed/expected,0,1)`. ACS housing units are not a floor or denominator. |
| Fire / EMS / schools | Fire=`0.15+pop/30000+0.45*u`; EMS=`0.15+pop/40000+0.45*u`; schools=`0.20+pop/2500+0.20*u` | Each type defined when observed count >0 or expected count ≥0.5. Gap per defined type is clipped `1−observed/expected`; facility half averages only defined types. |
| Establishments half | `0.20 + pop/70 + 0.50*u` (v0 proxy) | Defined when all-place Overture count >0 or expected establishments ≥1. Gap=`clip(1−observed/expected,0,1)`. No CBP data is accessed. |
| POI | Mean of defined facility half and establishments half | Equal-weight defined halves; if only one is defined, use that half; if neither is defined, POI is undefined. |
| Tract composite | No new denominator | Equal mean of defined transport, building, and POI components; undefined terms are omitted. If none are defined, no composite is emitted. |

For missing urbanity, use finite `pct_urban`; otherwise use `pop_urban/pop_total` when population is positive; if both are unavailable at zero population, use the disclosed neutral 0.5 fallback. In the proxy expected-reference formulas, land area is floored at 0.01 km². Unknown-category POIs are apportioned using the in-sample selected-category share in the legacy v0 screen, not treated as category-specific zeroes. These heuristics are explicit assumptions, not learned parameters. A serialized zero attached to `defined=0` is only a storage placeholder and is excluded from all means.

## Alternative components and aggregation choices

The published component structure motivates equal weight across defined transport/building/POI terms and equal weight across the two defined POI halves. This is transparent and avoids asserting external importance weights, but does not establish that the organiser's hidden implementation uses the same values or that this is predictive-optimal. Other tempting choices—always dividing by three, filling undefined terms with zero, using housing units as a building denominator, importing CBP counts, or adding an arbitrary global scale—were rejected because they alter the stated defined-component rule, mix incompatible units, use prohibited references, or amount to unvalidated calibration.

Historical alternatives C1 (region constants), C3 (v0 denominators ×1.25), P4 (global scale of C1), P5 (region mean plus residual), and P6/region indicators are described with parameters, IDs, scores, inferred moments, assumptions, and cancellation status in [`probe-log.md`](probe-log.md). Those leaderboard-derived moments and coefficients are not accepted weighting choices for future models. P4's alpha remains only to reproduce its already-scored artifact. The route denominator alternatives state+US+Interstate, +FM/RM, and +explicit CR are a separate provided-feature sensitivity; the selected full route definition improves disclosed regional ratio-band coverage while accepting a documented trade-off in macro undefined-share error. No route alternative was chosen from leaderboard feedback.

## Historical leaderboard calibration (transparency only; not reusable)

The archived experiments include scored submissions and cancelled/dry-run probes. The generated [`probe-log.md`](probe-log.md) lists every ledger event, each probe's parameters, successful public RMSE only when paired with its recorded platform submission ID, artifact hashes, and the score-algebra moments or explicit “no moment” status. It documents the public-subset/all-official-row composition assumption and false initial score placeholders. In particular, P4's global scale is leaderboard-derived and reproduction-only; P5/P6/region probes were not successful scores. These statistics are historical disclosure and are forbidden as v2 priors, target moments, feature thresholds, or per-tract values. The operator's no-information-probe policy supersedes the historical plans; there is no new probe upload or candidate authorization here.

## Model-only v2 components and approval gates

V2 replaces v0's proxy rates with separately audited, fixed, challenge-feature-driven blocks. These audits report aggregate predictions and sensitivity; they do not form a combined candidate and do not demonstrate score accuracy.

- **Transport:** selected route-reference feature is `L_named/L_route` with `clip(1−L_named/L_route,0,1)`, defined only for `L_route>0`. `L_named` uses Overture named road classes; `L_route` is the sum of eligible named-route geometry from Overture route tags, each segment counted once. The chosen tags include state, U.S., Interstate, FM/RM, and explicit CR; details and kept/dropped network tables are below. No external road reference enters the feature. The aggregate ratio/undefined diagnostics are model-design checks only.
- **Facilities POI:** a pooled logistic prior estimates `P(reference type present)` from provided population density and urban fraction, then applies the explicit one-count-ahead expected-gap formula. The fixed qualitative coefficients, unclassified POI treatment, zero-presence definedness, and aggregate audit are in “Facility-miss POI v2.” No regional target moment or leaderboard feedback fitted these coefficients.
- **Buildings:** the experimental rule is a fixed building gap `0.01` only when `overture_buildings>0`, with zero-count rows undefined. It uses no ACS housing units, USFS, or CarbonPlan features. This remains a proposal requiring operator approval before scored integration; see `docs/decisions/building-component.md` and `reports/building-v2-audit.json`.
- **Building morphology sensitivity:** `config/building-morphology-preregistered.json` froze six equal-weight support features and beta sensitivities `0.01/0.02/0.05` before the Overture building-geometry scan. The separate `B_MORPH_V1` diagnostic uses only provided Overture geometry/attributes and allowed `ALAND`/population context. For defined rows, `gap=0.01+beta*(1-morphology_support)`; zero-count rows remain undefined. At primary beta `0.02`, the component mean is `0.02045` over 9,377 defined tracts; all 9,377 defined tracts differ from the `0.01` constant prior, while the two zero-building tracts retain the undefined numeric placeholder. Scores range from `0.01530` to `0.02809` among defined rows, finite and within `[0,0.1]`. This is a preregistered feature-sensitivity result, not a target accuracy test or an approved scored candidate; no reference footprint count, label, or leaderboard result was used. Full region summaries, attribute missingness, and all three beta distributions are in `reports/building-morphology-candidate.json`, with the tract-level diagnostic component table in `reports/candidates/building_morphology.csv`. Reproduce with `/home/martin/hddProjects/langywrap/uv run --with-requirements requirements.txt python scripts/build_building_morphology_candidate.py --workspace "$COMPETITION_WORKSPACE" --candidate-csv reports/candidates/building_morphology.csv --out reports/building-morphology-candidate.json`.
- **Building-source sensitivity:** `config/building-source-prior-preregistered.json` froze three qualitative exclusive-source-mixture profiles and beta values `0.01/0.02/0.05` before candidate measurement. `B_SOURCE_V1` uses only Overture-derived source-class shares plus `ALAND`/`pop_total`; support is `0.8 × weighted source-class share + 0.2 × bounded population-density context`, with `gap=0.01+beta*(1-support)` when Overture count is positive. The primary profile at beta `0.02` averages `0.01651` (defined rows q10/median/q90 `0.01329/0.01574/0.02068`, max `0.02603`); all 9,377 defined rows differ from the constant `0.01` baseline. Across fixed source profiles, the beta-0.02 means are `0.01930` (conservative), `0.01651` (primary), and `0.01574` (Microsoft-heavy). Shares describe provenance, not geometric identity or completeness; the screen is not an accuracy test or approved scored candidate. `reports/building-source-prior.json` includes per-region source shares, source-feature coverage and all profile/beta distributions; `reports/candidates/building_source_prior.csv` contains the tract-level diagnostic table. Reproduce with `/home/martin/hddProjects/langywrap/uv run --with-requirements requirements.txt python scripts/build_building_source_prior.py --workspace "$COMPETITION_WORKSPACE" --candidate reports/candidates/building_source_prior.csv --out reports/building-source-prior.json`.
- **Establishments:** the explicit analysis-only sensitivity formula is `clip(1−overture_poi_all/(k_pop*pop_total),0,1)` for positive population, with `k_pop=0.024` used only as a task-specified sensitivity anchor. For zero population, positive places yield a defined clipped zero; zero places are undefined. The anchor has **no approval for scored use**, uses no CBP source, and is not a model coefficient; see `docs/decisions/establishments-half.md` and `reports/establishments-sensitivity.json`.
- **Combined score:** future assembly must average only model-defined terms and pass the exact-ID/schema/range/hash invariants. The component audits alone do not authorize v2 assembly or upload. There is no local labeled holdout; deterministic checks establish code/contract correctness only.

## Edge cases, bias discovery, and interpretation

The full edge-case table and calculation policy are in [`edge-cases.md`](edge-cases.md), backed by `reports/edge-cases.json`. The audit finds 26 zero-population tracts, 2 zero-`ALAND` tracts, 43 tracts with a >50% water proxy, 132 with no selected named roads, 2,644 with no selected route-network km, 2 with no Overture buildings, 2 with no Overture places, and 26 missing `pct_urban` values. These are covariate/coverage characteristics, not hidden-score outcomes. The audit also reports fixed-prior v2 component distributions for zero-population, water-proxy, and other edge groups. The expected facility gap is `P(reference present) × conditional gap`, so low prior presence yields proportionally small expected gaps; missing `pct_urban` falls back to `pop_urban/pop_total`, or neutral 0.5 at zero population. These are component diagnostics, not an assembled candidate (the establishments half remains approval-gated). The water statistic is only `max(0,min(1,1−ALAND/geometry_area))` after projecting provided tract geometry to EPSG:5070; it is not an external water mask or a scoring correction.

Exploratory SVI/rural comparisons below are strata of C2/C3 **predictions** only. Their bootstrap intervals quantify tract-resampling variation in those proxies; they are not confidence intervals for the hidden organiser score. The analysis does not establish a causal mechanism, actual service access, or measured equity disparity. Avoid naming a high predicted gap as a confirmed underserved tract. For any future disparity assessment, compare within region or use common region weights: region composition confounds pooled comparisons, as documented in `reports/discovery-strata-confound.json`.

## Literature and feature-provenance context

The documentation-only prior-art review is [`docs/literature/completeness-prior-art.md`](../literature/completeness-prior-art.md); its coverage and URL-verification caveats are summarized in [`docs/literature/README.md`](../literature/README.md). It reviews road/building completeness, volunteered geographic information quality, provider/schema documentation, organizer-method context, and leaderboard-oracle ethics. Publication context motivates testing coverage mechanisms and careful uncertainty language; it does not contribute labels, per-tract rates, coefficients, or external numeric inputs to scored predictions. The selected Overture road/POI category definitions are documented in the audits, and all source-data restrictions remain as stated in the rules.

## Reproduction and recommendations

The broad source and method checks are available as deterministic repository scripts/reports. Recreate the new edge-case and probe-log artifacts from the competition repository:

```sh
uv run --with-requirements requirements.txt \
  python scripts/audit_edge_cases.py \
  --workspace "$COMPETITION_WORKSPACE" --out reports/edge-cases.json
uv run --with-requirements requirements.txt \
  python scripts/render_probe_log.py --out docs/writeup/probe-log.md
```

Then run the full unit suite with the pinned runtime (`uv run --with-requirements requirements.txt pytest -q`). Audit scripts for route variants, POI categories, facility misses, building v2, establishments sensitivity, and discovery are individually reproducible using commands in their linked reports/method sections. Never read a hidden target, removed reference, leaderboard page, or score placeholder to regenerate these documents.

**Recommendation:** retain the transparent equal-defined-component aggregation as a methodology description, not as an accuracy claim; use the selected Overture route definition only after the remaining model/approval gates are met; keep the building and establishments priors out of scored predictions until separately approved; and avoid water corrections or subgroup calibration without allowed evidence. Do not submit an information probe. Any future score evaluation must be a completed, documented model candidate and follow [`docs/submission-policy.md`](../submission-policy.md). The historic leaderboard log is disclosure, not authorization.

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

### Route denominator sensitivity and selected definition

`reports/transport-route-variants.json` compares the three cumulative alternatives using only the permitted workspace caches and disclosed aggregate context. The sequence is: (1) state + U.S. + Interstate; (2) add FM/RM; (3) add explicit CR county routes. Per-region length ratios, route-only undefined shares, and hybrid any-component-undefined shares are in the report.

| Variant | Regions inside disclosed 0.71–1.59 ratio band (of 4) | Regions inside lane 0.6–1.7 band | Macro mean absolute error of hybrid any-undefined share |
|---|---:|---:|---:|
| State + US + Interstate | 1 | 1 | 0.03356 |
| + FM/RM | 2 | 2 | 0.03646 |
| + CR | 3 | 3 | 0.04380 |

All three variants put all four hybrid any-undefined shares within 0.10 of the disclosed regional values. Selection is fixed from these diagnostics only: prioritize the number of regional ratios inside the disclosed band, then total distance outside it, and use macro any-share error as a tie-breaker. The selected denominator is state + US + Interstate + FM/RM + explicit CR: adding FM/RM moves Texas from 2.397 to 1.183, and adding CR moves Northern California from 1.768 to 1.504; three ratios are in band, while Maricopa remains 1.929. This choice trades some closeness on undefined-share error (the core-only variant has lower MAE) for substantially better disclosed ratio-band coverage; all variants satisfy the ±0.10 undefined-share check. The organizer supplied a cross-region range, not individual region ratios, so this is not a comparison to region-specific targets. No leaderboard values, labels, or reference layers were used, and no score probe or candidate was created.

Reproduce the sensitivity table without rescanning Overture source objects:

```sh
/home/martin/hddProjects/langywrap/uv run \
  --with pyarrow==26.0.0 --with numpy==2.5.4 \
  python scripts/audit_transport_route_variants.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/transport-route-variants.json
```

Reproduce the underlying route audit from the repository (the command refreshes only route-derived columns in the authorized workspace feature cache and writes aggregate diagnostics):

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

### 50 m same-name facility dedup sensitivity

`scripts/audit_place_duplicates.py` compares raw selected facility rows with a 50 m same-name sensitivity in `reports/place-dedup.json`. It uses the configured fire/EMS/school category sets; normalizes only `names.primary` by Unicode NFC, casefold, and whitespace collapse; preserves punctuation; and leaves unnamed facilities untouched. Bbox centers are projected from EPSG:4326 to EPSG:5070. Within each normalized-name group, stable-order greedy clusters merge a row only when it lies within 50 m of a retained representative; cluster categories are unioned and assigned to that representative's tract. This avoids transitive chains whose endpoints are farther than 50 m. These are provided-data sensitivity results, not a target-accuracy test.

| Region | Facility rows, raw → deduplicated | Facility rows removed | Fire zero share, raw → deduplicated | EMS zero share, raw → deduplicated | School POI rows, raw → deduplicated |
|---|---:|---:|---:|---:|---:|
| Maricopa AZ | 6,408 → 6,405 | 3 (0.047%) | 69.37% → 69.37% | 84.49% → 84.49% | 5,582 → 5,579 |
| Northern CA | 3,316 → 3,315 | 1 (0.030%) | 53.47% → 53.47% | 76.31% → 76.31% | 2,773 → 2,772 |
| Eastern OK | 5,881 → 5,876 | 5 (0.085%) | 54.19% → 54.19% | 72.48% → 72.48% | 4,836 → 4,831 |
| South-Central TX | 30,146 → 30,119 | 27 (0.090%) | 63.72% → 63.72% | 76.98% → 76.98% | 26,247 → 26,220 |
| **All regions** | **45,751 → 45,715** | **36 (0.079%)** | **62.82% → 62.82%** | **77.64% → 77.64%** | **39,438 → 39,402** |

All 36 removed rows are school duplicates; no fire or EMS row is removed, so fire0/ems0 shares are unchanged. Keep raw Overture counts as the default: no evidence here shows dedup improves hidden-score accuracy, and leaderboard probing is prohibited. No candidate, estimator default, or submission was changed.

Reproduce the sensitivity from the competition repository:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  /home/martin/hddProjects/langywrap/uv run --with-requirements requirements.txt \
  python scripts/audit_place_duplicates.py \
  --workspace "$COMPETITION_WORKSPACE" --out reports/place-dedup.json
```

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
