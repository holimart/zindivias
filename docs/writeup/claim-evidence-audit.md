# Claim-to-evidence audit for the Bias Bounty writeup

Row: `bias-writeup-claim-evidence-audit`. Documentation screen: every quantitative statement in the best bias discovery / best documentation writeup must carry an exact evidence pointer and a reproducible command; not a model-promotion or accuracy assessment.

## Verdict

- **88 of 88 quantitative claims fully traceable** (100.0%).
- **1035 of 1035 numeric tokens (100.0%) are present in the evidence named by the claim that states them.**
- 88 per-claim verify commands and 17 reproduce commands; every listed command's recorded exit code is 0 and every reproduce product hash-verifies.
- Accuracy language outside the allowed classes: none. Prohibited reference/label/leaderboard sources used as scored-model evidence: none.
- All acceptance gates: **PASS**.

## Method

Each of the three writeup documents is split into quantitative claim blocks (a block is a paragraph or a table containing at least one number outside a fenced command). Every number in a block is a token; a token is *covered* when the same value appears in an evidence artifact named by that claim, allowing for the decimal places actually written and for fraction/percentage presentation. Identifier-like digit strings (bare GEOIDs) are not claims. Every claim names a primary artifact plus an exact pointer into it, is checked against the live file hash, carries a per-claim verify command that must exit 0, and carries the command that regenerates its artifact.

## Claims by document

| Document | Claims | Fully traceable | Numeric tokens | Covered |
|---|---:|---:|---:|---:|
| `docs/writeup/bias-discovery.md` | 29 | 29 | 262 | 262 |
| `docs/writeup/methodology.md` | 52 | 52 | 642 | 642 |
| `docs/writeup/edge-cases.md` | 7 | 7 | 131 | 131 |

## Claim inventory

Each row is one quantitative claim: where it is stated, the artifact and pointer that substantiate it, and how to reproduce that artifact. `Class` is the provenance of the statement, not a claim of accuracy.

| Claim | Document | Section | Lines | Class | Primary evidence | Pointer | Tokens | Verify | Reproduce |
|---|---|---|---|---|---|---|---:|---|---|
| `bias-discovery-001` In a set of six Butte County tracts in the Paradise/Magalia Camp Fire … | `bias-discovery.md` | Impact | 7-7 | `measured_provided_data` | `reports/discovery-postfire.json` | `/focal_butte_2018_burn_geoids` | 4/4 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-002` The comparison is exactly `Overture overture_buildings / provided stra… | `bias-discovery.md` | Evidence | 17-17 | `measured_provided_data` | `reports/discovery-postfire.json` | `/` | 1/1 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-003` \| Butte County GEOID \| Overture / provided USFS count \| Provided MTBS … | `bias-discovery.md` | Evidence | 19-26 | `measured_provided_data` | `reports/discovery-postfire.json` | `/focal_butte_2018_burn_geoids` | 24/24 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-004` Two additional supplied examples are `06089010603` (ratio 0.691; MTBS … | `bias-discovery.md` | Evidence | 28-28 | `measured_provided_data` | `reports/discovery-postfire.json` | `/additional_named_examples` | 8/8 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-005` \| Provided NorCal burn class \| Tracts \| Ratio <0.8 \| Share below 0.8 \|… | `bias-discovery.md` | Evidence | 30-36 | `measured_provided_data` | `reports/discovery-postfire.json` | `/` | 26/26 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-006` Fourteen of 591 tracts have provided burned percentages above 100 and … | `bias-discovery.md` | Evidence | 38-38 | `measured_provided_data` | `reports/discovery-postfire.json` | `/high_burn_vs_unburned_below_0_8/replicates` | 6/6 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-007` That published interval resamples tracts independently. Tracts inside … | `bias-discovery.md` | Evidence | 40-40 | `measured_provided_data` | `reports/discovery-postfire-county-bootstrap.json` | `/summary/replicates` | 9/9 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-008` ![NorCal and Butte tract map: fill is provided MTBS burn percentage; p… | `bias-discovery.md` | Evidence | 42-42 | `measured_provided_data` | `reports/discovery-postfire.json` | `/` | 1/1 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-009` **Source/vintage caution:** the compared count is the provided strata … | `bias-discovery.md` | Evidence | 44-44 | `measured_provided_data` | `reports/discovery-postfire.json` | `/interpretation` | 2/2 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-010` The automated scorecard reports marginal tribal and rural comparisons … | `bias-discovery.md` | Novelty | 83-83 | `measured_provided_data` | `WORKSPACE:RULES.md` | `section:Official challenge page text (zindi-bias-bounty)` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-011` The script joins only official sample GEOIDs to the provided `tribal_a… | `bias-discovery.md` | Evidence | 87-87 | `measured_provided_data` | `reports/discovery-strata-confound.json` | `/total_tracts` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-012` \| Stratum \| Total \| Eastern OK \| Maricopa AZ \| Northern CA \| South-Cen… | `bias-discovery.md` | Evidence | 89-95 | `measured_provided_data` | `reports/discovery-strata-confound.json` | `/total_tracts` | 45/45 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-013` Within-region tribal prevalence is 67.53% in Eastern Oklahoma, 2.32% i… | `bias-discovery.md` | Evidence | 97-97 | `measured_provided_data` | `reports/discovery-strata-confound.json` | `/stratum_composition/tribal_any/by_region/northern-ca/share_of_stratum` | 12/12 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-014` The earlier backlog measurement listed rural counts of 412/174/147/1,0… | `bias-discovery.md` | Evidence | 99-99 | `measured_provided_data` | `reports/discovery-strata-confound.json` | `/rural_measure_before_reconciliation/counts_listed_in_backlog/south-central-tx` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-015` The rules disclose a 0.71–1.59 cross-region Overture/TIGER named-road … | `bias-discovery.md` | Evidence | 101-101 | `measured_provided_data` | `reports/transport-route-ref-audit.json` | `/regions/maricopa-az/sum_L_named_over_sum_L_route` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-016` The script asserts 9,379 unique official IDs and deterministic cohort … | `bias-discovery.md` | Reproducibility | 114-114 | `reproduction_statement` | `reports/discovery-strata-confound.json` | `/total_tracts` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-017` The analytic group is defined using only provided strata fields: `pct_… | `bias-discovery.md` | Proposed pattern outside the published | 124-124 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-018` \| Group \| Tracts \| Mean C2 predicted composite \| \|---\|---:\|---:\| \| Rur… | `bias-discovery.md` | Quantified proxy evidence | 128-132 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/high_group_mean` | 4/4 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-019` A 50,000-resample tract bootstrap gives a 95% percentile interval of *… | `bias-discovery.md` | Quantified proxy evidence | 134-134 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/bootstrap_replicates` | 5/5 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-020` \| Component \| Upper quartile (n; mean) \| Other rural (n; mean) \| Diffe… | `bias-discovery.md` | Quantified proxy evidence | 138-142 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/C2_component_results_defined_only/transport/high_group_defined_mean` | 19/19 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-021` \| South-Central TX GEOID \| `svi_minority` \| C2 composite \| C2 transpor… | `bias-discovery.md` | Quantified proxy evidence | 148-154 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/named_high_group_tracts_sorted_by_C2_estimate` | 25/25 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-022` The 50,000-draw bootstrap uses fixed seeds; a second run generated byt… | `bias-discovery.md` | Reproduction | 173-173 | `reproduction_statement` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/bootstrap_replicates` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `bias-discovery-023` The analysis covers all 9,379 authoritative sample GEOIDs in Maricopa,… | `bias-discovery.md` | Method and source definitions | 181-181 | `measured_provided_data` | `docs/data-inventory.md` | `document` | 1/1 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-024` Quartiles are calculated within each region. Reported means weight tra… | `bias-discovery.md` | Method and source definitions | 185-185 | `measured_provided_data` | `reports/source-provenance.json` | `/statistics/stratum_means` | 4/4 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-025` \| Region \| Building OSM share, Q4−Q1 (95% CI) \| Non-Meta place share, … | `bias-discovery.md` | Findings | 191-196 | `measured_provided_data` | `reports/source-provenance.json` | `/` | 24/24 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-026` The within-region Spearman correlations with `svi_overall` were negati… | `bias-discovery.md` | Findings | 198-198 | `measured_provided_data` | `reports/source-provenance.json` | `/regions/maricopa-az/spearman_correlations/building_osm_share/svi_overall/rho` | 8/8 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-027` Tribal-overlap contrasts were also negative in the three regions with … | `bias-discovery.md` | Findings | 200-200 | `measured_provided_data` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 3/3 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-028` The ten lowest-OSM-share tracts within each region's high-SVI quartile… | `bias-discovery.md` | Findings | 204-204 | `measured_provided_data` | `reports/source-provenance.json` | `/regions/eastern-ok/named_lowest_osm_high_svi` | 8/8 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `bias-discovery-029` ![Overture source-provider shares across region-specific SVI quartiles… | `bias-discovery.md` | Findings | 206-206 | `measured_provided_data` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 1/1 | exit 0 | `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/h…` |
| `methodology-001` **Coverage: 100% of the documentation items listed below.** Completion… | `methodology.md` | Rubric checklist and reading guide | 7-7 | `scope_statement` | `reports/candidates.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-002` The official scoring domain is the union of four published per-region … | `methodology.md` | Data sources, scope, and provenance | 21-21 | `input_inventory` | `docs/competition.md` | `document` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-003` \| Region \| Official sample \| ACS geometry parquet bytes / rows \| Overt… | `methodology.md` | Data sources, scope, and provenance | 34-39 | `input_inventory` | `docs/data-inventory.md` | `document` | 44/44 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-004` \| Region \| Official sample CSV bytes; SHA-256 \| Local strata parquet S… | `methodology.md` | Data sources, scope, and provenance | 43-48 | `input_inventory` | `reports/v2-input-provenance.json` | `/inputs/provided_local_copies/samples/maricopa-az-sample-submission.csv/bytes` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-005` Audit reports additionally record row-group/projection information and… | `methodology.md` | Data sources, scope, and provenance | 50-50 | `input_inventory` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-006` The deterministic C2/v0 screen uses only provided Overture feature sum… | `methodology.md` | Organiser scoring context and our calc | 56-56 | `model_specification` | `scripts/estimate_gaps.py` | `document` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-007` \| Component \| C2/v0 expected reference proxy \| Definedness and compone… | `methodology.md` | Organiser scoring context and our calc | 58-65 | `model_specification` | `scripts/estimate_gaps.py` | `def:sha256` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-008` For missing urbanity, use finite `pct_urban`; otherwise use `pop_urban… | `methodology.md` | Organiser scoring context and our calc | 67-67 | `model_specification` | `scripts/estimate_gaps.py` | `def:sha256` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-009` Historical alternatives C1 (region constants), C3 (v0 denominators ×1.… | `methodology.md` | Alternative components and aggregation | 73-73 | `historical_disclosure` | `reports/candidates.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-010` - **Buildings:** the experimental rule is a fixed building gap `0.01` … | `methodology.md` | Model-only v2 components and approval  | 85-85 | `measured_model_only` | `reports/building-v2-audit.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-011` - **Building morphology sensitivity:** `config/building-morphology-pre… | `methodology.md` | Model-only v2 components and approval  | 86-86 | `measured_model_only` | `reports/building-morphology-candidate.json` | `/global_sensitivity_by_beta/0.02/all_official_tracts/mean` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-012` - **Building-source sensitivity:** `config/building-source-prior-prere… | `methodology.md` | Model-only v2 components and approval  | 87-87 | `measured_model_only` | `reports/building-source-prior.json` | `/global_sensitivity_by_profile_and_beta/primary/0.02/all_official_tracts/mean` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-013` - **Cross-theme support sensitivity:** `config/cross-theme-support-pre… | `methodology.md` | Model-only v2 components and approval  | 88-88 | `measured_model_only` | `reports/cross-theme-support.json` | `/global/rows` | 10/10 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-014` - **Schema optional-attribute presence sensitivity:** `config/schema-p… | `methodology.md` | Model-only v2 components and approval  | 89-89 | `measured_model_only` | `reports/schema-presence-candidate.json` | `/measure_after/optional_field_drop_perturbation_tests/max_change_over_all_single_field_drops` | 11/11 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-015` - **Density-context sensitivity:** `config/density-context-preregister… | `methodology.md` | Model-only v2 components and approval  | 90-90 | `measured_model_only` | `reports/density-context-candidate.json` | `/method/scope/official_id_count` | 15/15 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-016` - **Facility spatial-catchment sensitivity:** `config/facility-catchme… | `methodology.md` | Model-only v2 components and approval  | 91-91 | `measured_model_only` | `reports/facility-catchment-candidate.json` | `/measure_before/facility_zero_shares_and_logistic_candidate_moments/global/observed_overture_zero_counts/fire0` | 25/25 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-017` - **Candidate stability envelope:** `config/candidate-stability-envelo… | `methodology.md` | Model-only v2 components and approval  | 92-92 | `measured_model_only` | `reports/candidate-stability-envelope.json` | `/measure_after/official_rows` | 10/10 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-018` The full edge-case table and calculation policy are in [`edge-cases.md… | `methodology.md` | Edge cases, bias discovery, and interp | 98-98 | `measured_provided_data` | `reports/edge-cases.json` | `/geometry_method` | 11/11 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-019` The opt-in route audit is `scripts/estimate_gaps.py --route-audit-only… | `methodology.md` | Route-reference transport v2 (provided | 124-124 | `measured_provided_data` | `scripts/estimate_gaps.py` | `document` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-020` \| Region \| `sum(L_named)/sum(L_route)` \| `L_named` / `L_route` km \| `L… | `methodology.md` | Route-reference transport v2 (provided | 128-133 | `measured_provided_data` | `reports/transport-route-ref-audit.json` | `/regions/maricopa-az/sum_L_named_over_sum_L_route` | 28/28 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-021` All four route-v2 transport + v0 building/POI any-undefined shares are… | `methodology.md` | Route-reference transport v2 (provided | 135-135 | `measured_provided_data` | `reports/transport-route-ref-audit.json` | `/regions/maricopa-az/sum_L_named_over_sum_L_route` | 10/10 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-022` The measure-before route-reference component is recomputed as `clip(1 … | `methodology.md` | Connector-graph road topology sensitiv | 141-141 | `measured_provided_data` | `reports/road-topology-candidate.json` | `/candidate_rows` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-023` The frozen adjustment is `0.05 × (1 − largest_component_node_share)` a… | `methodology.md` | Connector-graph road topology sensitiv | 143-143 | `measured_provided_data` | `reports/road-topology-candidate.json` | `/measure_after/diagnostics_and_candidate_by_region_and_urban_rural/all/topology_defined_rows` | 14/14 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-024` The edge audit processed 3,143,887 supplied road-segment rows and 8,49… | `methodology.md` | Connector-graph road topology sensitiv | 145-145 | `measured_provided_data` | `reports/road-topology-candidate.json` | `/measure_after/diagnostics_and_candidate_by_region_and_urban_rural/south-central-tx:Urban/topology_graph/dead_end_nodes_per_tract/variance_population` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-025` `reports/transport-route-variants.json` compares the three cumulative … | `methodology.md` | Route denominator sensitivity and sele | 159-159 | `measured_provided_data` | `reports/transport-route-variants.json` | `/` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-026` \| Variant \| Regions inside disclosed 0.71–1.59 ratio band (of 4) \| Reg… | `methodology.md` | Route denominator sensitivity and sele | 161-165 | `measured_provided_data` | `reports/transport-route-variants.json` | `/aggregate_variant_metrics` | 14/14 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-027` All three variants put all four hybrid any-undefined shares within 0.1… | `methodology.md` | Route denominator sensitivity and sele | 167-167 | `measured_provided_data` | `reports/transport-route-variants.json` | `/regions/south-central-tx/variants/state_us_interstate/sum_L_named_over_sum_L_route` | 7/7 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-028` The audit read only official `GEOID` cells from the per-region sample … | `methodology.md` | POI numerator audit and selected categ | 200-211 | `measured_provided_data` | `docs/data-inventory.md` | `document` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-029` \| Region \| Official tracts \| Selected fire / EMS / school POI rows \| `… | `methodology.md` | POI numerator audit and selected categ | 232-237 | `measured_provided_data` | `reports/poi-category-audit.json` | `/regions/maricopa-az/tract_index/rows` | 56/56 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-030` `Sources.dataset` is a multi-valued property: dataset row counts are n… | `methodology.md` | POI numerator audit and selected categ | 239-246 | `measured_provided_data` | `reports/poi-category-audit.json` | `/regions/south-central-tx/category_frequency` | 16/16 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-031` \| Region \| Core zero tracts fire / EMS / school \| All fire/rescue cate… | `methodology.md` | POI numerator audit and selected categ | 248-253 | `measured_provided_data` | `reports/poi-category-audit.json` | `/regions/maricopa-az/tract_zero_sensitivity/core_v0/fire/zero_tracts` | 100/100 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-032` `scripts/audit_place_duplicates.py` compares raw selected facility row… | `methodology.md` | 50 m same-name facility dedup sensitiv | 269-269 | `measured_provided_data` | `reports/place-dedup.json` | `/policy/distance` | 5/5 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-033` \| Region \| Facility rows, raw → deduplicated \| Facility rows removed \|… | `methodology.md` | 50 m same-name facility dedup sensitiv | 271-277 | `measured_provided_data` | `reports/place-dedup.json` | `/regions/maricopa-az/facility_rows/raw_unique_rows` | 50/50 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-034` All 36 removed rows are school duplicates; no fire or EMS row is remov… | `methodology.md` | 50 m same-name facility dedup sensitiv | 279-279 | `measured_provided_data` | `reports/place-dedup.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-035` Here `n_overture_t` is the selected exact-category count plus the prov… | `methodology.md` | Facility-miss POI v2 (fixed prior; mod | 315-315 | `measured_model_only` | `reports/poi-facility-miss-audit.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-036` \| Type \| Intercept \| Density slope \| Urbanity slope \| \|---\|---:\|---:\|-… | `methodology.md` | Facility-miss POI v2 (fixed prior; mod | 319-323 | `measured_model_only` | `reports/poi-facility-miss-audit.json` | `/` | 9/9 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-037` With the prior frozen, the audit reports the observed Overture zero co… | `methodology.md` | Facility-miss POI v2 (fixed prior; mod | 325-325 | `measured_model_only` | `reports/poi-facility-miss-audit.json` | `/summaries/maricopa-az/poi_v2_mean_defined` | 16/16 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-038` Before weighting, raw core counts are 5,242 fire, 2,739 EMS, and 39,43… | `methodology.md` | POI confidence-weighted count sensitiv | 342-342 | `measured_model_only` | `reports/poi-confidence-candidate.json` | `/measure_before/source_and_confidence_coverage/fire/selected_rows` | 11/11 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-039` \| Confidence threshold \| Mean POI candidate \| Variance \| Std. dev. \| M… | `methodology.md` | POI confidence-weighted count sensitiv | 344-348 | `measured_model_only` | `reports/poi-confidence-candidate.json` | `/measure_after/threshold_candidates/POI_CONFIDENCE_T050/all/score/mean` | 27/27 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-040` The deterministic diagnostic in `scripts/discovery_confidence_by_strat… | `methodology.md` | Overture POI confidence by SVI and urb | 364-374 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-041` Quartiles are region-specific over official scored tracts with finite … | `methodology.md` | Overture POI confidence by SVI and urb | 376-383 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/definitions/intervals` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-042` \| Region \| All-place confidence q10 / q25 / median \| All places `<0.5`… | `methodology.md` | Overture POI confidence by SVI and urb | 385-390 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/regions/maricopa-az/overall_confidence_quantiles_q10_q25_q50_q75_q90/q10` | 40/40 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-043` The observed pattern is opposite the initial hypothesis that higher-SV… | `methodology.md` | Overture POI confidence by SVI and urb | 392-401 | `measured_provided_data` | `reports/confidence-by-stratum.json` | `/` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-044` 1. Restrict to the authoritative South-Central TX sample. | `methodology.md` | Discovery screen | 422-422 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-045` 2. Define the rural screen as provided `pct_urban < 0.5`. | `methodology.md` | Discovery screen | 423-423 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-046` 3. Among rural tracts with nonmissing `svi_minority`, calculate the em… | `methodology.md` | Discovery screen | 424-424 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/group_definition/upper_quartile_cutoff_inclusive` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-047` 4. Compare mean C2 predicted `coverage_gap_score` across those groups.… | `methodology.md` | Discovery screen | 425-425 | `measured_provided_proxy` | `reports/candidates.json` | `/` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-048` 5. Compute 95% percentile intervals from 50,000 bootstrap resamples of… | `methodology.md` | Discovery screen | 426-426 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/bootstrap_replicates` | 3/3 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-049` The screen found 248 high-field rural tracts and 739 other rural tract… | `methodology.md` | Discovery screen | 428-428 | `measured_provided_proxy` | `reports/discovery-rural-svi-minority.json` | `/composite_results/c2/high_group_mean` | 5/5 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `methodology-050` In that snapshot 1,441 participants had produced 390 ranked teams. Quo… | `methodology.md` | Scorecard reflection | 456-461 | `historical_disclosure` | `reports/lb-scorecard-snapshot.json` | `/summary/participants` | 11/11 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet python scr…` |
| `methodology-051` The scorecard is more informative than the single number. The all-zero… | `methodology.md` | Scorecard reflection | 463-475 | `historical_disclosure` | `reports/scorecards/index.json` | `/scorecards` | 16/16 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet python scr…` |
| `methodology-052` Per the operator amendment of `2026-10-24` this paragraph is reflectio… | `methodology.md` | Scorecard reflection | 477-484 | `historical_disclosure` | `reports/scorecards/index.json` | `/preregistration/intent` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet python scr…` |
| `edge-cases-001` This table makes the handling explicit for low-information and structu… | `edge-cases.md` | Edge-case register for the coverage-ga | 3-3 | `scope_statement` | `docs/data-inventory.md` | `document` | 2/2 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-002` \| Region \| Official n \| Population 0 \| `ALAND` 0 \| Water proxy >50% \| … | `edge-cases.md` | Observed edge cases | 7-13 | `measured_provided_data` | `reports/v2-defined-mask-audit.json` | `/scope/region_rows/maricopa-az` | 55/55 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-003` Water is an approximation, not an independently observed water mask: f… | `edge-cases.md` | Observed edge cases | 15-15 | `measured_provided_data` | `reports/edge-cases.json` | `/geometry_method` | 8/8 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-004` The audit now applies the fixed provided-feature v2 component function… | `edge-cases.md` | Model-only v2 predictions for edge gro | 19-19 | `measured_model_only` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 4/4 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-005` \| Edge group \| Region \| n \| Facility-half v2 mean [q10–q90] \| Partial … | `edge-cases.md` | Model-only v2 predictions for edge gro | 23-33 | `measured_model_only` | `reports/edge-cases.json` | `/regions/maricopa-az/v2_edge_case_predictions/missing_pct_urban/predictions/facility_half_gap/mean` | 46/46 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-006` \| Case \| Method behavior \| Explicit limitation / safeguard \| \|---\|---\|… | `edge-cases.md` | Handling policy | 39-50 | `policy_statement` | `docs/data-inventory.md` | `document` | 15/15 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |
| `edge-cases-007` The audit reads only sample GEOID column 0 (not score-placeholder cell… | `edge-cases.md` | Reproduce the aggregate audit | 63-63 | `reproduction_statement` | `docs/data-inventory.md` | `section:Bias Bounty public GeoParquet inventory` | 1/1 | exit 0 | `/home/martin/hddProjects/langywrap/uv run --quiet --with num…` |

## Evidence artifacts and hashes

| Artifact | SHA-256 | Claims |
|---|---|---:|
| `WORKSPACE:RULES.md` | `MISSING` | 6 |
| `config/building-morphology-preregistered.json` | `85cc0b33d5a5eb0e2817d9be492a29236bc58bd6eb8b12c1e637e881330a4b5a` | 1 |
| `config/building-source-prior-preregistered.json` | `692cffb84873a29a1ebf62dec1808e17297fe7d58591129bebe57f4526067c4b` | 1 |
| `config/candidate-stability-envelope-preregistered.json` | `c66ea1cd8fd7de1fce13f532ec2d787dffd8f61623f538fcc296dd25e9faa435` | 1 |
| `config/cross-theme-support-preregistered.json` | `db5c5ce27509e214108c2b027a0c6b4cd06dd0336e3d38e664955e674bf9bb47` | 1 |
| `config/density-context-preregistered.json` | `497ce1280a8e13a5a575d80abc8078ba549851e3fba5f8f6385580ebe0ba803c` | 1 |
| `config/facility-catchment-preregistered.json` | `18cf5fc3468997728b3d4786c5892b636d17b7cd191eb02b2a047aa300bbc656` | 1 |
| `config/poi-category-definitions.json` | `8309a0bb08613b92621f4977eb78998e469951c0cd0ed012d93ea2f5cf59654f` | 3 |
| `config/poi-confidence-weighting-preregistered.json` | `f93ffa0153c6e2207dc99ae1a0d44753371a2ce173f71eb64feb5d17e0e46296` | 1 |
| `config/reproduction-inputs.json` | `0172220d2a463be4cd9ca014439363bb490994a59709b576bc6474d7546454bd` | 3 |
| `config/schema-presence-preregistered.json` | `4f715f96e48e4de60a47d69e270f53b92908813c89bf35aeee142e1887e2edc7` | 1 |
| `docs/competition.md` | `fc8141d3b9624f100b455dbded97ebf1754e96f87e91d25c4f151cb291097e65` | 1 |
| `docs/data-inventory.md` | `88fe309bd2a577d544081fd95ddc7980e77a4e4751d175eab7d5752e7f4ce950` | 20 |
| `docs/decisions/building-component.md` | `cc63abcf8a1128ee35e2a3a8b5cc4938cb333f3b30caea81a239374fc9855974` | 1 |
| `reports/building-morphology-candidate.json` | `e7f6f50d0b00fd2675f47878d524d78544f021f8bfa9bd2766722fff621cccdd` | 1 |
| `reports/building-source-prior.json` | `4066d3a86223dca5cdd2cf63e6660afbc6577915172a48f265c4f70da08f51d4` | 1 |
| `reports/building-v2-audit.json` | `4815735bbefcd155a8bd566fc92403e378d436aa97dc062344f36fcc9f7936d9` | 1 |
| `reports/candidate-stability-envelope.json` | `258cb9647e72ea3f8d225c18efed7de41bc9c1beb70218684fa332d7ea1d2622` | 1 |
| `reports/candidates.json` | `5374d6811fc1c6ea28ef680e3d29038c981d16265d4731df6dadd286a0bf34e1` | 4 |
| `reports/candidates/facility_catchment_candidate_manifest.json` | `180da02422e5b3045580dd642288c5fcb6aa18f01382c732b10946a20a99605e` | 1 |
| `reports/candidates/road_topology_candidate_manifest.json` | `b269e4487905937d48418822aeb11a92e76f0371236d99e175e920d82852705f` | 3 |
| `reports/candidates/schema_presence_candidate_manifest.json` | `f4450ee825de7a41b3c5c5df1f5aefb1808904aaf028837ad414296667d1c700` | 1 |
| `reports/confidence-by-stratum.json` | `ac759312e2b3e1384078c2d46dc386a9c2a2905ae5c6366d1247c13ee6916053` | 4 |
| `reports/cross-theme-support.json` | `77c010a8c74f62487816b142e489b651f38dfcc93793c87fbe9c91711d1d352d` | 1 |
| `reports/density-context-candidate.json` | `8da97e44bb3af4a06fb98f83065c8719fc746b5c1596cb11ef8bd611c72dd7b7` | 1 |
| `reports/discovery-postfire-county-bootstrap.json` | `18123e36b1d9565419c3c8e4b1adf91d6730c83b7c09099b8105fe6b1f756ccb` | 8 |
| `reports/discovery-postfire.json` | `cbf100bc833bf09c02bad9f3c11a53d0da0736a7a249018c1ecc8dce55f3d49a` | 16 |
| `reports/discovery-rural-svi-minority.json` | `da1a581ddb25347cc6a0ffd2bb06cb8a1811082d08973bbfefc04e0c5c08cbe0` | 19 |
| `reports/discovery-strata-confound.json` | `ce0f1307793d1c658754cb7f9dd6fee7ea9cabf2a8a0e7ccb8e9888c2bad9ecb` | 14 |
| `reports/edge-cases.json` | `53626e3ad297c4609e541a7570146b732677275d2d371f17eac6606361911237` | 8 |
| `reports/estimator-v1-audit.json` | `1d72712e63f9b8f9682cd82a3cebc888be8bf0301cfc82cda05a2012a417ef5a` | 3 |
| `reports/estimator-v2-audit.json` | `131376c0474244b5c1c1cad27d0dd1ce1f5513a01c599a6bc53f79c9ae8bc4d0` | 8 |
| `reports/facility-catchment-candidate.json` | `53cdbfa4450223d84db7647dc4fb16d058d6bd239740e9153f51000e884e6794` | 1 |
| `reports/gap-estimator-audit.json` | `a872e2dd62a5333037bc8e663278134c5b0a08493c7ec41768962e33859187f9` | 2 |
| `reports/lb-score-algebra.json` | `ae2066c228e13dcc2f0e174ed6ccb8491d5ac343286f914dd75d3213ebeeeeff` | 3 |
| `reports/lb-scorecard-snapshot.json` | `82e9515d5d4462fb057f3c2780dc1895f8c625caab4dc4c86ea6f7a9a5906d56` | 3 |
| `reports/leaderboard/2026-10-25T051219+0000.json` | `7f699892f4f38d8ce55deec8ae4ddbc03e33098bb4f053305ea67fd6d7b5686c` | 3 |
| `reports/leaderboard/index.json` | `1f35df77f881e0978a7d5653cc8e6b75b4026c614473cb88186a0619e61dd932` | 3 |
| `reports/place-dedup.json` | `de03f53c37fbb9a909b2f45172ede1a58bc059371e22837738df56a54bcd3cba` | 3 |
| `reports/poi-category-audit.json` | `cd48fa40d0fb1b3cdd1e431734acb646791916df02ee021a60243020b17d9de8` | 6 |
| `reports/poi-confidence-candidate.json` | `23c49c00105c5936baa094a3ce0f5a23e41589939c6fea972870f1fde794370e` | 2 |
| `reports/poi-facility-miss-audit.json` | `b559c8d3d85709d0e5f2d2102bb39da4d8255a3c62f728cf736da522025a289c` | 4 |
| `reports/road-topology-candidate.json` | `4a8b630e865609ae23e8825c864c771ed319d63e5c27eb86158e1dac6dcfc6ce` | 3 |
| `reports/sample-contract-audit.json` | `e9a56f30ea7c5a09bf94ebc95d2bf6b1e7559456ee8cf1c16304d9137175dbd0` | 4 |
| `reports/schema-presence-candidate.json` | `c4a4cfe3e290e15dd6cc1165c079f9494a2ac415c09c0c9334bd0184268fe457` | 1 |
| `reports/scorecards/BvyKrR6v.json` | `eedd62442b80c58528254b4731673343512c6bf29db5226241c0dadd5cce2c4f` | 3 |
| `reports/scorecards/TM83xxU6.json` | `efb267a63c7a312d04b59d53a667332a3ecb77dc229082f38d711b1aa01bbdaf` | 3 |
| `reports/scorecards/index.json` | `5355a2acf86675e1a07958e2e2ef3569109f911f91c69b850fb6142a6e53ba70` | 3 |
| `reports/scorecards/rTARuUaG.json` | `e977b4440c47f3b06afe45548145eabb1675852dd2d4d8b3052afb1759141550` | 3 |
| `reports/scorecards/uRRw9KpB.json` | `bfa4c458866ba17b588696fd9a4453cfae872f9935aebcaf8314ba3bf876ba7d` | 3 |
| `reports/scorecards/ySXi73rd.json` | `0927d4b704da35c06cb7dce6884210be90e91b9c510aa4408d2c3a8d87c19584` | 3 |
| `reports/source-provenance.json` | `e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565` | 7 |
| `reports/transport-route-ref-audit.json` | `bf904336963f553cc7442c5996a4d8d504fe30aad5c4f1a2e44da8fa773b1927` | 12 |
| `reports/transport-route-variants.json` | `2a1acc760aadd2fce3a1df2afae6bfd9aaaef2a2ce510df8b674bccb9eb11aa2` | 3 |
| `reports/v2-defined-mask-audit.json` | `fea33ef7daeeeac2fd187803481e050085698afd6f44ed182e9c5af5028054d2` | 8 |
| `reports/v2-input-provenance.json` | `b52c3b1bcfee452e8d04c2af28a8c9abd1a0fb609b50c1ed9a9d56b9ec7f5d6a` | 5 |
| `scripts/audit_place_duplicates.py` | `03717aa54a75a6e55bbe6b85f65dd66f4cd1cae88632b12c5006087247da1e90` | 1 |
| `scripts/build_building_morphology_candidate.py` | `cdbd39940e238f73aa9262d051840b8020ba37423b2889eeed23273aeb3158f5` | 1 |
| `scripts/build_building_source_prior.py` | `f5e52470b366f3b0c6adb39d5fad8ee9c5b43039ca71d9aed9de8a9f991b1f9c` | 1 |
| `scripts/build_facility_catchment_candidate.py` | `0683ffa78db3836789047a98f3b382e8886ca4694b1c934d98faffe4aa724bca` | 1 |
| `scripts/build_schema_presence_candidate.py` | `70f771ff18a6904be3f7e8ee17da6e1e447e8f9d6931bcebddb567faea720f39` | 1 |
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
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-015
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-016
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-017
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-018
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-019
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-020
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-021
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-022
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-023
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-024
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-025
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-026
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-027
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-028
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim bias-discovery-029
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
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-047
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-048
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-049
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-050
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-051
# exit 0
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_claim.py --claim methodology-052
```

Reproduce commands (executed once with the authorised workspace and recorded in the committed execution log; products are hash-verified by every audit run):

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
/home/martin/hddProjects/langywrap/uv run --quiet python scripts/snapshot_lb_and_scorecards.py
# exit 0
MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with matplotlib==3.10.8 python scripts/discovery_postfire_stale_reference.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire.json --map reports/discovery-postfire-map.png
```

## Unresolved claims

None. Every quantitative claim in the three writeup documents carries a verified evidence pointer, a passing verify command and a hash-verified reproduction path.

## Limits of this screen

This is a documentation screen. Presence of a number in the cited artifact proves the statement was not invented and is reproducible from committed permitted data; it does not make an unvalidated proxy accurate, does not endorse any interpretation, and does not assess the hidden organiser target. Statements about real-world consequences remain hypothetical, and no claim here is an accuracy measurement. No candidate was changed and nothing was uploaded.
