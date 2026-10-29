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
- **Cross-theme support sensitivity:** `config/cross-theme-support-preregistered.json` froze `XTS_C2_V2` before final adjustment measurement. It adds at most `0.05 × (1−cross_theme_support)` to the frozen C2 prediction, where cross-theme support is half the mean plus half the minimum of three bounded Overture features: Microsoft building-source share, the route-network/named-road length share, and the share of places with confidence ≥0.5. The 9,379-row official-scope prior C2 hash is fixed in the manifest; 119 road and two building/place rows with missing feature support use neutral 0.5 fills with explicit flags. Primary output changed 9,377/9,379 rows; mean adjustment was `0.02874`, max `0.04617`, and global prediction variance changed `0.02413→0.02507`. Per-region prior→adjusted variances were OK `0.01503→0.01582`, AZ `0.02136→0.02190`, Northern CA `0.02106→0.02183`, and South-Central TX `0.02620→0.02711`. Raw support correlations are descriptive (building/road Pearson `0.305`, building/place `0.014`, road/place `−0.081`). Overture source membership, route tags, and confidence are internal indicators, not truth or completeness measures; no accuracy claim or v2 promotion follows. The exact features, missingness, distributions, four-region correlations, candidate ID/bounds, and CSV/manifest hashes are in `reports/cross-theme-support.json`; the analysis-only candidate is `reports/candidates/cross_theme_support.csv`.
- **Schema optional-attribute presence sensitivity:** `config/schema-presence-preregistered.json` freezes all 44 modeled optional fields from the supplied POI, building, and road top-level schemas, with every remaining top-level field explicitly excluded or assigned an identity/geometry role. POI `confidence` and all three `sources` fields are excluded so this is distinct from confidence weighting and source-mixture analysis. The builder reads only those selected attributes plus `bbox`; it assigns bbox centers to provided official tract polygons and reads only the frozen C2 model prediction as the baseline. Null, present-empty, explicit scalar `unknown`/`unspecified`, mixed unknown/known, and known values are separate states; unknown is not counted as missing, and schema-unavailable is distinct from row-null. Across 44 optional-field drop perturbations, the maximum candidate-score change is `0.00327` (≤`0.05`); with all selected fields unavailable the candidate falls back exactly to C2. On 9,379 official IDs, 9,377 have feature-support diagnostics; 4,687 rows (49.97%) adjust. The bounded candidate delta mean/median/p95/max is `0.00121/0/0.00499/0.01539`; C2's all-row mean changes from `0.16898` to `0.17019`. Mean field-level presence rates by theme (equal average of declared field rates among bbox-assigned records) span POIs `76.05–76.70%`, buildings `14.90–18.91%`, and roads `18.24–24.33%` across regions; the complete 44-field × theme × region status matrix and C2/candidate moments are in `reports/schema-presence-candidate.json`. All selected fields were present in the actual Parquet schemas, no exact `unknown`/`unspecified` scalar token occurred in the configured categorical fields, and empty nested/list fields were retained as separate states. Candidate scores are finite/in `[0,1]`; two complete builds were byte-identical. This is an internal metadata/support sensitivity, not evidence of geographic completeness or accuracy; bbox-center assignment is approximate. No labels, sample score placeholders, `sources`, confidence, removed references/equivalents, external feature data, or leaderboard feedback were used, and no upload or accuracy claim was made. Candidate CSV SHA-256 `b06c649eab390eee0f77465d01e7c728bdc126f92270b5570d618723037e3f18`; manifest SHA-256 `f4450ee825de7a41b3c5c5df1f5aefb1808904aaf028837ad414296667d1c700`. Reproduce with `uv run --with-requirements requirements.txt python scripts/build_schema_presence_candidate.py --workspace "$COMPETITION_WORKSPACE" --candidate reports/candidates/schema_presence_candidate.csv --manifest reports/candidates/schema_presence_candidate_manifest.json --out reports/schema-presence-candidate.json`.
- **Density-context sensitivity:** `config/density-context-preregistered.json` froze `DENSITY_C2_V1`, the 5 km/20 km centroid radii, log-density scales, and ≤0.05 positive adjustment before measuring the context features. It averages neighboring official-tract ACS population-density and Overture-building-density support, excluding each focal tract and indexing neighbors separately within each of the four regions. The inputs are the provided `ALAND`/`pop_total`/`ur_class`, Overture building counts, and only `GEOID,geometry` from the provided ACS tract polygon table; housing-unit and all other ACS columns are not read. Relative to frozen C2 on 9,379 official rows, 9,147 changed; mean candidate delta was `0.01035`, maximum `0.04720`, and prediction variance changed `0.02413→0.02338`. Delta means were Rural `0.01924` (n=1,712), Urban `0.00836` (n=7,641), and unclassified `0.00864` (n=26), descriptive only. Every region passed the leave-one-tract-out perturbation check (zero change), and the full 9,794-row template output uses the approved zero formatting fill for 415 featureless eastern-WA rows. The candidate is not an accuracy test or approved model integration; `reports/density-context-candidate.json` includes pre/post moments, region/urbanity coverage, missingness, neighbor summaries, bounds, and hashes; the analysis artifact is `reports/candidates/density_context_candidate.csv`.
- **Facility spatial-catchment sensitivity:** `config/facility-catchment-preregistered.json` froze the exact core fire/EMS/school categories, same-region pool, 1/5/10 km centroid radii, and a fixed 5% relative presence-prior uplift before spatial measurement. The builder reads only provided Overture POI `bbox,categories`, ACS `GEOID,geometry`, permitted strata fields, and the existing Overture summary columns needed to reproduce the pooled logistic baseline. Selected point counts assigned to official tracts reproduce the pinned audit's facility zero counts exactly: fire 5,892/9,379 (62.82%), EMS 7,282/9,379 (77.64%), schools 1,088/9,379 (11.60%). It applies `p_ref' = min(1, p_ref × (1 + 0.05 × (1−exp(−d_nearest/r))))` before the existing one-count-ahead expected-gap transform; no logistic coefficient is fitted. The official-scope facility-half mean changes from `0.12566` to `0.12751` at the 5 km primary radius (mean delta `0.00185`, max `0.00905`); 1 km mean/max delta is `0.00444/0.01373`, and 10 km is `0.00109/0.00726`. At 5 km, population-weighted centroid catchment shares across fire/EMS/schools are AZ `0.904/0.754/0.925`, Northern CA `0.836/0.692/0.890`, Eastern OK `0.806/0.691/0.860`, and South-Central TX `0.897/0.822/0.932`. The report includes per-type nearest-distance quantiles, region/urbanity deltas, zero-population cases (26 tracts), zero-ALAND cases (2), and edge/sparse catchments (220 tracts have none of the three facility types within 10 km). No USGS list/coordinates, external POI, removed reference, target, or leaderboard information was used. This is an analysis-only spatial sensitivity, not service-area evidence or an accuracy test. Reproduce with `uv run --with-requirements requirements.txt python scripts/build_facility_catchment_candidate.py --workspace "$COMPETITION_WORKSPACE" --candidate reports/candidates/facility_catchment_candidate.csv --manifest reports/candidates/facility_catchment_candidate_manifest.json --out reports/facility-catchment-candidate.json`; full radius/missingness/weighting diagnostics and SHA-256 values are in the report and manifest.
- **Candidate stability envelope:** `config/candidate-stability-envelope-preregistered.json` freezes six complete feature-derived score variants: C2 mechanistic baseline; cross-theme support; density context; a 5 km facility-catchment replacement over the v0 transport/building/CBP components; building morphology β=0.02; and primary source-prior β=0.02. The facility-catchment and two building variants are recomposed with the existing defined-component mean; cross-theme and density use their frozen full-score candidates. No target, public score, or fitted weights are used. The envelope is an analysis-only diagnostic; its equal-weight mean is not promoted or submitted, and the screen does not satisfy or bypass any scored-integration gate. It uses only the 9,379 official IDs; 415 template-only rows in C2/density files are ignored without parsing their candidate values. All six candidates align and are finite/in `[0,1]`. Spread median/p95/max is `0.11514/0.27016/0.51499`; `75.99%` of rows exceed `0.05`, so the preregistered `p95 ≤ 0.10` criterion correctly flags operator review. Region p95 spread is `0.20209` Eastern OK, `0.22034` Maricopa, `0.20836` Northern CA, and `0.33936` South-Central TX. Two no-defined-component edge rows in each building variant carry forward the existing C2 score; this deterministic fallback is disclosed. Candidate distributions, input hashes, feature-availability rates, and region × provided urbanity summaries are in `reports/candidate-stability-envelope.json`; the long-form member table and per-tract envelope CSV hashes are in its manifest. Two full builds were byte-identical. This is model-specification spread only: no local RMSE or empirical accuracy claim is available.
- **Establishments:** the explicit analysis-only sensitivity formula is `clip(1−overture_poi_all/(k_pop*pop_total),0,1)` for positive population, with `k_pop=0.024` used only as a task-specified sensitivity anchor. For zero population, positive places yield a defined clipped zero; zero places are undefined. The anchor has **no approval for scored use**, uses no CBP source, and is not a model coefficient; see `docs/decisions/establishments-half.md` and `reports/establishments-sensitivity.json`.
- **Combined score:** future assembly must average only model-defined terms and pass the exact-ID/schema/range/hash invariants. The component audits alone do not authorize v2 assembly or upload. There is no local labeled holdout; deterministic checks establish code/contract correctness only.

## Family M metadata and provenance estimator review

Family M is a descriptive screen of metadata, provider provenance, and name signals from the challenge-provided Overture layers, with permitted housing-unit context where specified. It is not a fitted coverage model or evidence about organiser scores. The committed summary records each output's defined-only distribution and coverage, the family pairwise rank correlations, VIF flags, and the matching per-tract Parquet SHA-256. All interpretation remains descriptive: source metadata, tags, and names do not establish feature accuracy or service completeness.

| Idea | Provided-data quantity | Interpretation safeguard |
|---|---|---|
| `M1` | Road version summaries and the low-version share among tertiary roads | Version fields describe recorded metadata, not update recency or completeness. |
| `M2` | Wikidata-link shares across all places and among facility-like names | Link presence is not validation of a place or facility. |
| `M3` | Address-mismatch shares across places and among facilities | Address disagreement is a provenance signal, not proof of a wrong location. |
| `M4` | Chain-brand share and chain count per provided housing-unit field | Brand coverage and the density ratio are separate descriptive channels. |
| `M5` | Multi-property shares for buildings and roads | Both emitted channels are constant-zero on this study scope and are explicitly marked degenerate. |

The orthogonality screen shows that the all-place Wikidata-link share from `M2` and the chain-brand share from `M4` are nearly identical in rank order, so they should be treated as a disclosed redundancy rather than independent evidence. The facility variants from `M2` and `M3` also carry elevated VIF flags; they remain in the record rather than being silently removed. This is not a promotion or a claim that any estimator recovers the hidden quantity.

Source and report lineage: implementation `src/bias60/ideas/m.py`, SHA-256 `05e69ae644a746f51e015b8780dfc58733766c8d3eae52f0b1fb9087cd6a71de` (source commit `4a921228beb4951f7d87cf59311ec15f28fc06ec`); Family M summary `reports/bias60-family-m-summary.json`, SHA-256 `ee9150150a184447189899dfdcec51254b6ae08d64c0c2c191633b1510d5d378` (report commit `a20f091905b1d08f10c9d8c38b90c2e0480c053e`). That report binds the Family-M-era harness snapshot SHA-256 `c1c0f444b6d49dc5dc232f2055f8671d201a2728853ae141eaa09d2779768032` and orthogonality snapshot SHA-256 `c85586f18d2d091082fa66142bc18152880f06d4515165d86713157c37c37417`, both committed with the summary. The summary is label-free and reads the official GEOID partition, generated idea Parquets, and their harness/orthogonality reports.

## Edge cases, bias discovery, and interpretation

The full edge-case table and calculation policy are in [`edge-cases.md`](edge-cases.md), backed by `reports/edge-cases.json`. The audit finds 26 zero-population tracts, 2 zero-`ALAND` tracts, 43 tracts with a >50% water proxy, 132 with no selected named roads, 2,644 with no selected route-network km, 2 with no Overture buildings, 2 with no Overture places, and 26 missing `pct_urban` values. These are covariate/coverage characteristics, not hidden-score outcomes. The audit also reports fixed-prior v2 component distributions for zero-population, water-proxy, and other edge groups. The expected facility gap is `P(reference present) × conditional gap`, so low prior presence yields proportionally small expected gaps; missing `pct_urban` falls back to `pop_urban/pop_total`, or neutral 0.5 at zero population. These are component diagnostics, not an assembled candidate (the establishments half remains approval-gated). The water statistic is only `max(0,min(1,1−ALAND/geometry_area))` after projecting provided tract geometry to EPSG:5070; it is not an external water mask or a scoring correction.

Exploratory SVI/rural comparisons below are strata of C2/C3 **predictions** only. Their bootstrap intervals quantify tract-resampling variation in those proxies; they are not confidence intervals for the hidden organiser score. The analysis does not establish a causal mechanism, actual service access, or measured equity disparity. Avoid naming a high predicted gap as a confirmed underserved tract. For any future disparity assessment, compare within region or use common region weights: region composition confounds pooled comparisons, as documented in `reports/discovery-strata-confound.json`.

## Literature and feature-provenance context

The documentation-only prior-art review is [`docs/literature/completeness-prior-art.md`](../literature/completeness-prior-art.md); its coverage and URL-verification caveats are summarized in [`docs/literature/README.md`](../literature/README.md). It reviews road/building completeness, volunteered geographic information quality, provider/schema documentation, organizer-method context, and leaderboard-oracle ethics. Publication context motivates testing coverage mechanisms and careful uncertainty language; it does not contribute labels, per-tract rates, coefficients, or external numeric inputs to scored predictions. The selected Overture road/POI category definitions are documented in the audits, and all source-data restrictions remain as stated in the rules.

## Family S spatial-context estimator diagnostics

Family S is a label-free collection of five registered spatial-context estimators, computed only from provided tract geometry, strata, Overture summaries/points, and already-emitted estimator artifacts. These are diagnostics and covariates, not a calibrated score or evidence about the hidden organiser target. Each idea emits the official GEOID partition; its defined mask varies by channel and is not silently filled with observed zeros.

| Idea | Spatial signal and outputs | Output rows / channels | Defined rows and limitations |
|---|---|---:|---|
| `S1` | Queen-neighbour means (self excluded) of earlier estimator channels, plus Moran's I diagnostics | 600,256 / 64 | Each lag retains its source defined mask; zero-coverage and sparse channels remain visible. At emission time the source set comprised 44 earlier implemented ideas; later Family T emissions were not retroactively added to this frozen S1 artifact. |
| `S2` | County empirical-Bayes pooling of provided Overture building and all-POI counts; Poisson sampling variance and within-county excess variance set the shrinkage | 18,758 / 2 | Both channels defined for 9,379 tracts. Pooling is a deterministic count smoother, not fitted to target scores. |
| `S3` | Capped decayed count of provided fire/EMS/school POIs across tract boundaries, within 150 m, only where the receiving tract has no same-type point | 28,137 / 3 | All three channels defined for 9,379 tracts. This detects package-boundary leakage only; it is not a service-area or completeness measure. |
| `S4` | Component-median prior-gap difference from the median of 25 nearest peers in the other three provided study regions, using population/building/road density and RUCA | 37,516 / 4 | Buildings, establishments and facilities each defined for 9,377 tracts; roads for 9,296. Peer features and estimates come only from the provided study regions, not the lab. |
| `S5` | Mean normalized local Geary contribution over existing defined gap channels on queen neighbours | 9,379 / 1 | Defined for 9,377 tracts; existing gap emissions are used as emitted, with no ratio reconstruction. |

The label-free summary `reports/bias60-family-s-summary.json` verifies the exact 9,379 GEOID/region partition and each Parquet digest. Parquet SHA-256 values are S1 `632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03`, S2 `5b5e45edd6cd0f0a14cdfb124d587136f0f059a1c80a0aa6131a6abac958980a`, S3 `ce01545f0e10cbffee64b50f5088608d6c67cb096f8769bca869af5fff5533d7`, S4 `5841cac5114f2c7571bd41f90914c076b2eb92e1fe7bd1c33d6fe95b47a2a71c`, and S5 `81d9153819dadf76a9d8666f6887300d412f8d5fc6acf15c1a8dfc924840725c`. The summary SHA-256 is `ea1bdb11e7c358eb5ffc4b75a0a1a3c19d7f2b7bb84dce6453fb400643f68581`; it binds the current passing harness report (`20279d9548fa126ec81b2df9dbbe67b363101303b1222e76caf985dc2c9e9c58`) and orthogonality report (`d5fcb1872c2899d40c949d51181f4c6da35309463d2e84a0af5cbd63e6b0e443`). The current orthogonality matrix measures 145 channels (135 included after declared coverage/degeneracy handling), and flags 12 pairs involving S above `|rho| > 0.8`; an example is the correlation of `0.999` between S1's lagged M2 and M4 all-place metadata channels. Five S channels have VIF above 10 (10.162–14.047), including the S1 lags of M2 facility Wikidata share and M3 facility address-mismatch share and S3 school-boundary leakage. These flags disclose redundancy; all outputs are retained, not silently removed. Complete pairwise correlations, coverage/distributions, VIF values and flagged pairs are in the summary and the full `reports/ideas-orthogonality.json`.

Reproduce the label-free summary against the committed Family S Parquets and current harness/orthogonality reports with:

```sh
COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" \
  /home/martin/hddProjects/langywrap/uv run \
  --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 \
  python scripts/summarize_bias60_family_s.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/bias60-family-s-summary.json
```

This summary measures estimator distributions and their mutual dependence only. No labels, removed references or equivalent copies, leaderboard values, uploads or accuracy claims were used. The spatial signals and the reported correlations/VIF do not establish geographic coverage quality or predictive performance.

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

## Connector-graph road topology sensitivity (analysis only)

`scripts/build_road_topology_candidate.py` preregisters `ROAD_TOPOLOGY_ROUTE_REF_V1` in `config/road-topology-preregistered.json`. It uses only the provided Overture road `class`, `connectors`, and `geometry` columns, official sample GEOIDs, provided ACS tract geometry for assignment, `pct_urban` strata, and the provided route-length cache. For each provided segment, connector points are interpolated from the connector's normalized `at` position; successive connector IDs form adjacency edges. The tract-local graph contains unique connector IDs assigned to that official tract and a simple undirected edge only when both endpoints of a successive connector pair map to the same tract. Cross-tract edges are counted but not included in the local graph; duplicate unordered edges are collapsed for degree/components. Isolated connector vertices are retained. Thus the reported graph is a tract-induced structural summary, not a routing/access network or independent truth source. The hypothesis is motivated by the road-completeness prior art in [`docs/literature/completeness-prior-art.md`](../literature/completeness-prior-art.md) and Overture's connector schema, but no global OSM rate or external road evidence is transferred into the candidate.

The measure-before route-reference component is recomputed as `clip(1 - overture_named_road_km_5070 / overture_route_km, 0, 1)`, defined only when route length is positive. It reconciles exactly to the frozen route-reference audit for each region's named/route length sums, route-zero count, and defined-only mean. Across 9,379 tracts the route-reference candidate is defined for 6,735 (71.81%), with a defined-only mean of `0.03166`; its numeric all-row mean (undefined rows stored as zero with a flag) is `0.02273`. The prior v0 transport component is defined for 5,914 (63.06%) and has all-row mean `0.09683`. The measure-before report also records legacy/named/route length distributions overall, by region, and by rural/urban `pct_urban` strata.

The frozen adjustment is `0.05 × (1 − largest_component_node_share)` and is added to the route-reference gap only on route-defined rows; route-undefined rows retain numeric zero and `defined=0`. This is a positive bounded context sensitivity, not a fit to a reference. Topology diagnostics were available for 9,377/9,379 tracts (99.98%). Across tracts, local components have median 6 and p95 34; mean dead-end-node share is 14.54%, mean degree is 2.24, median largest-component node share is 93.68%, and isolated single-node components average 3.40 per tract. The candidate adjustment mean/median/p95/max over all rows is `0.00513/0.00149/0.02409/0.04358`; 67.45% of rows change. Among route-defined rows, the route-reference gap mean changes from `0.03166` to `0.03880`. Regional candidate delta means range from `0.00321` (Maricopa) to `0.00571` (South-Central Texas); the full region × rural/urban degree, component, adjustment, definedness, and road-length tables are in `reports/road-topology-candidate.json`.

The edge audit processed 3,143,887 supplied road-segment rows and 8,491,546 connector occurrences. It skipped 1,087 segments with invalid/non-LineString geometry after valid connector parsing, observed 1,591 connector points outside the official tract polygons, and excluded 245,818 within-package adjacencies whose endpoints fell in different tracts. Two South-Central Texas tracts had no assigned connector nodes. These boundary/geometry cases are disclosed in the report and do not cause an unbounded fallback. The per-tract candidate is finite and in `[0,1]`; maximum actual change is `0.04358`, below the fixed `0.05` limit. Two complete builds were byte-identical. Candidate CSV SHA-256 is `612c2080c3d470fbcdc0cd752cd26847da05a35bf5d245c0b1599134cc82fda7`; manifest SHA-256 is `b269e4487905937d48418822aeb11a92e76f0371236d99e175e920d82852705f`.

No TIGER/MTFCC values, external road layers, labels, removed references/equivalents, sample score placeholders, or leaderboard information were used; no submission was made. Connector degrees/components can reflect cul-de-sacs, ramps, package edges, or schema/source artifacts and do not establish missing real-world roads or predictive accuracy. No local target RMSE is possible or claimed. Reproduce with:

```sh
uv run --with-requirements requirements.txt python scripts/build_road_topology_candidate.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --candidate reports/candidates/road_topology_candidate.csv \
  --manifest reports/candidates/road_topology_candidate_manifest.json \
  --out reports/road-topology-candidate.json
```

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

## POI confidence-weighted count sensitivity (fixed thresholds)

The frozen, analysis-only builder is `scripts/build_poi_confidence_candidate.py`; its thresholds, category sets, v2 formula, and source-use rules are preregistered in `config/poi-confidence-weighting-preregistered.json`. It reads the four official sample GEOID columns, the provided tract geometry for bbox-center assignment, `ALAND`/population/urbanity strata, and only Overture `bbox`, `categories`, `confidence`, and `sources` columns. Exact fire/EMS/school category sets come from `config/poi-category-definitions.json`. A matched POI contributes once per selected family. At threshold τ, its selected-family count contribution is `confidence` when valid confidence is at least τ, and zero otherwise; raw counts remain separately reported. The existing v2 region-share allocation for no-category POIs and the establishments half are held fixed to isolate the confidence sensitivity. Existing pooled v2 priors and `k_pop=0.024` are analysis-only; source dataset membership is reported as provenance and receives no weight. Hospitals remain excluded.

Before weighting, raw core counts are 5,242 fire, 2,739 EMS, and 39,438 school POI-family rows (overlap between families is possible). Zero-tract counts are 5,892, 7,282, and 1,088 respectively. V0 POI score mean is `0.25699` over all official rows (`0.25704` among its 9,377 defined rows); the raw-count v2 POI mean is `0.12148` over all 9,379. The re-created raw v2 candidate matches the frozen v2 audit's means, variances, and defined counts overall and in each region. Every selected core POI has valid confidence and a dataset source in this input; source identity is descriptive, not a truth or completeness assessment.

| Confidence threshold | Mean POI candidate | Variance | Std. dev. | Mean Δ vs. raw v2 | Mean fire / EMS / school count Δ vs. raw | Defined tracts |
|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 0.12410 | 0.015889 | 0.12605 | +0.00261 | −0.09289 / −0.05209 / −0.72571 | 9,379 / 9,379 |
| 0.7 | 0.12563 | 0.015886 | 0.12604 | +0.00415 | −0.12614 / −0.07082 / −1.01624 | 9,379 / 9,379 |
| 0.9 | 0.12789 | 0.015841 | 0.12586 | +0.00641 | −0.19037 / −0.10017 / −1.44350 | 9,379 / 9,379 |

The outputs are deterministic, finite and in `[0,1]`, with exact official ID/order coverage; two complete builds were byte-identical. Regional and `ur_class` weighted-count/output moments, deltas, source coverage, and the v0/v2 measure-before tables are in `reports/poi-confidence-candidate.json`; the per-tract CSV and hash manifest are under `reports/candidates/`. Candidate SHA-256 is `28558424db768222ec7639986970af7a37b74228ef8f4e3190e5e58b6e19071c`; manifest SHA-256 is `061932cb63ce52b53924f73279540f603d2395b0e543f632888b501ebb0cf777`. Reproduce using:

```sh
uv run --with-requirements requirements.txt python scripts/build_poi_confidence_candidate.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --candidate reports/candidates/poi_confidence_weighted.csv \
  --manifest reports/candidates/poi_confidence_weighted_manifest.json \
  --out reports/poi-confidence-candidate.json
```

This is a fixed-threshold sensitivity, not an accuracy test: no labels or target RMSE exist, confidence is not validation, and no candidate was promoted or uploaded.

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

Every figure in this section is a C2/C3 provided-data proxy statistic computed over model
predictions. It is not a claim about true organiser scores, it is not an observed real-world
disparity measurement, and it does not establish causation.

The reproducible script is `scripts/discovery_rural_svi_minority.py`. It constructs a
within-region intersection not specified in the official description of the automated scorecard:

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

## Scorecard reflection

The row `bias-lb-snapshot-and-scorecard` snapshots where we stand and what the organiser's Bias
Scorecard says about our scored submissions. `scripts/snapshot_lb_and_scorecards.py` reads only the
shared orchestrator ledger plus one read-only GET of the authenticated submission status path, writes
the dated file `reports/leaderboard/2026-10-25T051219+0000.json` and one
`reports/scorecards/<submission_id>.json` per submission, and contains no upload path at all;
`reports/lb-scorecard-snapshot.json` is the machine record of the whole snapshot.

In that snapshot 1,441 participants had produced 390 ranked teams. Quoted to the six decimals the
snapshot stores, our best public RMSE was 0.091440 (P4, platform submission `ySXi73rd`) at rank 274:
0.007553 above the ranked median 0.083887, 0.024401 above the rank-50 line 0.067039, and 0.091011
above the rank-10 boundary 0.000429. We do not treat that boundary as a modelling target: six teams
record an exact zero and the best positive score on the board is 0.000092, so the snapshot reports the
median and rank-50 gaps beside it instead of implying that rank ten is reachable by modelling.

The scorecard is more informative than the single number. The all-zero anchor `uRRw9KpB`
(public 0.107246) returns all nine dimensions `undefined`, each with the organiser's reason
"Reference group has zero mean gap; ratio is undefined", so a degenerate prediction is legible in
this rubric on its own. The two region-constant-shaped submissions `BvyKrR6v` (public 0.146449)
and `ySXi73rd` carry identical scorecards - summer heat disparity ratio 1.141 (14.1% larger) and
tribal-versus-non-tribal 0.705 (29.5% smaller), with every other dimension "Similar" - while their
public RMSE differ (0.146449 against our best 0.091440): shrinking the whole prediction scale moved
the score and left the equity profile untouched. The component estimator `rTARuUaG` (public 0.235359) and its scaled
variant `TM83xxU6` (public 0.349410) are where the rubric bites: rural-versus-urban 0.248 (75.2%
smaller), tribal 0.556 (44.4% smaller) and wildfire 0.621 (37.9% smaller). Read honestly, our own
component predictions assign systematically smaller gaps to rural, tribal and high-wildfire tracts
than to their reference groups, and the organiser's rubric surfaces that even in submissions whose
composite RMSE is poor.

Per the operator amendment of `2026-10-24` this paragraph is reflection only. No feature, threshold,
weight or parameter in the model chain was changed on the strength of any number above, nothing was
uploaded to obtain them, and no scorecard value is an input to any candidate; the snapshot records
`scorecard_influenced_parameters: []` and `no_upload_performed: true`, and the audit behind it fails
closed if either ever changes. The rural/tribal/wildfire asymmetry is reported because the organisers
encourage reflecting on the scorecard, and because it is consistent with the descriptive findings in
`docs/writeup/bias-discovery.md`, which are evidence for follow-up investigation and not for any
score-driven adjustment.

## Shrink diagnosis — H1 rule-anchored phase (partial)

This diagnostic uses the frozen feature-only C2 component table and the organizer's published
aggregate definedness context. It reads no score placeholders, hidden/reference layers, external
data, or leaderboard/submission scores. The score distributions, component masks, clipping mass,
source-membership counts and input SHA-256s are in `reports/shrink-diagnosis.json`; reproduce with:

```sh
uv run --with-requirements requirements.txt python scripts/audit_shrink_diagnosis_h1.py \
  --workspace "$COMPETITION_WORKSPACE" --out reports/shrink-diagnosis.json
```

| Region | Our any-top-level-component undefined | Published any-reference-component undefined | Difference (pp) | C2 transport / building / POI defined rates | Overture named/route proxy ratio |
|---|---:|---:|---:|---:|---:|
| Maricopa AZ | 36.53% | 55% | −18.47 | 63.47% / 100% / 100% | 1.929 |
| Northern CA | 30.46% | 37% | −6.54 | 69.54% / 100% / 100% | 1.504 |
| Eastern OK | 40.69% | 21% | +19.69 | 59.31% / 100% / 100% | 1.161 |
| South-Central TX | 36.95% | 28% | +8.95 | 63.05% / 100% / 99.97% | 1.183 |

The direction reverses by region: our estimator has fewer undefined rows than the published
aggregate in Arizona/California, but more in Oklahoma/Texas. This flags region-specific H1 risk,
not a single global definedness correction. The organizer publishes only the aggregate
any-component rate, so these figures cannot validate individual component masks. The
named/route ratios are internal Overture named-length / selected Overture route-network-length
ratios; they are not regional Overture/TIGER ratios. Maricopa's 1.929 is outside the disclosed
cross-region range 0.71–1.59, while the other three are inside, but that comparison is not a
per-region target test.

On defined rows, C2's mean transport/building/POI gaps are respectively AZ
`0.15360/0.04905/0.31285`, Northern CA `0.11917/0.04657/0.23368`, Eastern OK
`0.07051/0.01922/0.19935`, and South-Central TX `0.08479/0.09167/0.25591`. The audit also
reports quantiles and exact 0/1 clipping mass for transport, building, the POI composite, fire,
EMS, schools, establishments, and the composite. These are proxy distributions, not observed
reference scores. The proxy recipes mix provided Overture road/building/place records with
provided strata land/population/urbanity features. Building and POI Overture source memberships
are multi-source and overlap (e.g. Microsoft/OSM/Esri for buildings and Meta/Microsoft/other
providers for places); their percentages describe input provenance, not correctness or signal
reliability. A road-source membership decomposition was not available in this audit.

**H1 verdict: mixed and inconclusive.** Aggregate definedness mismatches are substantial in two
regions in opposite directions, and the Maricopa Overture route denominator is a proxy concern;
neither establishes that a component was computed contrary to the official target definition or
explains candidate error without per-component reference masks/scores.

### H2: cross-construction reliability shrink

The preregistered H2 analysis is in `config/shrink-diagnosis-preregistered.json`; it uses only the
frozen feature-derived C2, A1, A2 and A3 prediction artifacts. P4 and V2 were excluded (V2's
registry forecast contains a public-score anchor). For each construction, its regional-centered
covariance with the other constructions estimates shared signal; pooled nonnegative signal shares
are normalized to construction weights. The consensus is then shrunk around its own regional mean
by `c = estimated shared cross-covariance / total consensus variance`. This makes the intercept
explicit and only attenuates within-region deviations. A zero target mean is not justified by
label-free evidence, so shrinkage is toward the feature-only regional consensus mean, not zero.

| Region | Raw regional c | Applied c | LOCO average MSE gain vs unshrunk consensus | S regional mean |
|---|---:|---:|---:|---:|
| Maricopa AZ | 0.7485 | 0.8017 pooled | 0.0004308 | 0.32434 |
| Northern CA | 0.7692 | 0.8017 pooled | 0.0004259 | 0.26479 |
| Eastern OK | 0.8035 | 0.8017 pooled | 0.0005637 | 0.23207 |
| South-Central TX | 0.8154 | 0.8017 pooled | 0.0004333 | 0.23988 |

Regional raw c spans only 0.0669, below the preregistered 0.10 cutoff; therefore a single pooled
`c=0.8016961` is applied rather than estimating four shrink parameters. Pooled shared variance is
`0.0041583`, total consensus variance `0.0051869`. Global construction weights are C2
`0.03299`, A1 `0.31457`, A2 `0.32827`, and A3 `0.32416`; all three alternate constructions carry
similar locally estimated agreement weights, while C2's agreement share is smaller. Leave-one-
construction-out proxy root-mean-square error is `0.149381` for the shrink estimator versus
`0.150876` for the unshrunk reliability-weighted consensus. The shrink MSE gain is positive in all
four regions, so the preregistered local-proxy gate passed. These held-out comparisons are against
another prediction method, not the organiser's target; the numeric `predicted_score` is a
cross-construction generalization proxy, not a measure of target performance. Target performance
is unidentifiable without reference values.

**H2 verdict: local cross-method agreement supports registering one shrink hypothesis, but does
not establish accuracy.** S is frozen and registered as
`S_crossmethod_reliability_shrink_v1` with purpose `improvement`; its score-only artifact is
`reports/candidates/S_crossmethod_reliability_shrink_v1.csv` (SHA-256
`26be20c569f72f94eb21711a41656eea909a92f3fb4495c1ec3b30f7a26a367a`). The registry's numeric
predicted score is the local LOCO proxy above, explicitly not a target RMSE. The platform-only
415 eastern-WA rows are zero formatting fills without target claims. No upload is authorized by
this row.

Reproduce H2 and finalize the registry entry with:

```sh
uv run --with-requirements requirements.txt python scripts/audit_shrink_diagnosis_h2.py \
  --workspace "$COMPETITION_WORKSPACE"
python scripts/register_shrink_diagnosis_candidate.py
```

The four constructions share underlying provided features, so their errors may be correlated;
common systematic bias can inflate the apparent signal share. Four methods are a small method set,
tracts are spatially dependent, and the region mean itself is unvalidated. Consequently, S is a
scientifically motivated candidate for later authorized evaluation, not evidence that shrinkage
improves hidden-target RMSE. No public scores, labels, removed references, external data, or
leaderboard-derived parameters informed the reliability estimates, weights, region rule, or S.
