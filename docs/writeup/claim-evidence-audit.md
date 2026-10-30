# Claim-to-evidence audit for the Bias Bounty writeup

Row: `bias-writeup-claim-evidence-audit`. Documentation screen: every quantitative statement in the best bias discovery / best documentation writeup must carry an exact evidence pointer and a reproducible command; not a model-promotion or accuracy assessment.

## Verdict

- **83 of 83 quantitative claims fully traceable** (100.0%).
- **967 of 967 numeric tokens (100.0%) are present in the evidence named by the claim that states them.**
- 83 per-claim verify commands and 21 reproduce commands; 19/20 recorded reproduce-command exits are 0; 1 command(s) lack an execution record in this no-`--execute-reproduce` audit pass; all recorded reproduce-command product hashes match the current files. 2 executed reproduce command(s) generated product differences not covered by the registered disclosure rules; see the audit JSON's tier_b_reproduce entries.
- Accuracy language outside the allowed classes: none. Prohibited reference/label/leaderboard sources used as scored-model evidence: none.
- All acceptance gates: **FAIL**.

## Method

Each registered writeup source document is split into quantitative claim blocks (a block is a paragraph or a table containing at least one number outside a fenced command). Every number in a block is a token; a token is *covered* when the same value appears in an evidence artifact named by that claim, allowing for the decimal places actually written and for fraction/percentage presentation. Identifier-like digit strings (bare GEOIDs) are not claims. Every claim names a primary artifact plus an exact pointer into it, is checked against the live file hash, carries a per-claim verify command that must exit 0, and carries the command that regenerates its artifact.

## Claims by document

| Document | Claims | Fully traceable | Numeric tokens | Covered |
|---|---:|---:|---:|---:|
| `docs/writeup/bias-discovery.md` | 14 | 14 | 123 | 123 |
| `docs/writeup/methodology-summary.md` | 1 | 1 | 3 | 3 |
| `docs/writeup/methodology.md` | 46 | 46 | 535 | 535 |
| `docs/writeup/edge-cases.md` | 7 | 7 | 131 | 131 |
| `docs/writeup/appendices/screens-that-did-not-produce-a-finding.md` | 12 | 12 | 104 | 104 |
| `docs/writeup/appendices/san-antonio-svi-gradient.md` | 3 | 3 | 71 | 71 |

## Claim inventory

Each row is one quantitative claim: where it is stated, the artifact and pointer that substantiate it, and how to reproduce that artifact. `Class` is the provenance of the statement, not a claim of accuracy.

| Claim | Document | Section | Lines | Class | Primary evidence | Pointer | Tokens | Verify | Reproduce |
|---|---|---|---|---|---|---|---:|---|---|
| `bias-discovery-001` \| Region \| Rural mean \| Urban mean \| Rural − urban (percentage points)… | `bias-discovery.md` | Evidence | 9-14 | `measured_provided_data` | `reports/source-provenance.json` | `/regions/eastern-ok/spearman_correlations/building_osm_share/pct_urban/n` | 33/33 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-002` Values are copied from `reports/source-provenance.json` (SHA-256 `e801… | `bias-discovery.md` | Evidence | 16-16 | `measured_provided_data` | `reports/source-provenance.json` | `/statistics/stratum_means` | 1/1 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-003` Lead: border metros have machine-only footprints with an Overture thir… | `bias-discovery.md` | Impact and novelty | 30-30 | `measured_external_discovery_data` | `reports/discovery-border-transfer.json` | `/` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with-req…` |
| `bias-discovery-004` CDC PLACES 2025 estimates uninsured prevalence among adults aged 18–64… | `bias-discovery.md` | Impact and novelty | 32-32 | `measured_external_discovery_data` | `reports/who-is-affected-cdc-places.json` | `/contrasts/ten border counties vs rest of South-Central Texas` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with-req…` |
| `bias-discovery-005` Across 460 border tracts, OSM mean is 9.18% vs 61.48% elsewhere (68.66… | `bias-discovery.md` | Evidence and mechanism | 36-36 | `measured_external_discovery_data` | `reports/discovery-border-metros.json` | `/results/adjusted_border_association_plus_svi_hispanic/border_coefficient` | 12/12 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with-req…` |
| `bias-discovery-006` `Other` is an Overture `sources` label, not removed Google Open Buildi… | `bias-discovery.md` | Evidence and mechanism | 38-38 | `measured_external_discovery_data` | `reports/discovery-border-transfer.json` | `/other_source_context/bexar_cache/footprint_weighted_other_source_share` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with-req…` |
| `bias-discovery-007` “Google-only” denotes an Overture provider tag, not a removed referenc… | `bias-discovery.md` | Does it transfer? | 42-42 | `measured_external_discovery_data` | `reports/source-provenance.json` | `/` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with-req…` |
| `bias-discovery-008` \| County \| OSM \| Third-provider tag \| Esri \| \|---\|---:\|---:\|---:\| \| **… | `bias-discovery.md` | Does it transfer? | 44-50 | `measured_external_discovery_data` | `reports/discovery-border-transfer.json` | `/results` | 15/15 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with-req…` |
| `bias-discovery-009` In six Paradise/Magalia Butte County tracts, provided Wildfire Risk to… | `bias-discovery.md` | Impact and evidence | 62-62 | `measured_external_discovery_data` | `reports/discovery-postfire.json` | `/focal_butte_2018_burn_geoids` | 4/4 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-010` Focal GEOIDs and DINS rows appear in [Appendix J](#appendix-j-supporti… | `bias-discovery.md` | Impact and evidence | 64-64 | `measured_external_discovery_data` | `reports/discovery-postfire.json` | `/` | 2/2 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-011` **Negative mechanism check.** DINS supports neither the proposed fire-… | `bias-discovery.md` | Impact and evidence | 68-68 | `measured_external_discovery_data` | `reports/discovery-dins-mechanism.json` | `/spearman/statistic` | 14/14 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-012` Focal shares show Microsoft-only 0.430–0.990 versus OSM-only 0.001–0.0… | `bias-discovery.md` | Impact and evidence | 70-70 | `measured_external_discovery_data` | `reports/postfire-lineage-recheck.json` | `/focal_share_ranges/microsoft_only_min` | 12/12 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-013` Entries #35520 and #35527 (23 Oct) provide stronger DINS-matched wildf… | `bias-discovery.md` | Impact and evidence | 72-72 | `measured_external_discovery_data` | `docs/writeup/related-entries.md` | `document` | 4/4 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-014` Of 858 `tribal_any` tracts, 805 (93.82%) are in Eastern Oklahoma; 6,00… | `bias-discovery.md` | Evidence | 84-84 | `measured_provided_data` | `reports/discovery-strata-confound.json` | `/stratum_composition/non_tribal/by_region/south-central-tx/tracts` | 6/6 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `methodology-summary-001` Final-two: `V2_model_cgs` (`hvLtPVmr`; SHA-256 `5d0607d5531793008d8d5c… | `methodology-summary.md` | What the submitted score is | 3-3 | `scope_statement` | `reports/final-two.json` | `/scope/platform_template_rows` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-001` Two already-submitted files carry the final-two candidates: `V2_model_… | `methodology.md` | What the submitted score is | 7-7 | `measured_model_only` | `docs/data-inventory.md` | `document` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-002` - **Transport**: `T_i = clip(1 − named_road_km_5070_i / route_km_i, 0,… | `methodology.md` | What the submitted score is | 11-11 | `measured_model_only` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-003` - **Buildings**: `B_i = 0.01` iff the provided Overture building count… | `methodology.md` | What the submitted score is | 12-12 | `measured_model_only` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-004` - **POI**: For fire, EMS, and schools, `p_t = sigmoid(a_t + b_t·log1p(… | `methodology.md` | What the submitted score is | 13-13 | `measured_model_only` | `reports/candidates.json` | `/candidate_set` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-005` **Coverage: 100% of the documentation items listed below.** Completion… | `methodology.md` | Rubric checklist and reading guide | 28-28 | `scope_statement` | `reports/candidates.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with-req…` |
| `methodology-006` The official scoring domain is the union of four published per-region … | `methodology.md` | Data sources, scope, and provenance | 42-42 | `input_inventory` | `docs/competition.md` | `document` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-007` \| Region \| Official sample \| ACS geometry parquet bytes / rows \| Overt… | `methodology.md` | Data sources, scope, and provenance | 55-60 | `input_inventory` | `docs/data-inventory.md` | `document` | 44/44 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-008` \| Region \| Official sample CSV bytes; SHA-256 \| Local strata parquet S… | `methodology.md` | Data sources, scope, and provenance | 64-69 | `input_inventory` | `reports/v2-input-provenance.json` | `/inputs/provided_local_copies/samples/maricopa-az-sample-submission.csv/bytes` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-009` Audit reports additionally record row-group/projection information and… | `methodology.md` | Data sources, scope, and provenance | 71-71 | `input_inventory` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-010` The deterministic C2/v0 screen uses only provided Overture feature sum… | `methodology.md` | Organiser scoring context and our calc | 77-77 | `model_specification` | `scripts/estimate_gaps.py` | `document` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-011` \| Component \| C2/v0 expected reference proxy \| Definedness and compone… | `methodology.md` | Organiser scoring context and our calc | 79-86 | `model_specification` | `scripts/estimate_gaps.py` | `def:sha256` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-012` For missing urbanity, use finite `pct_urban`; otherwise use `pop_urban… | `methodology.md` | Organiser scoring context and our calc | 88-88 | `model_specification` | `scripts/estimate_gaps.py` | `def:sha256` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-013` Historical alternatives C1 (region constants), C3 (v0 denominators ×1.… | `methodology.md` | Alternative components and aggregation | 94-94 | `historical_disclosure` | `reports/candidates.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-014` - **Buildings:** the submitted building term is a fixed gap of `0.01` … | `methodology.md` | Provided-feature model components | 106-107 | `measured_model_only` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-015` The full edge-case table and calculation policy are in [`edge-cases.md… | `methodology.md` | Edge cases, bias discovery, and interp | 114-114 | `measured_provided_data` | `reports/edge-cases.json` | `/geometry_method` | 11/11 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-016` The opt-in route audit is `scripts/estimate_gaps.py --route-audit-only… | `methodology.md` | Route-reference transport v2 (provided | 140-140 | `measured_provided_data` | `scripts/estimate_gaps.py` | `document` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-017` \| Region \| `sum(L_named)/sum(L_route)` \| `L_named` / `L_route` km \| `L… | `methodology.md` | Route-reference transport v2 (provided | 144-149 | `measured_provided_data` | `reports/transport-route-ref-audit.json` | `/regions/maricopa-az/sum_L_named_over_sum_L_route` | 28/28 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-018` All four route-v2 transport + v0 building/POI any-undefined shares are… | `methodology.md` | Route-reference transport v2 (provided | 151-151 | `measured_provided_data` | `reports/transport-route-ref-audit.json` | `/regions/maricopa-az/sum_L_named_over_sum_L_route` | 10/10 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-019` The measure-before route-reference component is recomputed as `clip(1 … | `methodology.md` | Connector-graph road topology sensitiv | 157-157 | `measured_provided_data` | `reports/road-topology-candidate.json` | `/candidate_rows` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-020` The frozen adjustment is `0.05 × (1 − largest_component_node_share)` a… | `methodology.md` | Connector-graph road topology sensitiv | 159-159 | `measured_provided_data` | `reports/road-topology-candidate.json` | `/measure_after/diagnostics_and_candidate_by_region_and_urban_rural/all/topology_defined_rows` | 14/14 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-021` The edge audit processed 3,143,887 supplied road-segment rows and 8,49… | `methodology.md` | Connector-graph road topology sensitiv | 161-161 | `measured_provided_data` | `reports/road-topology-candidate.json` | `/measure_after/diagnostics_and_candidate_by_region_and_urban_rural/south-central-tx:Urban/topology_graph/dead_end_nodes_per_tract/variance_population` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-022` `reports/transport-route-variants.json` compares the three cumulative … | `methodology.md` | Route denominator sensitivity and sele | 175-175 | `measured_provided_data` | `reports/transport-route-variants.json` | `/` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-023` \| Variant \| Regions inside disclosed 0.71–1.59 ratio band (of 4) \| Reg… | `methodology.md` | Route denominator sensitivity and sele | 177-181 | `measured_provided_data` | `reports/transport-route-variants.json` | `/aggregate_variant_metrics` | 14/14 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-024` All three variants put all four hybrid any-undefined shares within 0.1… | `methodology.md` | Route denominator sensitivity and sele | 183-183 | `measured_provided_data` | `reports/transport-route-variants.json` | `/regions/south-central-tx/variants/state_us_interstate/sum_L_named_over_sum_L_route` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-025` The audit read only official `GEOID` cells from the per-region sample … | `methodology.md` | POI numerator audit and selected categ | 216-227 | `measured_provided_data` | `reports/sample-contract-audit.json` | `/authoritative_rows` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-026` \| Region \| Official tracts \| Selected fire / EMS / school POI rows \| `… | `methodology.md` | POI numerator audit and selected categ | 248-253 | `measured_provided_data` | `reports/poi-category-audit.json` | `/regions/maricopa-az/tract_index/rows` | 56/56 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-027` `Sources.dataset` is a multi-valued property: dataset row counts are n… | `methodology.md` | POI numerator audit and selected categ | 255-262 | `measured_provided_data` | `reports/poi-category-audit.json` | `/regions/south-central-tx/category_frequency` | 16/16 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-028` \| Region \| Core zero tracts fire / EMS / school \| All fire/rescue cate… | `methodology.md` | POI numerator audit and selected categ | 264-269 | `measured_provided_data` | `reports/poi-category-audit.json` | `/regions/maricopa-az/tract_zero_sensitivity/core_v0/fire/zero_tracts` | 100/100 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-029` `scripts/audit_place_duplicates.py` compares raw selected facility row… | `methodology.md` | 50 m same-name facility dedup sensitiv | 285-285 | `measured_provided_data` | `reports/place-dedup.json` | `/policy/distance` | 5/5 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-030` \| Region \| Facility rows, raw → deduplicated \| Facility rows removed \|… | `methodology.md` | 50 m same-name facility dedup sensitiv | 287-293 | `measured_provided_data` | `reports/place-dedup.json` | `/regions/maricopa-az/facility_rows/raw_unique_rows` | 50/50 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-031` All 36 removed rows are school duplicates; no fire or EMS row is remov… | `methodology.md` | 50 m same-name facility dedup sensitiv | 295-295 | `measured_provided_data` | `reports/poi-facility-miss-audit.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-032` Here `n_overture_t` is the selected exact-category count plus the prov… | `methodology.md` | Facility-miss POI v2 (fixed prior; mod | 331-331 | `measured_model_only` | `reports/poi-facility-miss-audit.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-033` \| Type \| Intercept \| Density slope \| Urbanity slope \| \|---\|---:\|---:\|-… | `methodology.md` | Facility-miss POI v2 (fixed prior; mod | 335-339 | `measured_model_only` | `reports/poi-facility-miss-audit.json` | `/` | 9/9 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-034` With the prior frozen, the audit reports the observed Overture zero co… | `methodology.md` | Facility-miss POI v2 (fixed prior; mod | 341-341 | `measured_model_only` | `reports/poi-facility-miss-audit.json` | `/summaries/maricopa-az/poi_v2_mean_defined` | 16/16 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-035` Before weighting, raw core counts are 5,242 fire, 2,739 EMS, and 39,43… | `methodology.md` | POI confidence-weighted count sensitiv | 358-358 | `measured_model_only` | `reports/poi-confidence-candidate.json` | `/measure_before/source_and_confidence_coverage/fire/selected_rows` | 11/11 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-036` \| Confidence threshold \| Mean POI candidate \| Variance \| Std. dev. \| M… | `methodology.md` | POI confidence-weighted count sensitiv | 360-364 | `measured_model_only` | `reports/poi-confidence-candidate.json` | `/measure_after/threshold_candidates/POI_CONFIDENCE_T050/all/score/mean` | 27/27 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-037` The deterministic diagnostic in `scripts/discovery_confidence_by_strat… | `methodology.md` | Overture POI confidence by SVI and urb | 380-390 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-038` Quartiles are region-specific over official scored tracts with finite … | `methodology.md` | Overture POI confidence by SVI and urb | 392-399 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/definitions/intervals` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-039` \| Region \| All-place confidence q10 / q25 / median \| All places `<0.5`… | `methodology.md` | Overture POI confidence by SVI and urb | 401-406 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/regions/maricopa-az/overall_confidence_quantiles_q10_q25_q50_q75_q90/q10` | 40/40 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-040` The observed pattern is opposite the initial hypothesis that higher-SV… | `methodology.md` | Overture POI confidence by SVI and urb | 408-417 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-041` 1. Restrict to the authoritative South-Central TX sample. | `methodology.md` | Discovery screen | 438-438 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-042` 2. Define the rural screen as provided `pct_urban < 0.5`. | `methodology.md` | Discovery screen | 439-439 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-043` 3. Among rural tracts with nonmissing `svi_minority`, calculate the em… | `methodology.md` | Discovery screen | 440-440 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/group_definition/upper_quartile_cutoff_inclusive` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-044` 4. Compare mean C2 predicted `coverage_gap_score` across those groups.… | `methodology.md` | Discovery screen | 441-441 | `measured_provided_proxy` | `scripts/estimate_gaps.py` | `def:sha256` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-045` 5. Compute 95% percentile intervals from 50,000 bootstrap resamples of… | `methodology.md` | Discovery screen | 442-442 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/bootstrap_replicates` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-046` The screen found 248 high-field rural tracts and 739 other rural tract… | `methodology.md` | Discovery screen | 444-444 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/high_group_mean` | 5/5 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-001` This table makes the handling explicit for low-information and structu… | `edge-cases.md` | Edge-case register for the coverage-ga | 3-3 | `scope_statement` | `docs/data-inventory.md` | `document` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-002` \| Region \| Official n \| Population 0 \| `ALAND` 0 \| Water proxy >50% \| … | `edge-cases.md` | Observed edge cases | 7-13 | `measured_provided_data` | `reports/v2-defined-mask-audit.json` | `/scope/region_rows/maricopa-az` | 55/55 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-003` Water is an approximation, not an independently observed water mask: f… | `edge-cases.md` | Observed edge cases | 15-15 | `measured_provided_data` | `reports/edge-cases.json` | `/geometry_method` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-004` The audit now applies the fixed provided-feature v2 component function… | `edge-cases.md` | Model-only v2 predictions for edge gro | 19-19 | `measured_model_only` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 4/4 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-005` \| Edge group \| Region \| n \| Facility-half v2 mean [q10–q90] \| Partial … | `edge-cases.md` | Model-only v2 predictions for edge gro | 23-33 | `measured_model_only` | `reports/edge-cases.json` | `/regions/maricopa-az/v2_edge_case_predictions/missing_pct_urban/predictions/facility_half_gap/mean` | 46/46 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-006` \| Case \| Method behavior \| Explicit limitation / safeguard \| \|---\|---\|… | `edge-cases.md` | Handling policy | 39-50 | `policy_statement` | `docs/data-inventory.md` | `document` | 15/15 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-007` The audit reads only sample GEOID column 0 (not score-placeholder cell… | `edge-cases.md` | Reproduce the aggregate audit | 63-63 | `reproduction_statement` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-001` The analytic group is defined using only provided strata fields: `pct_… | `screens-that-did-not-produce-a-finding.md` | Proposed pattern outside the published | 13-13 | `measured_provided_data` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-002` \| Group \| Tracts \| Mean C2 predicted composite \| \|---\|---:\|---:\| \| Rur… | `screens-that-did-not-produce-a-finding.md` | Quantified proxy evidence | 17-21 | `measured_provided_data` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/high_group_mean` | 4/4 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-003` A 50,000-resample tract bootstrap gives a 95% percentile interval of *… | `screens-that-did-not-produce-a-finding.md` | Quantified proxy evidence | 23-23 | `measured_provided_data` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/bootstrap_replicates` | 5/5 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-004` \| Component \| Upper quartile (n; mean) \| Other rural (n; mean) \| Diffe… | `screens-that-did-not-produce-a-finding.md` | Quantified proxy evidence | 27-31 | `measured_provided_data` | `reports/discovery-rural-svi-minority.json` | `/C2_component_results_defined_only/transport/high_group_defined_mean` | 19/19 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-005` \| South-Central TX GEOID \| `svi_minority` \| C2 composite \| C2 transpor… | `screens-that-did-not-produce-a-finding.md` | Quantified proxy evidence | 37-43 | `measured_provided_data` | `reports/discovery-rural-svi-minority.json` | `/named_high_group_tracts_sorted_by_C2_estimate` | 25/25 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-006` The 50,000-draw bootstrap uses fixed seeds; a second run generated byt… | `screens-that-did-not-produce-a-finding.md` | Reproduction | 62-62 | `measured_provided_data` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/bootstrap_replicates` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-007` **Scope and interpretation.** This is a follow-on discovery-only scree… | `screens-that-did-not-produce-a-finding.md` | Section 4 — External public context la | 66-66 | `measured_provided_data` | `reports/discovery-external-layers.json` | `/external_sources` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-008` **CDC/ATSDR SVI 2022.** On the 9,294 complete cases (85 selected SVI v… | `screens-that-did-not-produce-a-finding.md` | Section 4 — External public context la | 68-68 | `measured_provided_data` | `reports/discovery-external-layers.json` | `/external_sources` | 12/12 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-009` **USDA ERS RUCA 2020.** RUCA joined for all 9,379 official GEOIDs; com… | `screens-that-did-not-produce-a-finding.md` | Section 4 — External public context la | 70-70 | `measured_provided_data` | `reports/discovery-external-layers.json` | `/external_sources` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-010` **Census PDB 2026 broadband context.** The selected exposure was tract… | `screens-that-did-not-produce-a-finding.md` | Section 4 — External public context la | 72-72 | `measured_provided_data` | `reports/discovery-external-layers.json` | `/external_sources` | 12/12 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-011` **Census AIANNH 2020 tribal-area polygons.** We dissolved overlapping … | `screens-that-did-not-produce-a-finding.md` | Section 4 — External public context la | 74-74 | `measured_provided_data` | `reports/discovery-external-layers.json` | `/external_sources` | 10/10 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `screens-that-did-not-produce-a-finding-012` The config is `config/discovery-external-layers-preregistered.json`; t… | `screens-that-did-not-produce-a-finding.md` | Section 4 — External public context la | 87-87 | `measured_provided_data` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `san-antonio-svi-gradient-001` This descriptive check uses only official sample GEOIDs, provided Over… | `san-antonio-svi-gradient.md` | Evidence and method | 3-3 | `measured_provided_data` | `reports/san-antonio-svi-gradient.json` | `/definitions/bootstrap` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with-req…` |
| `san-antonio-svi-gradient-002` \| Scope \| Q1 \| Q2 \| Q3 \| Q4 \| Q4−Q1, pp (95% CI) \| n (Q1/Q4) \| \|---\|--… | `san-antonio-svi-gradient.md` | Evidence and method | 5-12 | `measured_provided_data` | `reports/san-antonio-svi-gradient.json` | `/` | 53/53 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with-req…` |
| `san-antonio-svi-gradient-003` Bexar County's 372 tracts show a higher-vulnerability tail: Q4 has 139… | `san-antonio-svi-gradient.md` | Evidence and method | 14-14 | `measured_provided_data` | `reports/san-antonio-svi-gradient.json` | `/bexar_county/quartiles/q4/population_sum` | 16/16 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with-req…` |

## Evidence artifacts and hashes

| Artifact | SHA-256 | Claims |
|---|---|---:|
| `config/poi-category-definitions.json` | `8309a0bb08613b92621f4977eb78998e469951c0cd0ed012d93ea2f5cf59654f` | 3 |
| `config/poi-confidence-weighting-preregistered.json` | `f93ffa0153c6e2207dc99ae1a0d44753371a2ce173f71eb64feb5d17e0e46296` | 1 |
| `config/reproduction-inputs.json` | `0172220d2a463be4cd9ca014439363bb490994a59709b576bc6474d7546454bd` | 3 |
| `docs/competition.md` | `381e8389194f278adc808ec023a688d727cab94bb539335049191261d2fa1d28` | 1 |
| `docs/data-inventory.md` | `88fe309bd2a577d544081fd95ddc7980e77a4e4751d175eab7d5752e7f4ce950` | 31 |
| `docs/writeup/related-entries.md` | `a4547537f23be97e86e96631704daacc8d59a50c9f81400d28726712240043a6` | 1 |
| `reports/building-v2-audit.json` | `4815735bbefcd155a8bd566fc92403e378d436aa97dc062344f36fcc9f7936d9` | 1 |
| `reports/candidates.json` | `799d0857fd25b12ead43e091d53799af0a325dfe510553410dd3cd580f5fa815` | 7 |
| `reports/confidence-by-stratum.json` | `ac759312e2b3e1384078c2d46dc386a9c2a2905ae5c6366d1247c13ee6916053` | 4 |
| `reports/discovery-border-metros.json` | `27779986d787594abdadf741c983db13dedf25fb476cc4ff2ae096a1a4260244` | 5 |
| `reports/discovery-border-transfer.json` | `64037e7360dab85eae3f748ec5d0ec14155999587302ae12e83bd3f9a893b795` | 4 |
| `reports/discovery-dins-mechanism.json` | `8af173a124f59ef7fc6a114907eb65340cc9f87bde7093db0eb7441fc7d3ccfd` | 4 |
| `reports/discovery-external-layers.json` | `6d5c68655af215fcea7f1eb01e05d5065e48ec2f2c8a7786317d8d2ad9a24998` | 12 |
| `reports/discovery-postfire-county-bootstrap.json` | `0bd5dedf9fbfda5f590262290119f433737283fddda79793f87e573da736bc74` | 2 |
| `reports/discovery-postfire.json` | `cbf100bc833bf09c02bad9f3c11a53d0da0736a7a249018c1ecc8dce55f3d49a` | 4 |
| `reports/discovery-rural-svi-minority.json` | `da1a581ddb25347cc6a0ffd2bb06cb8a1811082d08973bbfefc04e0c5c08cbe0` | 18 |
| `reports/discovery-strata-confound.json` | `ce0f1307793d1c658754cb7f9dd6fee7ea9cabf2a8a0e7ccb8e9888c2bad9ecb` | 1 |
| `reports/edge-cases.json` | `53626e3ad297c4609e541a7570146b732677275d2d371f17eac6606361911237` | 8 |
| `reports/estimator-v1-audit.json` | `1d72712e63f9b8f9682cd82a3cebc888be8bf0301cfc82cda05a2012a417ef5a` | 3 |
| `reports/estimator-v2-audit.json` | `131376c0474244b5c1c1cad27d0dd1ce1f5513a01c599a6bc53f79c9ae8bc4d0` | 5 |
| `reports/final-two.json` | `c31358286078bd1d0b7ff87deb791a63cd0df51b45b53da13de27c678ea22361` | 1 |
| `reports/gap-estimator-audit.json` | `a872e2dd62a5333037bc8e663278134c5b0a08493c7ec41768962e33859187f9` | 2 |
| `reports/place-dedup.json` | `de03f53c37fbb9a909b2f45172ede1a58bc059371e22837738df56a54bcd3cba` | 3 |
| `reports/poi-category-audit.json` | `cd48fa40d0fb1b3cdd1e431734acb646791916df02ee021a60243020b17d9de8` | 5 |
| `reports/poi-confidence-candidate.json` | `23c49c00105c5936baa094a3ce0f5a23e41589939c6fea972870f1fde794370e` | 3 |
| `reports/poi-facility-miss-audit.json` | `b559c8d3d85709d0e5f2d2102bb39da4d8255a3c62f728cf736da522025a289c` | 5 |
| `reports/postfire-lineage-recheck.json` | `52c16e28258e3b3ca73a3135a81643725a9230372078f5f40c5827d75c1642ca` | 2 |
| `reports/road-topology-candidate.json` | `4a8b630e865609ae23e8825c864c771ed319d63e5c27eb86158e1dac6dcfc6ce` | 3 |
| `reports/sample-contract-audit.json` | `e9a56f30ea7c5a09bf94ebc95d2bf6b1e7559456ee8cf1c16304d9137175dbd0` | 6 |
| `reports/san-antonio-svi-gradient.json` | `d040d3c857103fa890c061291259d798cab136570874386f0bc9e2465b86d70f` | 3 |
| `reports/source-provenance.json` | `e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565` | 3 |
| `reports/t1-stroke-residual-screen.json` | `d717571b5a5dcc6e7bddf84398082ca44118b4b39b22c905d92cd7bee250e3b9` | 12 |
| `reports/transport-route-ref-audit.json` | `bf904336963f553cc7442c5996a4d8d504fe30aad5c4f1a2e44da8fa773b1927` | 9 |
| `reports/transport-route-variants.json` | `2a1acc760aadd2fce3a1df2afae6bfd9aaaef2a2ce510df8b674bccb9eb11aa2` | 3 |
| `reports/v2-component-contribution-audit.json` | `dd1fb9696932cec9006f0ea487516418b49004317005f7368ea89cbcb6db597d` | 1 |
| `reports/v2-defined-mask-audit.json` | `fea33ef7daeeeac2fd187803481e050085698afd6f44ed182e9c5af5028054d2` | 8 |
| `reports/v2-input-provenance.json` | `b52c3b1bcfee452e8d04c2af28a8c9abd1a0fb609b50c1ed9a9d56b9ec7f5d6a` | 5 |
| `reports/who-is-affected-cdc-places.json` | `6b007fbed27f613b54c29ae1ac28e759c52c68bd5e8f9fabb68c115ad58e3ef7` | 1 |
| `scripts/audit_place_duplicates.py` | `03717aa54a75a6e55bbe6b85f65dd66f4cd1cae88632b12c5006087247da1e90` | 1 |
| `scripts/discovery_confidence_by_stratum.py` | `35a30e0a87ad016d3e936be4437084cffc91f708ef5c6692886c38805026d9a1` | 1 |
| `scripts/estimate_gaps.py` | `4f59982e0349af048ba11efbd5dbc629076eef8d3c8dc08d5747da5833bfc4fe` | 5 |

## Commands

Per-claim verify commands (executed by the audit; each must exit 0):

```sh
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-001
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-002
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-003
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-004
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-005
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-006
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-007
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-008
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-009
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-010
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-011
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-012
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-013
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-014
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim edge-cases-001
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim edge-cases-002
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim edge-cases-003
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim edge-cases-004
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim edge-cases-005
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim edge-cases-006
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim edge-cases-007
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-001
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-002
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-003
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-004
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-005
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-006
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-007
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-008
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-009
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-010
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-011
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-012
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-013
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-014
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-015
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-016
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-017
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-018
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-019
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-020
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-021
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-022
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-023
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-024
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-025
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-026
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-027
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-028
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-029
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-030
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-031
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-032
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-033
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-034
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-035
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-036
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-037
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-038
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-039
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-040
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-041
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-042
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-043
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-044
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-045
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-046
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-summary-001
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim san-antonio-svi-gradient-001
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim san-antonio-svi-gradient-002
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim san-antonio-svi-gradient-003
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-001
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-002
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-003
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-004
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-005
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-006
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-007
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-008
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-009
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-010
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-011
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim screens-that-did-not-produce-a-finding-012
```

Reproduce commands (executed with the authorised workspace and recorded in the execution log; product hashes and regenerated-product differences are checked on every audit run):

```sh
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_edge_cases.py --workspace "$COMPETITION_WORKSPACE" --out reports/edge-cases.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_place_duplicates.py --workspace "$COMPETITION_WORKSPACE" --out reports/place-dedup.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_poi_categories.py --workspace "$COMPETITION_WORKSPACE" --out reports/poi-category-audit.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_sample_contract.py --workspace "$COMPETITION_WORKSPACE" --out reports/sample-contract-audit.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_transport_route_variants.py --workspace "$COMPETITION_WORKSPACE"
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/build_estimator_v1_candidates.py --workspace "$COMPETITION_WORKSPACE" --out-dir reports/estimator-v1 --out-report reports/estimator-v1-audit.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/build_estimator_v2_candidates.py --workspace "$COMPETITION_WORKSPACE"
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/build_poi_confidence_candidate.py --workspace "$COMPETITION_WORKSPACE"
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/build_road_topology_candidate.py --workspace "$COMPETITION_WORKSPACE"
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/discovery_confidence_by_stratum.py --workspace "$COMPETITION_WORKSPACE" --out reports/confidence-by-stratum.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/discovery_rural_svi_minority.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-rural-svi-minority.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/estimate_gaps.py --workspace "$COMPETITION_WORKSPACE" --poi-model facility-miss
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/estimate_gaps.py --workspace "$COMPETITION_WORKSPACE" --route-audit-only --route-audit-out reports/transport-route-ref-audit.json
# exit None
/home/martin/hddProjects/langywrap/uv run --quiet --with-requirements requirements.txt --with pytest python -m pytest -q
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with-requirements requirements.txt python scripts/audit_san_antonio_svi_gradient.py --workspace "$COMPETITION_WORKSPACE" --out reports/san-antonio-svi-gradient.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with-requirements requirements.txt python scripts/discovery/border_metros.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-border-metros.json && /home/martin/hddProjects/langywrap/uv run --quiet --with-requirements requirements.txt python scripts/discovery/transfer_outside_regions.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-border-transfer.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with-requirements requirements.txt python scripts/discovery/border_metros.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-border-metros.json && /home/martin/hddProjects/langywrap/uv run --quiet --with-requirements requirements.txt python scripts/discovery/transfer_outside_regions.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-border-transfer.json && /home/martin/hddProjects/langywrap/uv run --python 3.12 --no-project --isolated --with-requirements requirements.txt --with-requirements requirements-cdc-analysis.txt python scripts/audit_who_is_affected_cdc_places.py --workspace "$COMPETITION_WORKSPACE" --out reports/who-is-affected-cdc-places.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with-requirements requirements.txt python scripts/discovery/transfer_outside_regions.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-border-transfer.json
# exit 0
MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with matplotlib==3.10.8 python scripts/discovery_postfire_stale_reference.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire.json --map reports/discovery-postfire-map.png && /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 python scripts/audit_discovery_postfire_county_bootstrap.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire-county-bootstrap.json && MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with shapely==2.2.0 --with matplotlib==3.10.8 python scripts/discovery_dins_mechanism.py --workspace "$COMPETITION_WORKSPACE" --dins-file "$COMPETITION_WORKSPACE/external-discovery-2026-10-29/camp-fire-dins.geojson" --out reports/discovery-dins-mechanism.json --markdown reports/discovery-dins-mechanism.md --scatter reports/discovery-dins-mechanism.png
# exit 1
MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with matplotlib==3.10.8 python scripts/discovery_postfire_stale_reference.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire.json --map reports/discovery-postfire-map.png && /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 python scripts/audit_discovery_postfire_county_bootstrap.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire-county-bootstrap.json && MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with shapely==2.2.0 --with matplotlib==3.10.8 python scripts/discovery_dins_mechanism.py --workspace "$COMPETITION_WORKSPACE" --dins-file "$COMPETITION_WORKSPACE/external-discovery-2026-10-29/camp-fire-dins.geojson" --out reports/discovery-dins-mechanism.json --markdown reports/discovery-dins-mechanism.md --scatter reports/discovery-dins-mechanism.png && /home/martin/hddProjects/langywrap/uv run --quiet --with-requirements requirements.txt python scripts/audit_postfire_lineage.py --workspace "$COMPETITION_WORKSPACE" --out reports/postfire-lineage-recheck.json
# exit 0
MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with matplotlib==3.10.8 python scripts/discovery_source_provenance.py --workspace "$COMPETITION_WORKSPACE" --out reports/source-provenance.json --figure reports/source-provenance.png
```

## Unresolved claims

None for claim evidence: every quantitative claim in the registered writeup documents carries a verified evidence pointer and a passing verify command. Overall audit acceptance remains separate; see the verdict for any reproduce-product differences.

## Limits of this screen

This is a documentation screen. Presence of a number in the cited artifact proves the statement was not invented and is reproducible from committed permitted data; it does not make an unvalidated proxy accurate, does not endorse any interpretation, and does not assess the hidden organiser target. Statements about real-world consequences remain hypothetical, and no claim here is an accuracy measurement. No candidate was changed and nothing was uploaded.
