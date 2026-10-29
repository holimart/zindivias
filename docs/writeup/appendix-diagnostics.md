# Appendix diagnostics — unsubmitted estimator screens

This supporting archive retains detailed reproducibility records for exploratory diagnostics that were not among the final uploaded submissions. It is linked for auditability; it is not part of the concise judge-facing methodology.

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

## Six sensitivity builders and candidate-stability diagnostics

- **Building morphology sensitivity:** `config/building-morphology-preregistered.json` froze six equal-weight support features and beta sensitivities `0.01/0.02/0.05` before the Overture building-geometry scan. The separate `B_MORPH_V1` diagnostic uses only provided Overture geometry/attributes and allowed `ALAND`/population context. For defined rows, `gap=0.01+beta*(1-morphology_support)`; zero-count rows remain undefined. At primary beta `0.02`, the component mean is `0.02045` over 9,377 defined tracts; all 9,377 defined tracts differ from the `0.01` constant prior, while the two zero-building tracts retain the undefined numeric placeholder. Scores range from `0.01530` to `0.02809` among defined rows, finite and within `[0,0.1]`. This is a preregistered feature-sensitivity result, not a target accuracy test or an approved scored candidate; no reference footprint count, label, or leaderboard result was used. Full region summaries, attribute missingness, and all three beta distributions are in `reports/building-morphology-candidate.json`, with the tract-level diagnostic component table in `reports/candidates/building_morphology.csv`. Reproduce with `/home/martin/hddProjects/langywrap/uv run --with-requirements requirements.txt python scripts/build_building_morphology_candidate.py --workspace "$COMPETITION_WORKSPACE" --candidate-csv reports/candidates/building_morphology.csv --out reports/building-morphology-candidate.json`.

- **Building-source sensitivity:** `config/building-source-prior-preregistered.json` froze three qualitative exclusive-source-mixture profiles and beta values `0.01/0.02/0.05` before candidate measurement. `B_SOURCE_V1` uses only Overture-derived source-class shares plus `ALAND`/`pop_total`; support is `0.8 × weighted source-class share + 0.2 × bounded population-density context`, with `gap=0.01+beta*(1-support)` when Overture count is positive. The primary profile at beta `0.02` averages `0.01651` (defined rows q10/median/q90 `0.01329/0.01574/0.02068`, max `0.02603`); all 9,377 defined rows differ from the constant `0.01` baseline. Across fixed source profiles, the beta-0.02 means are `0.01930` (conservative), `0.01651` (primary), and `0.01574` (Microsoft-heavy). Shares describe provenance, not geometric identity or completeness; the screen is not an accuracy test or approved scored candidate. `reports/building-source-prior.json` includes per-region source shares, source-feature coverage and all profile/beta distributions; `reports/candidates/building_source_prior.csv` contains the tract-level diagnostic table. Reproduce with `/home/martin/hddProjects/langywrap/uv run --with-requirements requirements.txt python scripts/build_building_source_prior.py --workspace "$COMPETITION_WORKSPACE" --candidate reports/candidates/building_source_prior.csv --out reports/building-source-prior.json`.

- **Cross-theme support sensitivity:** `config/cross-theme-support-preregistered.json` froze `XTS_C2_V2` before final adjustment measurement. It adds at most `0.05 × (1−cross_theme_support)` to the frozen C2 prediction, where cross-theme support is half the mean plus half the minimum of three bounded Overture features: Microsoft building-source share, the route-network/named-road length share, and the share of places with confidence ≥0.5. The 9,379-row official-scope prior C2 hash is fixed in the manifest; 119 road and two building/place rows with missing feature support use neutral 0.5 fills with explicit flags. Primary output changed 9,377/9,379 rows; mean adjustment was `0.02874`, max `0.04617`, and global prediction variance changed `0.02413→0.02507`. Per-region prior→adjusted variances were OK `0.01503→0.01582`, AZ `0.02136→0.02190`, Northern CA `0.02106→0.02183`, and South-Central TX `0.02620→0.02711`. Raw support correlations are descriptive (building/road Pearson `0.305`, building/place `0.014`, road/place `−0.081`). Overture source membership, route tags, and confidence are internal indicators, not truth or completeness measures; no accuracy claim or v2 promotion follows. The exact features, missingness, distributions, four-region correlations, candidate ID/bounds, and CSV/manifest hashes are in `reports/cross-theme-support.json`; the analysis-only candidate is `reports/candidates/cross_theme_support.csv`.

- **Schema optional-attribute presence sensitivity:** `config/schema-presence-preregistered.json` freezes all 44 modeled optional fields from the supplied POI, building, and road top-level schemas, with every remaining top-level field explicitly excluded or assigned an identity/geometry role. POI `confidence` and all three `sources` fields are excluded so this is distinct from confidence weighting and source-mixture analysis. The builder reads only those selected attributes plus `bbox`; it assigns bbox centers to provided official tract polygons and reads only the frozen C2 model prediction as the baseline. Null, present-empty, explicit scalar `unknown`/`unspecified`, mixed unknown/known, and known values are separate states; unknown is not counted as missing, and schema-unavailable is distinct from row-null. Across 44 optional-field drop perturbations, the maximum candidate-score change is `0.00327` (≤`0.05`); with all selected fields unavailable the candidate falls back exactly to C2. On 9,379 official IDs, 9,377 have feature-support diagnostics; 4,687 rows (49.97%) adjust. The bounded candidate delta mean/median/p95/max is `0.00121/0/0.00499/0.01539`; C2's all-row mean changes from `0.16898` to `0.17019`. Mean field-level presence rates by theme (equal average of declared field rates among bbox-assigned records) span POIs `76.05–76.70%`, buildings `14.90–18.91%`, and roads `18.24–24.33%` across regions; the complete 44-field × theme × region status matrix and C2/candidate moments are in `reports/schema-presence-candidate.json`. All selected fields were present in the actual Parquet schemas, no exact `unknown`/`unspecified` scalar token occurred in the configured categorical fields, and empty nested/list fields were retained as separate states. Candidate scores are finite/in `[0,1]`; two complete builds were byte-identical. This is an internal metadata/support sensitivity, not evidence of geographic completeness or accuracy; bbox-center assignment is approximate. No labels, sample score placeholders, `sources`, confidence, removed references/equivalents, external feature data, or leaderboard feedback were used, and no upload or accuracy claim was made. Candidate CSV SHA-256 `b06c649eab390eee0f77465d01e7c728bdc126f92270b5570d618723037e3f18`; manifest SHA-256 `f4450ee825de7a41b3c5c5df1f5aefb1808904aaf028837ad414296667d1c700`. Reproduce with `uv run --with-requirements requirements.txt python scripts/build_schema_presence_candidate.py --workspace "$COMPETITION_WORKSPACE" --candidate reports/candidates/schema_presence_candidate.csv --manifest reports/candidates/schema_presence_candidate_manifest.json --out reports/schema-presence-candidate.json`.

- **Density-context sensitivity:** `config/density-context-preregistered.json` froze `DENSITY_C2_V1`, the 5 km/20 km centroid radii, log-density scales, and ≤0.05 positive adjustment before measuring the context features. It averages neighboring official-tract ACS population-density and Overture-building-density support, excluding each focal tract and indexing neighbors separately within each of the four regions. The inputs are the provided `ALAND`/`pop_total`/`ur_class`, Overture building counts, and only `GEOID,geometry` from the provided ACS tract polygon table; housing-unit and all other ACS columns are not read. Relative to frozen C2 on 9,379 official rows, 9,147 changed; mean candidate delta was `0.01035`, maximum `0.04720`, and prediction variance changed `0.02413→0.02338`. Delta means were Rural `0.01924` (n=1,712), Urban `0.00836` (n=7,641), and unclassified `0.00864` (n=26), descriptive only. Every region passed the leave-one-tract-out perturbation check (zero change), and the full 9,794-row template output uses the approved zero formatting fill for 415 featureless eastern-WA rows. The candidate is not an accuracy test or approved model integration; `reports/density-context-candidate.json` includes pre/post moments, region/urbanity coverage, missingness, neighbor summaries, bounds, and hashes; the analysis artifact is `reports/candidates/density_context_candidate.csv`.

- **Facility spatial-catchment sensitivity:** `config/facility-catchment-preregistered.json` froze the exact core fire/EMS/school categories, same-region pool, 1/5/10 km centroid radii, and a fixed 5% relative presence-prior uplift before spatial measurement. The builder reads only provided Overture POI `bbox,categories`, ACS `GEOID,geometry`, permitted strata fields, and the existing Overture summary columns needed to reproduce the pooled logistic baseline. Selected point counts assigned to official tracts reproduce the pinned audit's facility zero counts exactly: fire 5,892/9,379 (62.82%), EMS 7,282/9,379 (77.64%), schools 1,088/9,379 (11.60%). It applies `p_ref' = min(1, p_ref × (1 + 0.05 × (1−exp(−d_nearest/r))))` before the existing one-count-ahead expected-gap transform; no logistic coefficient is fitted. The official-scope facility-half mean changes from `0.12566` to `0.12751` at the 5 km primary radius (mean delta `0.00185`, max `0.00905`); 1 km mean/max delta is `0.00444/0.01373`, and 10 km is `0.00109/0.00726`. At 5 km, population-weighted centroid catchment shares across fire/EMS/schools are AZ `0.904/0.754/0.925`, Northern CA `0.836/0.692/0.890`, Eastern OK `0.806/0.691/0.860`, and South-Central TX `0.897/0.822/0.932`. The report includes per-type nearest-distance quantiles, region/urbanity deltas, zero-population cases (26 tracts), zero-ALAND cases (2), and edge/sparse catchments (220 tracts have none of the three facility types within 10 km). No USGS list/coordinates, external POI, removed reference, target, or leaderboard information was used. This is an analysis-only spatial sensitivity, not service-area evidence or an accuracy test. Reproduce with `uv run --with-requirements requirements.txt python scripts/build_facility_catchment_candidate.py --workspace "$COMPETITION_WORKSPACE" --candidate reports/candidates/facility_catchment_candidate.csv --manifest reports/candidates/facility_catchment_candidate_manifest.json --out reports/facility-catchment-candidate.json`; full radius/missingness/weighting diagnostics and SHA-256 values are in the report and manifest.

- **Candidate stability envelope:** `config/candidate-stability-envelope-preregistered.json` freezes six complete feature-derived score variants: C2 mechanistic baseline; cross-theme support; density context; a 5 km facility-catchment replacement over the v0 transport/building/CBP components; building morphology β=0.02; and primary source-prior β=0.02. The facility-catchment and two building variants are recomposed with the existing defined-component mean; cross-theme and density use their frozen full-score candidates. No target, public score, or fitted weights are used. The envelope is an analysis-only diagnostic; its equal-weight mean is not promoted or submitted, and the screen does not satisfy or bypass any scored-integration gate. It uses only the 9,379 official IDs; 415 template-only rows in C2/density files are ignored without parsing their candidate values. All six candidates align and are finite/in `[0,1]`. Spread median/p95/max is `0.11514/0.27016/0.51499`; `75.99%` of rows exceed `0.05`, so the preregistered `p95 ≤ 0.10` criterion correctly flags operator review. Region p95 spread is `0.20209` Eastern OK, `0.22034` Maricopa, `0.20836` Northern CA, and `0.33936` South-Central TX. Two no-defined-component edge rows in each building variant carry forward the existing C2 score; this deterministic fallback is disclosed. Candidate distributions, input hashes, feature-availability rates, and region × provided urbanity summaries are in `reports/candidate-stability-envelope.json`; the long-form member table and per-tract envelope CSV hashes are in its manifest. Two full builds were byte-identical. This is model-specification spread only: no local RMSE or empirical accuracy claim is available.

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

## T1 named-stroke class-drop diagnostic and implementation limit

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
