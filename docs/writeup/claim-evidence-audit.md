# Claim-to-evidence audit for the Bias Bounty writeup

Row: `bias-writeup-claim-evidence-audit`. Documentation screen: every quantitative statement in the best bias discovery / best documentation writeup must carry an exact evidence pointer and a reproducible command; not a model-promotion or accuracy assessment.

## Verdict

- **72 of 72 quantitative claims fully traceable** (100.0%).
- **862 of 862 numeric tokens (100.0%) are present in the evidence named by the claim that states them.**
- 72 per-claim verify commands and 19 reproduce commands; 19/19 recorded reproduce-command exits are 0; 0 command(s) lack an execution record in this no-`--execute-reproduce` audit pass; all recorded reproduce-command product hashes match the current files. 3 executed reproduce command(s) generated product differences not covered by the registered disclosure rules; see the audit JSON's tier_b_reproduce entries.
- Accuracy language outside the allowed classes: none. Prohibited reference/label/leaderboard sources used as scored-model evidence: none.
- All acceptance gates: **FAIL**.

## Method

Each registered writeup source document is split into quantitative claim blocks (a block is a paragraph or a table containing at least one number outside a fenced command). Every number in a block is a token; a token is *covered* when the same value appears in an evidence artifact named by that claim, allowing for the decimal places actually written and for fraction/percentage presentation. Identifier-like digit strings (bare GEOIDs) are not claims. Every claim names a primary artifact plus an exact pointer into it, is checked against the live file hash, carries a per-claim verify command that must exit 0, and carries the command that regenerates its artifact.

## Claims by document

| Document | Claims | Fully traceable | Numeric tokens | Covered |
|---|---:|---:|---:|---:|
| `docs/writeup/bias-discovery.md` | 9 | 9 | 93 | 93 |
| `docs/writeup/methodology-summary.md` | 2 | 2 | 6 | 6 |
| `docs/writeup/methodology.md` | 42 | 42 | 528 | 528 |
| `docs/writeup/edge-cases.md` | 7 | 7 | 131 | 131 |
| `docs/writeup/appendices/screens-that-did-not-produce-a-finding.md` | 12 | 12 | 104 | 104 |

## Claim inventory

Each row is one quantitative claim: where it is stated, the artifact and pointer that substantiate it, and how to reproduce that artifact. `Class` is the provenance of the statement, not a claim of accuracy.

| Claim | Document | Section | Lines | Class | Primary evidence | Pointer | Tokens | Verify | Reproduce |
|---|---|---|---|---|---|---|---:|---|---|
| `bias-discovery-001` Across Maricopa, Northern California, Eastern Oklahoma, and South-Cent… | `bias-discovery.md` | Evidence | 9-9 | `measured_provided_data` | `reports/source-provenance.json` | `/` | 2/2 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-002` Paradise and Magalia are Butte County communities affected by the 2018… | `bias-discovery.md` | Impact and novelty | 23-23 | `measured_provided_data` | `reports/discovery-postfire.json` | `/interpretation` | 5/5 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-003` All six GEOIDs have a provided MTBS last-wildfire year of 2018. The ta… | `bias-discovery.md` | Evidence | 29-29 | `measured_provided_data` | `reports/discovery-dins-mechanism.json` | `/spearman/cohort` | 2/2 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-004` \| GEOID \| Overture / USFS \| USFS − Overture \| DINS destroyed \| DINS ma… | `bias-discovery.md` | Evidence | 31-38 | `measured_provided_data` | `reports/discovery-dins-mechanism.json` | `/focal_butte_tracts` | 30/30 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-005` Burn classes follow the report's 0–100% bins; tracts reported above 10… | `bias-discovery.md` | Evidence | 42-42 | `measured_provided_data` | `reports/discovery-postfire.json` | `/` | 3/3 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-006` \| Provided NorCal burn class \| Tracts \| Overture / USFS < 0.8 \| Share … | `bias-discovery.md` | Evidence | 44-50 | `measured_provided_data` | `reports/discovery-postfire.json` | `/` | 26/26 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-007` The tract-bootstrap difference is +9.55 percentage points (95% CI [+0.… | `bias-discovery.md` | Evidence | 56-56 | `measured_provided_data` | `reports/discovery-postfire.json` | `/high_burn_vs_unburned_below_0_8/replicates` | 4/4 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-008` **DINS mechanism reproduction check — negative.** The DINS reproductio… | `bias-discovery.md` | Evidence | 58-58 | `measured_provided_data` | `reports/discovery-dins-mechanism.json` | `/spearman/statistic` | 13/13 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-009` In the official sample, 805 of 858 tracts marked `tribal_any` (93.82%,… | `bias-discovery.md` | Evidence | 72-72 | `measured_provided_data` | `reports/discovery-strata-confound.json` | `/stratum_composition/non_tribal/by_region/south-central-tx/tracts` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-summary-001` The organisers provide no labels. Four official samples define 9,379 s… | `methodology-summary.md` | Scope and safeguards | 3-3 | `scope_statement` | `reports/final-two.json` | `/scope/official_scored_tracts` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-summary-002` Public-split RMSEs: all-zero C0 (`uRRw9KpB`) `0.1072`; corrected-order… | `methodology-summary.md` | Public-score transparency (descriptive | 17-17 | `historical_disclosure` | `reports/final-two.json` | `/corrected_cgs_selection/scores` | 4/4 | exit 0 | `python scripts/verify_writeup_claim.py --claim methodology-s…` |
| `methodology-001` **Coverage: 100% of the documentation items listed below.** Completion… | `methodology.md` | Rubric checklist and reading guide | 7-7 | `scope_statement` | `reports/candidates.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-002` The official scoring domain is the union of four published per-region … | `methodology.md` | Data sources, scope, and provenance | 21-21 | `input_inventory` | `docs/competition.md` | `document` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-003` \| Region \| Official sample \| ACS geometry parquet bytes / rows \| Overt… | `methodology.md` | Data sources, scope, and provenance | 34-39 | `input_inventory` | `docs/data-inventory.md` | `document` | 44/44 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-004` \| Region \| Official sample CSV bytes; SHA-256 \| Local strata parquet S… | `methodology.md` | Data sources, scope, and provenance | 43-48 | `input_inventory` | `reports/v2-input-provenance.json` | `/inputs/provided_local_copies/samples/maricopa-az-sample-submission.csv/bytes` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-005` Audit reports additionally record row-group/projection information and… | `methodology.md` | Data sources, scope, and provenance | 50-50 | `input_inventory` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-006` The deterministic C2/v0 screen uses only provided Overture feature sum… | `methodology.md` | Organiser scoring context and our calc | 56-56 | `model_specification` | `scripts/estimate_gaps.py` | `document` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-007` \| Component \| C2/v0 expected reference proxy \| Definedness and compone… | `methodology.md` | Organiser scoring context and our calc | 58-65 | `model_specification` | `scripts/estimate_gaps.py` | `def:sha256` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-008` For missing urbanity, use finite `pct_urban`; otherwise use `pop_urban… | `methodology.md` | Organiser scoring context and our calc | 67-67 | `model_specification` | `scripts/estimate_gaps.py` | `def:sha256` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-009` Historical alternatives C1 (region constants), C3 (v0 denominators ×1.… | `methodology.md` | Alternative components and aggregation | 73-73 | `historical_disclosure` | `reports/candidates.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-010` - **Buildings:** the submitted building term is a fixed gap of `0.01` … | `methodology.md` | Provided-feature model components | 85-86 | `measured_model_only` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-011` The full edge-case table and calculation policy are in [`edge-cases.md… | `methodology.md` | Edge cases, bias discovery, and interp | 93-93 | `measured_provided_data` | `reports/edge-cases.json` | `/geometry_method` | 11/11 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-012` The opt-in route audit is `scripts/estimate_gaps.py --route-audit-only… | `methodology.md` | Route-reference transport v2 (provided | 119-119 | `measured_provided_data` | `scripts/estimate_gaps.py` | `document` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-013` \| Region \| `sum(L_named)/sum(L_route)` \| `L_named` / `L_route` km \| `L… | `methodology.md` | Route-reference transport v2 (provided | 123-128 | `measured_provided_data` | `reports/transport-route-ref-audit.json` | `/regions/maricopa-az/sum_L_named_over_sum_L_route` | 28/28 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-014` All four route-v2 transport + v0 building/POI any-undefined shares are… | `methodology.md` | Route-reference transport v2 (provided | 130-130 | `measured_provided_data` | `reports/transport-route-ref-audit.json` | `/regions/maricopa-az/sum_L_named_over_sum_L_route` | 10/10 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-015` The measure-before route-reference component is recomputed as `clip(1 … | `methodology.md` | Connector-graph road topology sensitiv | 136-136 | `measured_provided_data` | `reports/road-topology-candidate.json` | `/candidate_rows` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-016` The frozen adjustment is `0.05 × (1 − largest_component_node_share)` a… | `methodology.md` | Connector-graph road topology sensitiv | 138-138 | `measured_provided_data` | `reports/road-topology-candidate.json` | `/measure_after/diagnostics_and_candidate_by_region_and_urban_rural/all/topology_defined_rows` | 14/14 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-017` The edge audit processed 3,143,887 supplied road-segment rows and 8,49… | `methodology.md` | Connector-graph road topology sensitiv | 140-140 | `measured_provided_data` | `reports/road-topology-candidate.json` | `/measure_after/diagnostics_and_candidate_by_region_and_urban_rural/south-central-tx:Urban/topology_graph/dead_end_nodes_per_tract/variance_population` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-018` `reports/transport-route-variants.json` compares the three cumulative … | `methodology.md` | Route denominator sensitivity and sele | 154-154 | `measured_provided_data` | `reports/transport-route-variants.json` | `/` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-019` \| Variant \| Regions inside disclosed 0.71–1.59 ratio band (of 4) \| Reg… | `methodology.md` | Route denominator sensitivity and sele | 156-160 | `measured_provided_data` | `reports/transport-route-variants.json` | `/aggregate_variant_metrics` | 14/14 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-020` All three variants put all four hybrid any-undefined shares within 0.1… | `methodology.md` | Route denominator sensitivity and sele | 162-162 | `measured_provided_data` | `reports/transport-route-variants.json` | `/regions/south-central-tx/variants/state_us_interstate/sum_L_named_over_sum_L_route` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-021` The audit read only official `GEOID` cells from the per-region sample … | `methodology.md` | POI numerator audit and selected categ | 195-206 | `measured_provided_data` | `docs/data-inventory.md` | `document` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-022` \| Region \| Official tracts \| Selected fire / EMS / school POI rows \| `… | `methodology.md` | POI numerator audit and selected categ | 227-232 | `measured_provided_data` | `reports/poi-category-audit.json` | `/regions/maricopa-az/tract_index/rows` | 56/56 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-023` `Sources.dataset` is a multi-valued property: dataset row counts are n… | `methodology.md` | POI numerator audit and selected categ | 234-241 | `measured_provided_data` | `reports/poi-category-audit.json` | `/regions/south-central-tx/category_frequency` | 16/16 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-024` \| Region \| Core zero tracts fire / EMS / school \| All fire/rescue cate… | `methodology.md` | POI numerator audit and selected categ | 243-248 | `measured_provided_data` | `reports/poi-category-audit.json` | `/regions/maricopa-az/tract_zero_sensitivity/core_v0/fire/zero_tracts` | 100/100 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-025` `scripts/audit_place_duplicates.py` compares raw selected facility row… | `methodology.md` | 50 m same-name facility dedup sensitiv | 264-264 | `measured_provided_data` | `reports/place-dedup.json` | `/policy/distance` | 5/5 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-026` \| Region \| Facility rows, raw → deduplicated \| Facility rows removed \|… | `methodology.md` | 50 m same-name facility dedup sensitiv | 266-272 | `measured_provided_data` | `reports/place-dedup.json` | `/regions/maricopa-az/facility_rows/raw_unique_rows` | 50/50 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-027` All 36 removed rows are school duplicates; no fire or EMS row is remov… | `methodology.md` | 50 m same-name facility dedup sensitiv | 274-274 | `measured_provided_data` | `reports/place-dedup.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-028` Here `n_overture_t` is the selected exact-category count plus the prov… | `methodology.md` | Facility-miss POI v2 (fixed prior; mod | 310-310 | `measured_model_only` | `reports/poi-facility-miss-audit.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-029` \| Type \| Intercept \| Density slope \| Urbanity slope \| \|---\|---:\|---:\|-… | `methodology.md` | Facility-miss POI v2 (fixed prior; mod | 314-318 | `measured_model_only` | `reports/poi-facility-miss-audit.json` | `/` | 9/9 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-030` With the prior frozen, the audit reports the observed Overture zero co… | `methodology.md` | Facility-miss POI v2 (fixed prior; mod | 320-320 | `measured_model_only` | `reports/poi-facility-miss-audit.json` | `/summaries/maricopa-az/poi_v2_mean_defined` | 16/16 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-031` Before weighting, raw core counts are 5,242 fire, 2,739 EMS, and 39,43… | `methodology.md` | POI confidence-weighted count sensitiv | 337-337 | `measured_model_only` | `reports/poi-confidence-candidate.json` | `/measure_before/source_and_confidence_coverage/fire/selected_rows` | 11/11 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-032` \| Confidence threshold \| Mean POI candidate \| Variance \| Std. dev. \| M… | `methodology.md` | POI confidence-weighted count sensitiv | 339-343 | `measured_model_only` | `reports/poi-confidence-candidate.json` | `/measure_after/threshold_candidates/POI_CONFIDENCE_T050/all/score/mean` | 27/27 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-033` The deterministic diagnostic in `scripts/discovery_confidence_by_strat… | `methodology.md` | Overture POI confidence by SVI and urb | 359-369 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-034` Quartiles are region-specific over official scored tracts with finite … | `methodology.md` | Overture POI confidence by SVI and urb | 371-378 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/definitions/intervals` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-035` \| Region \| All-place confidence q10 / q25 / median \| All places `<0.5`… | `methodology.md` | Overture POI confidence by SVI and urb | 380-385 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/regions/maricopa-az/overall_confidence_quantiles_q10_q25_q50_q75_q90/q10` | 40/40 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-036` The observed pattern is opposite the initial hypothesis that higher-SV… | `methodology.md` | Overture POI confidence by SVI and urb | 387-396 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-037` 1. Restrict to the authoritative South-Central TX sample. | `methodology.md` | Discovery screen | 417-417 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-038` 2. Define the rural screen as provided `pct_urban < 0.5`. | `methodology.md` | Discovery screen | 418-418 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-039` 3. Among rural tracts with nonmissing `svi_minority`, calculate the em… | `methodology.md` | Discovery screen | 419-419 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/group_definition/upper_quartile_cutoff_inclusive` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-040` 4. Compare mean C2 predicted `coverage_gap_score` across those groups.… | `methodology.md` | Discovery screen | 420-420 | `measured_provided_proxy` | `reports/candidates.json` | `/` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-041` 5. Compute 95% percentile intervals from 50,000 bootstrap resamples of… | `methodology.md` | Discovery screen | 421-421 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/bootstrap_replicates` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-042` The screen found 248 high-field rural tracts and 739 other rural tract… | `methodology.md` | Discovery screen | 423-423 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/high_group_mean` | 5/5 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
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

## Evidence artifacts and hashes

| Artifact | SHA-256 | Claims |
|---|---|---:|
| `config/poi-category-definitions.json` | `8309a0bb08613b92621f4977eb78998e469951c0cd0ed012d93ea2f5cf59654f` | 3 |
| `config/poi-confidence-weighting-preregistered.json` | `f93ffa0153c6e2207dc99ae1a0d44753371a2ce173f71eb64feb5d17e0e46296` | 1 |
| `config/reproduction-inputs.json` | `0172220d2a463be4cd9ca014439363bb490994a59709b576bc6474d7546454bd` | 3 |
| `docs/competition.md` | `381e8389194f278adc808ec023a688d727cab94bb539335049191261d2fa1d28` | 1 |
| `docs/data-inventory.md` | `88fe309bd2a577d544081fd95ddc7980e77a4e4751d175eab7d5752e7f4ce950` | 27 |
| `docs/final-two-protocol.md` | `8b9d68bf798ec86aedda29ca7309d94503e02918142740f66673377db2f0268e` | 1 |
| `reports/building-v2-audit.json` | `4815735bbefcd155a8bd566fc92403e378d436aa97dc062344f36fcc9f7936d9` | 1 |
| `reports/candidates.json` | `799d0857fd25b12ead43e091d53799af0a325dfe510553410dd3cd580f5fa815` | 4 |
| `reports/candidates/road_topology_candidate_manifest.json` | `b269e4487905937d48418822aeb11a92e76f0371236d99e175e920d82852705f` | 3 |
| `reports/confidence-by-stratum.json` | `ac759312e2b3e1384078c2d46dc386a9c2a2905ae5c6366d1247c13ee6916053` | 4 |
| `reports/discovery-dins-mechanism.json` | `8af173a124f59ef7fc6a114907eb65340cc9f87bde7093db0eb7441fc7d3ccfd` | 8 |
| `reports/discovery-external-layers.json` | `6d5c68655af215fcea7f1eb01e05d5065e48ec2f2c8a7786317d8d2ad9a24998` | 12 |
| `reports/discovery-postfire-county-bootstrap.json` | `f55774557f0461875cc5c4a56063ef7cf9f7056f11828ea368c85e6eb5b96348` | 5 |
| `reports/discovery-postfire.json` | `cbf100bc833bf09c02bad9f3c11a53d0da0736a7a249018c1ecc8dce55f3d49a` | 7 |
| `reports/discovery-rural-svi-minority.json` | `da1a581ddb25347cc6a0ffd2bb06cb8a1811082d08973bbfefc04e0c5c08cbe0` | 19 |
| `reports/discovery-strata-confound.json` | `ce0f1307793d1c658754cb7f9dd6fee7ea9cabf2a8a0e7ccb8e9888c2bad9ecb` | 2 |
| `reports/edge-cases.json` | `53626e3ad297c4609e541a7570146b732677275d2d371f17eac6606361911237` | 8 |
| `reports/estimator-v1-audit.json` | `1d72712e63f9b8f9682cd82a3cebc888be8bf0301cfc82cda05a2012a417ef5a` | 3 |
| `reports/estimator-v2-audit.json` | `131376c0474244b5c1c1cad27d0dd1ce1f5513a01c599a6bc53f79c9ae8bc4d0` | 1 |
| `reports/final-two.json` | `c31358286078bd1d0b7ff87deb791a63cd0df51b45b53da13de27c678ea22361` | 2 |
| `reports/gap-estimator-audit.json` | `a872e2dd62a5333037bc8e663278134c5b0a08493c7ec41768962e33859187f9` | 2 |
| `reports/lb-scorecard-snapshot.json` | `82e9515d5d4462fb057f3c2780dc1895f8c625caab4dc4c86ea6f7a9a5906d56` | 1 |
| `reports/place-dedup.json` | `de03f53c37fbb9a909b2f45172ede1a58bc059371e22837738df56a54bcd3cba` | 3 |
| `reports/poi-category-audit.json` | `cd48fa40d0fb1b3cdd1e431734acb646791916df02ee021a60243020b17d9de8` | 6 |
| `reports/poi-confidence-candidate.json` | `23c49c00105c5936baa094a3ce0f5a23e41589939c6fea972870f1fde794370e` | 2 |
| `reports/poi-facility-miss-audit.json` | `b559c8d3d85709d0e5f2d2102bb39da4d8255a3c62f728cf736da522025a289c` | 4 |
| `reports/road-topology-candidate.json` | `4a8b630e865609ae23e8825c864c771ed319d63e5c27eb86158e1dac6dcfc6ce` | 3 |
| `reports/sample-contract-audit.json` | `e9a56f30ea7c5a09bf94ebc95d2bf6b1e7559456ee8cf1c16304d9137175dbd0` | 5 |
| `reports/source-provenance.json` | `e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565` | 2 |
| `reports/submissions.jsonl` | `948eceb0b79a2d73bb4cc34df85b5119e9e0a9c707faf02a7d2d6dbaeb93808e` | 1 |
| `reports/t1-stroke-residual-screen.json` | `d717571b5a5dcc6e7bddf84398082ca44118b4b39b22c905d92cd7bee250e3b9` | 12 |
| `reports/transport-route-ref-audit.json` | `bf904336963f553cc7442c5996a4d8d504fe30aad5c4f1a2e44da8fa773b1927` | 7 |
| `reports/transport-route-variants.json` | `2a1acc760aadd2fce3a1df2afae6bfd9aaaef2a2ce510df8b674bccb9eb11aa2` | 3 |
| `reports/v2-defined-mask-audit.json` | `fea33ef7daeeeac2fd187803481e050085698afd6f44ed182e9c5af5028054d2` | 8 |
| `reports/v2-input-provenance.json` | `b52c3b1bcfee452e8d04c2af28a8c9abd1a0fb609b50c1ed9a9d56b9ec7f5d6a` | 6 |
| `scripts/audit_place_duplicates.py` | `03717aa54a75a6e55bbe6b85f65dd66f4cd1cae88632b12c5006087247da1e90` | 1 |
| `scripts/discovery_confidence_by_stratum.py` | `35a30e0a87ad016d3e936be4437084cffc91f708ef5c6692886c38805026d9a1` | 1 |
| `scripts/estimate_gaps.py` | `4f59982e0349af048ba11efbd5dbc629076eef8d3c8dc08d5747da5833bfc4fe` | 4 |

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
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-summary-001
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-summary-002
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
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest python -m pytest -q
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
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/discovery_strata_confound.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-strata-confound.json
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/estimate_gaps.py --workspace "$COMPETITION_WORKSPACE" --poi-model facility-miss
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/estimate_gaps.py --workspace "$COMPETITION_WORKSPACE" --route-audit-only --route-audit-out reports/transport-route-ref-audit.json
# exit 0
MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with matplotlib==3.10.8 python scripts/discovery_postfire_stale_reference.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire.json --map reports/discovery-postfire-map.png
# exit 0
MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with matplotlib==3.10.8 python scripts/discovery_postfire_stale_reference.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire.json --map reports/discovery-postfire-map.png && /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 python scripts/audit_discovery_postfire_county_bootstrap.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire-county-bootstrap.json && MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with shapely==2.2.0 --with matplotlib==3.10.8 python scripts/discovery_dins_mechanism.py --workspace "$COMPETITION_WORKSPACE" --dins-file "$COMPETITION_WORKSPACE/external-discovery-2026-10-29/camp-fire-dins.geojson" --out reports/discovery-dins-mechanism.json --markdown reports/discovery-dins-mechanism.md --scatter reports/discovery-dins-mechanism.png
# exit 0
MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with matplotlib==3.10.8 python scripts/discovery_source_provenance.py --workspace "$COMPETITION_WORKSPACE" --out reports/source-provenance.json --figure reports/source-provenance.png
# exit 0
python scripts/verify_writeup_claim.py --claim methodology-summary-002
```

## Unresolved claims

None for claim evidence: every quantitative claim in the registered writeup documents carries a verified evidence pointer and a passing verify command. Overall audit acceptance remains separate; see the verdict for any reproduce-product differences.

## Limits of this screen

This is a documentation screen. Presence of a number in the cited artifact proves the statement was not invented and is reproducible from committed permitted data; it does not make an unvalidated proxy accurate, does not endorse any interpretation, and does not assess the hidden organiser target. Statements about real-world consequences remain hypothetical, and no claim here is an accuracy measurement. No candidate was changed and nothing was uploaded.
