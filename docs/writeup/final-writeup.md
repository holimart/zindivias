# Bias Bounty Mapping Equity Challenge — discovery and methodology
Martin Holeček (Zindi: holi) — 2026-10-30

## Executive summary

- **Rural source attribution:** rural tracts have 16.8–38.7 pp lower OSM-attributed building shares than urban tracts across all four regions (`reports/source-provenance.json`).
- **Rio Grande border metros:** mean OSM share is 0.092 vs 0.615; adjusted border coefficient β=−0.446, with a 28.7% `other` provider-tag share—the putative Google Open Buildings spill-over, not a removed reference comparison (`reports/discovery-border-metros.json#/results`).
- **Paradise/Magalia:** Overture/WRC ratios are 0.620–0.747 in six tracts within the Microsoft-derived WRC lineage; the CAL FIRE DINS mechanism was not confirmed (0/6 focal passes; `reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`; `reports/discovery-dins-mechanism.json#/decision`).
- **Tribal comparison:** the pooled result is region-confounded; 805 of 858 tribal tracts are in Eastern Oklahoma (`reports/discovery-strata-confound.json#/stratum_composition`).
- **Out-of-region transfer:** Google-tagged shares were 0.330 in El Paso, 0.292 in Imperial, and 0.108 in Doña Ana, versus 0.000 in both controls (`reports/discovery-border-transfer.json#/results`).
- **Who is affected:** CDC PLACES 2025 estimates uninsured prevalence for adults aged 18–64 at 36.9% in the ten Texas border counties vs 18.1% elsewhere in the scored region (county-cluster 95% CI for the difference +16.6 to +22.1 pp); population context, not evidence about low-OSM tracts (`reports/who-is-affected-cdc-places.json`; `$COMPETITION_WORKSPACE/external-discovery-2026-10-29-fable/cdc-places-2025-tx/MANIFEST.json`).
- **Scored methods:** `V2_model_cgs` and its pre-registered `B_from_A_cgs` companion use provided features only (`reports/final-two.json#/final_candidates`).

## Submission context

- Protocol A: `V2_model_cgs`; leaderboard submission `hvLtPVmr`.
- Protocol B: `B_from_A_cgs`; leaderboard submission `eAaWXWxM`.
- Public RMSE (not private-split evidence): V2 0.1110; B 0.0975.
- Predictions use provided features only; no labels, hidden targets, placeholders, or removed references.
- All files cited by path (`reports/*.json`, `scripts/*`, `docs/writeup/appendices/*`) are in the submission repository: https://github.com/holimart/zindivias (CC-BY-SA 4.0); the organisers may also request it directly.
We request consideration for both Best Bias Discovery ($1,000) and Best Documentation ($500).

## Contents

1. Best Bias Discovery — findings.
2. [Related entries on the challenge board](#related-entries-on-the-challenge-board) (after the findings).
3. Methodology and score interpretation.
4. Appendix A: [detailed methodology](methodology.md).
5. Appendix B: [edge cases](edge-cases.md).
6. Appendix C: [supporting discovery screens](appendices/screens-that-did-not-produce-a-finding.md).
7. [Supporting diagnostic archive](appendix-diagnostics.md).
8. Appendices D–J: upload records, external-source compliance, reproducibility, provenance, named high-SVI OSM examples, and supporting discovery tables.

## Best Bias Discovery entry

### Finding 1 — Rural tracts have lower OSM-attributed shares of Overture buildings

#### Why it matters

Across all four regions, rural tracts have lower mean OSM-attributed shares of Overture buildings than urban tracts. This is a provided-data source pattern, not an organizer-score comparison or evidence of missing or incorrect buildings.

#### Evidence

| Region | Rural mean | Urban mean | Rural − urban (percentage points) | 95% tract-bootstrap CI (pp) | n tracts (rural, urban) |
|---|---:|---:|---:|---:|---:|
| Eastern Oklahoma | 9.87% | 26.70% | −16.83 | [−19.24, −14.71] | 1,191 (411, 780) |
| Maricopa, Arizona | 49.20% | 87.38% | −38.17 | [−43.15, −33.28] | 1,582 (163, 1,419) |
| Northern California | 24.70% | 61.57% | −36.87 | [−41.94, −32.01] | 591 (147, 444) |
| South-Central Texas | 25.15% | 63.83% | −38.68 | [−40.74, −36.56] | 5,989 (991, 4,998) |

Values are copied from `reports/source-provenance.json` (SHA-256 `e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565`). Rural/urban use `pct_urban < 0.5`/`>= 0.5`; missing is excluded. Means are unweighted across tracts; intervals use 2,000 tract-bootstrap resamples. All CIs are below zero; no spatial adjustment.

#### Named high-SVI examples

The report's `named_lowest_osm_high_svi` lists contain the ten lowest non-null OSM-attributed shares among finite-SVI Q4 tracts in each region; examples are not all rural. Full GEOIDs, county/place labels, and shares are in [Appendix I](#appendix-i-named-high-svi-tracts-with-lowest-osm-attribution); labels do not change selection or metrics. The provided-SVI gradient is detailed in [Appendix K](#appendix-k-san-antonio-area-svi-gradient).

Rural, higher-vulnerability residents may rely on building context for heat, drought, wildfire, and evacuation planning; missing buildings, inaccurate maps, service harm, and dispatch/evacuation outcomes were not measured. Mappers should treat the named tracts as partner-led review priorities and verify proposed edits with local and tribal knowledge.

**Limits and reproduction.** OSM attribution is provenance, not accuracy, completeness, age, or local mapping activity; provider memberships may overlap. Only provided Overture source fields and strata were used; no removed reference layers, scores, or candidate outputs (`reports/source-provenance.json`, cited SHA). Rebuild with `uv run --python 3.12 --no-project --isolated --with-requirements requirements.txt python scripts/discovery_source_provenance.py --workspace "$COMPETITION_WORKSPACE" --out reports/source-provenance.json --figure reports/source-provenance.png`.

### Finding 2 — Rio Grande border metros: Overture's machine-only source mix

#### Impact and novelty

Lead: border metros have machine-only footprints with an Overture third provider, beyond low OSM. Ector has lower OSM than El Paso (6.7% vs 9.3%), 47.7% Esri, and zero border-provider share; source, not accuracy, is measured.

CDC PLACES 2025 estimates uninsured prevalence among adults aged 18–64 at 36.9% (about one adult in three) in these ten border counties versus 18.1% elsewhere in the South-Central Texas scored region (difference +18.8 pp, county-cluster 95% CI +16.6 to +22.1 pp; 455 joined border tracts). This describes the border population where the place effect occurs; it is not evidence that low-OSM tracts are sicker. Health outcomes and dispatch/evacuation impacts were not measured (`reports/who-is-affected-cdc-places.json#/contrasts/ten border counties vs rest of South-Central Texas`).

#### Evidence and mechanism

Across 460 border tracts, OSM mean is 9.18% vs 61.48% elsewhere (68.66% urban); difference −52.30 pp (95% county-cluster CI −61.24 to −36.99), adjusted β=−0.446 (SE 0.070; n=5,958, 185 counties), descriptive; full counts in Appendix J.

`Other` is an Overture `sources` label, not removed Google Open Buildings or USGS reference data. The border label matches the first provider; non-border Texas, the second (Bexar cache share 0.336; ±526 count matches). This provider-label inference is not proof (Appendix J).

#### Does it transfer?

“Google-only” denotes an Overture provider tag, not a removed reference layer: 10.8–33.0% in three unscored border counties and zero in both controls. El Paso OSM/provider (9.3%/33.0%) matches the scored Rio Grande OSM range (4.2–12.0%); Doña Ana has 10.8% provider and 26.1% Esri. No interval.

| County | OSM | Third-provider tag | Esri |
|---|---:|---:|---:|
| **El Paso TX** | 0.093 | **0.330** | 0.000 |
| **Imperial CA** | 0.200 | **0.292** | 0.000 |
| **Doña Ana NM** | 0.179 | **0.108** | 0.261 |
| Ector TX (control) | 0.067 | **0.000** | 0.477 |
| Bernalillo NM (control) | 0.809 | **0.000** | 0.000 |

Full results and reproduction: [Appendix J](#appendix-j-supporting-discovery-tables).

#35031/#35474's Oklahoma Esri county-sharing mechanism coexists: Esri is 47.7% in Ector and 26.1% in Doña Ana.

Manifest: `$COMPETITION_WORKSPACE/external-discovery-2026-10-29-fable/MANIFEST.json`; rerun report `reports/discovery-border-transfer.json` (SHA-256 `64037e7360dab85eae3f748ec5d0ec14155999587302ae12e83bd3f9a893b795`); command in Appendix J. No scores or removed references.

### Finding 3 — Paradise/Magalia building-count discrepancy

#### Impact and evidence

In six Paradise/Magalia Butte County tracts, provided Wildfire Risk to Communities counts exceed Overture (ratios 0.620–0.747); the geographic/vintage discrepancy does not establish fire damage as its cause. Treating larger counts as current could misdirect field checks. Provided MTBS last-burn year is 2018; public CAL FIRE DINS is a discovery-only mechanism check (retrieved 2026-10-29; catalogue CC BY, version unspecified). Tract-level rows are in [Appendix J](#appendix-j-supporting-discovery-tables).

Focal GEOIDs and DINS rows appear in [Appendix J](#appendix-j-supporting-discovery-tables) (`reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`; `reports/discovery-dins-mechanism.json#/focal_butte_tracts`). The tract-bootstrap difference is +9.55 pp (95% CI [+0.55, +20.89]); the county-cluster bootstrap 95% CI [-1.65, +16.67] includes zero, so magnitude is exploratory (`reports/discovery-postfire-county-bootstrap.json#/county_cluster_bootstrap/cluster_95_percentile_interval`). The map marks these six tracts:

![NorCal/Butte tract map: fill is provided MTBS burned percentage; the six focal tracts are marked.](../../reports/discovery-postfire-map.png)

**Negative mechanism check.** DINS supports neither the proposed fire-damage nor rebuilding mechanism: 0/6 tracts met the ≤0.30 relative-error rule (required ≥4/6); Spearman was 0.463 across 28 2018-burn tracts, below 0.60. Among 591 NorCal tracts, 576 positive-count tracts have no DINS record (1,139,778 buildings); missing records do not establish absence or rebuilding. MTBS percentages are cumulative (64.4–153.8%); reference-count vintage is unverified. The border finding's SVI control is documented in Appendix F.

Focal shares show Microsoft-only 0.430–0.990 versus OSM-only 0.001–0.089; Overture/WRC ratios 0.620–0.747 against the provided Microsoft-derived USFS WRC count support a within-provider vintage gap, not OSM conflation (`reports/postfire-lineage-recheck.json#/focal_share_ranges`; `reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`). Pooled OLS gives β(OSM-only)=−0.016 (county-clustered SE 0.041; n=9,337; controls: urbanity, log density, region FE); seven unburned NorCal tracts have ratio <0.8, all urban—six Sacramento, one Placer (0.711–0.775)—so not all Sacramento (`reports/postfire-lineage-recheck.json#/pooled_ols`, `#/unburned_northern_ca_ratio_below_0_8`).

Entries #35520 and #35527 (23 Oct) provide stronger DINS-matched wildfire evidence; we acknowledge that work and limit Finding 3 here to the separate provider-lineage result and negative mechanism check ([related-entry omissions ledger](related-entries.md)).

Reproduce with `scripts/discovery_postfire_stale_reference.py`, `scripts/audit_discovery_postfire_county_bootstrap.py`, and `scripts/discovery_dins_mechanism.py`; inputs, filters and hashes are recorded in reports/manifests.

### Finding 4 — The tribal/non-tribal pooled comparison is region-confounded

#### Impact and novelty

Regional composition can explain the pooled tribal/non-tribal contrast; this is not evidence of a general tribal disparity or causation.

#### Evidence

Of 858 `tribal_any` tracts, 805 (93.82%) are in Eastern Oklahoma; 6,002 of 8,521 non-tribal tracts (70.44%) are in South-Central Texas (`reports/discovery-strata-confound.json#/stratum_composition`).

#### What this does and does not show

These are composition counts, not equity or target-score evidence. `tribal_any` is an overlap indicator, not legal status; comparisons should be region-stratified (`reports/discovery-strata-confound.json#/regional_counts_and_prevalence`, `#/missing`).

#### Reproducibility

Rebuild: `python scripts/discovery_strata_confound.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-strata-confound.json`.

## Related entries on the challenge board

The author of #34744 (13 Sep) withdrew the colonia-gap claim on 7 Oct because it relied on removed reference layers; its replacement concerns “dispatchable roads” but gives no county, interval, or mechanism, while #35071 (28 Sep) uses a reconstructed target for its “colonias vacuum” and acknowledges tract labels do not verify colonia boundaries. #34972 (23 Sep) and #35333 (14 Oct) describe rural road-vintage/phantom-road findings affecting roughly 33,000–400,000 people, not metropolitan building coverage, while #35011 (25 Sep) frames Hidalgo as a high-CVI tract set without a headline interval or Google-source analysis; our border-metro result instead covers 460 tracts and 1.77 million residents (89% urban) and reports an OSM-share association net of Hispanic share and urbanity (β=−0.446, SE=0.070, county-clustered). #35031 (26 Sep) and #35474 (21 Oct) identify county data-sharing as a mechanism in Oklahoma but do not report Google Open Buildings or an out-of-region test; we add Google as a third provider in the border-building source mix and find Google-only footprints in all three out-of-region border counties (El Paso 33.0%, Imperial 29.2%, Doña Ana 10.8%) and in neither non-border control (Ector or Bernalillo, 0.0%). #35264 (8 Oct) reports a pooled OSM-by-SVI gradient with leaderboard-calibrated gap magnitudes and regional-composition sensitivity, while #35030 (26 Sep) reports tribal-fire-station gaps in a sample where 805 of 858 tribal tracts are in Eastern Oklahoma; our San Antonio SVI contrast is within one city (Q4−Q1 −19.6 pp [−31.4, −7.9]) and our tribal comparison is explicitly region-confounded, not a general tribal effect. #34995 (25 Sep) reuses the reconstructed-gap approach withdrawn in #34744 for its mobile-home result, #34962 (23 Sep) owns the address-layer finding (we make no address claim), and #35520/#35527 (23 Oct) provide stronger DINS-matched wildfire evidence; we cite that work and limit Finding 3 to the provider-lineage result (OSM-only 0.1–8.9%) and its negative mechanism check. For who is affected, the public-domain CDC PLACES 2025 tract join (retrieved 29 Oct) describes the border population rather than low-OSM tracts: the ten border counties' uninsured prevalence for adults aged 18–64 is 36.9% versus 18.1% elsewhere in the region (county-clustered 95% CI for the difference +16.6 to +22.1 pp).

| What ours adds |
|---|
| Border **metros + buildings**; Google as a third provider and cross-border spill-over; an adjusted place association net of Hispanic share; a San Antonio within-city gradient; an out-of-region transfer test; and a CDC PLACES health join about the border population. |

## Methodology (Best Documentation entry)

### What the submitted score is

Final-two: `V2_model_cgs` (`hvLtPVmr`; SHA-256 `5d0607d5531793008d8d5c86b1625f26e5bb8e9504a2b4a4a84f0224a0f5cef7`) and `B_from_A_cgs` (`eAaWXWxM`; SHA-256 `38e17cf1ec99f4f3fc5d358d13ccd0edfabe947d81fd6b6e5ef61e6cea006607`). Each is a 9,794-row `GEOID,coverage_gap_score` file copied byte-for-byte from its frozen source. 9,379 GEOIDs are official; the other 415 eastern-WA template rows use registered zero fill, not target estimates.

V2 components:

- **Transport:** `T_i=clip(1−named_road_km_5070_i/route_km_i,0,1)`, defined iff `route_km_i>0`; uses provided Overture named-road and eligible route lengths.
- **Buildings:** `G_i=0.01` iff the provided Overture building count is positive; otherwise undefined (placeholder zero excluded).
- **POI:** For fire/EMS/schools, `p_t=sigmoid(a_t+b_t·log1p((pop_total/max(ALAND_km²,.01))/100)+c_t·u)`, with fixed `(a,b,c)` respectively `(-2.8,.30,.70)`, `(-3.2,.25,.60)`, `(-1,.20,.35)`. `u=pct_urban` if finite, else `pop_urban/pop_total` if population positive, else `.5`. `n_t` is provided Overture category count plus its registered unclassified-place share. Type gap=`p_t×clip(1−n_t/(n_t+1),0,1)`, defined iff `p_t>0`; facility half averages defined types. Establishments half=`clip(1−all_overture_poi_count/(.024×pop_total),0,1)`, defined iff population or all-place count is positive (positive count at zero population gives zero). POI is the equal mean of defined halves, or undefined if neither is. Inputs: provided Overture category/all-place counts and `pop_total`, `pop_urban`, `pct_urban`, `ALAND`; priors and `.024` are fixed, not fitted to scores/labels.

V2 is the arithmetic mean of defined `T_i`, `G_i`, and `POI_i`; undefined terms are omitted. B changes only the composite: `B_i=.5×V_i+.5×mean(V_j)` over official tracts *j* in the same region. Its component columns are unchanged; its regional mean uses V2 predictions, not targets.

**Fresh-clone rebuild:** committed V2 source/components plus a workspace containing `samples/<region>-sample-submission.csv` (four official files) and `SampleSubmission.csv` (GEOID/region metadata) suffice; no private feature cache is needed. From the repository root:

```sh
uv run python scripts/build_candidate_b_from_a.py --workspace "$COMPETITION_WORKSPACE" && \
uv run python scripts/build_bias_column_order_cgs.py --workspace "$COMPETITION_WORKSPACE"
```

Verified in a scratch clone; both hashes matched.

## Appendix A: Detailed methodology and estimator diagnostics

Detailed formulas, assumptions, and reproduction notes are in the linked [methodology source](methodology.md). Alternative estimator-family and sensitivity diagnostics are in [Appendix diagnostics](appendix-diagnostics.md).

## Appendix B: Edge cases and documented limitations

The complete edge-case table and calculation policy are available in the linked [edge-case appendix](edge-cases.md).

## Appendix C: Screens that did not produce a finding

Supporting discovery screens and limitations are available in the linked [screen appendix](appendices/screens-that-did-not-produce-a-finding.md). External-layer URLs, retrieval dates, and licence/terms status are summarized in Appendix F.

## Appendix D: Leaderboard submission records and score eligibility

Historical five-column uploads placed `transport_gap` second, so their returned public values are preserved as records but void for A/B selection. The `_cgs` rows use `GEOID,coverage_gap_score`; the four cohort scores were used only by the frozen A rule, which selected the final A shown below; the B companion score was excluded from A selection. No public score is used to alter a candidate value.

| Platform submission id | Candidate | Artifact | Artifact SHA-256 | Public RMSE | Split | Score source | Selection eligibility | Uploaded (UTC) |
|---|---|---|---|---:|---|---|---|---|
| uRRw9KpB | C0 | `reports/candidates/C0_all_zero.csv` | `0edf8140123e2135489e5728fd71bce6f1e28a97f447ca411ba45fdbc02bc214` | 0.107246299 | public | zindi_api_status | void for selection: five-column file had transport_gap in column 2 | 2026-10-24T09:18:02+00:00 |
| BvyKrR6v | C1 | `reports/candidates/C1_region_constants.csv` | `dc2e368194b31408cb5c313fa8ce3f3fa59a1b5965bc2df8e4217bbc9e0c82e0` | 0.146449112 | public | zindi_api_status | void for selection: five-column file had transport_gap in column 2 | 2026-10-24T09:18:25+00:00 |
| rTARuUaG | C2 | `reports/candidates/C2_component_estimator_v0.csv` | `5728fbbaa2e062fbd14199190a260132c95b2b858da539d7a9580cb2abc62514` | 0.235358712 | public | zindi_api_status | void for selection: five-column file had transport_gap in column 2 | 2026-10-24T09:19:08+00:00 |
| TM83xxU6 | C3 | `reports/candidates/C3_denominator_x125.csv` | `e5ee50f5c2cca149550f3408638d2b77431f6ed62cf104dfe8c07d8c8416f9e4` | 0.349409867 | public | zindi_api_status | void for selection: five-column file had transport_gap in column 2 | 2026-10-24T09:19:54+00:00 |
| ySXi73rd | P4 | `reports/candidates/P4_c1_shrink_alpha.csv` | `8b329802d882ea4bdff2bfd8c465c160d6ffd9269cfbe28dce8be9e702b07d37` | 0.091440198 | public | zindi_api_status | void for selection: five-column file had transport_gap in column 2 | 2026-10-24T09:38:39+00:00 |
| iQw8C6Q9 | A1_physical_equal | `reports/candidates/A1_physical_equal.csv` | `6af088b83ff3481d420e11b14d903fc510c2b4be2cea285c762ca12d500862de` | 0.126543547 | public | zindi_api_status | void for selection: five-column file had transport_gap in column 2 | 2026-10-26T14:23:12+00:00 |
| W83LEghZ | A2_spectral | `reports/candidates/A2_spectral.csv` | `f88573996566a1975726845a49f42fb2cd3781d5f0e83d55944fc65647692997` | 0.146089513 | public | zindi_api_status | void for selection: five-column file had transport_gap in column 2 | 2026-10-26T14:23:12+00:00 |
| tGB6sGRx | A3_dawid_skene | `reports/candidates/A3_dawid_skene.csv` | `6d588f2f7a45a58b128860a049ac19995b21902f7f98d3b2ab39d1d9fbb073b3` | 0.146089513 | public | zindi_api_status | void for selection: five-column file had transport_gap in column 2 | 2026-10-27T00:19:00+00:00 |
| EbwLNkmt | V2_model | `reports/candidates/V2_model.csv` | `3193ee78a64004f007829f1f9c12d4aa8d4f7c15136771e8da9108fdcea06f12` | 0.121644596 | public | zindi_api_status | void for selection: five-column file had transport_gap in column 2 | 2026-10-27T00:19:00+00:00 |
| hvLtPVmr | V2_model_cgs | `reports/candidates/V2_model_cgs.csv` | `5d0607d5531793008d8d5c86b1625f26e5bb8e9504a2b4a4a84f0224a0f5cef7` | 0.11102814 | public | zindi_api_status | selected A by frozen corrected-order _cgs rule | 2026-10-28T07:15:46.098Z |
| 4R4vkHSd | A1_physical_equal_cgs | `reports/candidates/A1_physical_equal_cgs.csv` | `2da0051af221ddf26a8a73740746811dbd956854a1fc12846c99864d3abae8a1` | 0.309628459 | public | zindi_api_status | corrected-order _cgs A-rule cohort; not selected as A | 2026-10-28T07:15:51.544Z |
| s7mXS43p | A2_spectral_cgs | `reports/candidates/A2_spectral_cgs.csv` | `584e086bb5df950f6079317afcdc7c5a0182b5263d170be2a10987d1ac4c6213` | 0.262578881 | public | zindi_api_status | corrected-order _cgs A-rule cohort; not selected as A | 2026-10-28T07:16:00.493Z |
| B8de77WD | A3_dawid_skene_cgs | `reports/candidates/A3_dawid_skene_cgs.csv` | `432ade8f1e46c6804cb34fb790612bb1befc9fe03b67a545e753be46b549237c` | 0.15224784 | public | zindi_api_status | corrected-order _cgs A-rule cohort; not selected as A | 2026-10-29T00:10:29.255Z |
| eAaWXWxM | B_from_A_cgs | `reports/candidates/B_from_A_cgs.csv` | `38e17cf1ec99f4f3fc5d358d13ccd0edfabe947d81fd6b6e5ef61e6cea006607` | 0.097535343 | public | zindi_api_status | pre-registered B companion; score excluded from A selection | 2026-10-29T00:12:18.199Z |

The scored methodology candidate in this writeup is `V2_model`, built from provided features only (commit `b45733aa31903c71ec4f3fb432c2bc9d519f3ff9`). The original five-column V2 upload is void for selection because Zindi scored its transport_gap column. The frozen A rule selected V2_model_cgs from the four successful corrected-order _cgs scores (public RMSE 0.11102814; submission hvLtPVmr). B_from_A_cgs is its pre-registered 50% shrink companion (public RMSE 0.097535343; submission eAaWXWxM); B's score was not used to select A.

The earlier five-column uploads above are retained as historical calibration records for auditability. Their column-2 scores measured `transport_gap` and are void for selection; the correctly ordered `_cgs` evaluations are separately labeled, and no score value, fitted constant or probe outcome is used to change any candidate prediction or discovery claim.

Final-two protocol outcome: A=`V2_model_cgs` / `hvLtPVmr` (0.11102814); B=`B_from_A_cgs` / `eAaWXWxM` (0.097535343). A was selected from exactly four corrected-order `_cgs` scores; unique winner `V2_model_cgs` exceeded all pairwise noise guards. B is the pre-registered companion, not an A-rule competitor.

## Appendix E: Prize consideration and official criteria

We request consideration for both Best Bias Discovery ($1,000) and Best Documentation ($500).

### Best Bias Discovery entry — Best Bias Discovery ($1,000)

- Prize: Best Bias Discovery ($1,000).
- Official note: Entries for Best Bias Discovery are judged from the writeup by the organizing team against four criteria
- Judging criteria and weights (official text):
  - Impact: 30%.
  - Novelty: 25%.
  - Evidence: 25%.
  - Reproducibility: 20%.
- Tie-break: Ties are broken by earlier submission.

### Best Documentation entry — Best Documentation ($500)

- Prize: Best Documentation ($500).
- Official note: No separate leaderboard submission is required for Best Bias Discovery or Best Documentation.

## Appendix F: External layers and source register

Five public external layers were used only for the discovery screen described in the linked supporting analysis; none enters scored predictions. Retrieval dates and licence/terms verification status are recorded below. Where terms were not verified, no reuse or public-domain claim is made.

| Layer | Source URL | Retrieved | Licence / terms status |
|---|---|---|---|
| CDC/ATSDR SVI 2022 | https://svi.cdc.gov/Documents/Data/2022/csv/states/SVI_2022_US.csv | 2026-10-27 | Dataset-specific terms were not stated on the fetched ATSDR page. Treated as a U.S. federal government work under 17 U.S.C. Section 105, subject to third-party exceptions; this is a statutory assessment, not a quoted dataset-specific licence. |
| USDA ERS RUCA 2020 | https://www.ers.usda.gov/media/5443/2020-rural-urban-commuting-area-codes-census-tracts.csv | 2026-10-27 | No terms/copyright statement found on fetched ERS page; unverified; ERS requests citation. |
| Census Planning Database 2026 | https://www2.census.gov/adrm/PDB/2026/pdb2026tr.csv | 2026-10-27 | Not verified; Census terms/copyright pages were not read. |
| Census TIGER/Line 2020 AIANNH polygons | https://www2.census.gov/geo/tiger/TIGER2020/AIANNH/tl_2020_us_aiannh.zip | 2026-10-27 | Not verified; Census terms/copyright pages were not read. |
| CDC PLACES 2025, Texas census tracts | https://data.cdc.gov/500-Cities-Places/PLACES-Local-Data-for-Better-Health-Census-Tract-D/cwsq-ngmh | 2026-10-29 | Public Domain per Socrata metadata (license.name='Public Domain', licenseId='PUBLIC_DOMAIN'; U.S. federal work). Retrieved CSV SHA-256 and query/filter are recorded in $COMPETITION_WORKSPACE/external-discovery-2026-10-29-fable/cdc-places-2025-tx/MANIFEST.json; report SHA-256 and input hashes are in reports/who-is-affected-cdc-places.json. |

Source-file hashes, download timestamps, and source-page licence checks are recorded in `$COMPETITION_WORKSPACE/external-discovery-2026-10-27/MANIFEST.json`; the data files are not redistributed in this packet.

Reproduce the CDC PLACES join and county-cluster intervals using `uv run --python 3.12 --no-project --isolated --with-requirements requirements.txt --with-requirements requirements-cdc-analysis.txt python scripts/audit_who_is_affected_cdc_places.py --workspace "$COMPETITION_WORKSPACE" --out reports/who-is-affected-cdc-places.json`. Input hashes, query/filter, outcomes, and limitations are recorded in the report and CDC workspace manifest.

The general register below lists prior-art sources, organiser-hosted challenge files, and public discovery endpoints. Scored predictions read only files in the organiser data package.

| URL | Reference | Accessed | Role |
|---|---|---|---|
| https://api.zindi.world/v1/competitions/bias-bounty-mapping-equity-challenge/files/SampleSubmission.csv | organiser-hosted challenge file used by the workspace downloader | 2026-10-23 | official challenge data endpoint provided by the organisers; not an external dataset |
| https://api.zindi.world/v1/discussions/35250 | Organiser Challenge Update on supporting comparison evidence and target-reconstruction limits (discussion 35250) | 2026-10-29 | official rule clarification permitting discovery-only comparison evidence when not used to reconstruct, approximate, or calibrate targets |
| https://api.zindi.world/v1/discussions/35334 | Zindi discussion 35334 (2026-10-14): methodology form requires PDF, maximum 10 MB | 2026-10-29 | organiser form upload-format instruction; this project exports a PDF under the stated size cap |
| https://zindi.africa/competitions/bias-bounty-mapping-equity-challenge | official challenge page (quoted rules and membership note) | 2026-10-25 | official challenge page; quoted text only, no data added to scored predictions |
| https://arxiv.org/abs/1502.04585 | Citation: Avrim Blum and Moritz Hardt, “The Ladder: A Reliable Leaderboard for Machine Learning Competitions,” arXiv:150 | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://arxiv.org/abs/1707.01825 | Citation: Jacob Whitehill, “Climbing the Kaggle Leaderboard by Exploiting the Log-Loss Oracle,” arXiv:1707.01825. arXiv | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://data.ca.gov/api/3/action/package_show?id=cal-fire-damage-inspection-dins-data | California State Geoportal metadata record and CC BY catalogue terms | 2026-10-29 | public source metadata used to record DINS attribution licence and dataset limitations |
| https://data.ca.gov/dataset/cal-fire-damage-inspection-dins-data | CAL FIRE Damage Inspection (DINS) Data catalogue; public Best Bias Discovery source, CC BY version unspecified | 2026-10-29 | public external source used only for the Best Bias Discovery mechanism check; not joined to scored predictions |
| https://data.cdc.gov/500-Cities-Places/PLACES-Local-Data-for-Better-Health-Census-Tract-D/cwsq-ngmh | CDC PLACES 2025, Texas census tracts | 2026-10-29 | discovery-only population-health context for ten Texas border counties; not evidence about low-OSM tracts and never used in scored predictions |
| https://docs.overturemaps.org/schema/ | Citation/documentation: Schema overview, Transportation Segment, and Places Place. URL check: all three official pages r | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://docs.overturemaps.org/schema/reference/places/place/ | Citation/documentation: Schema overview, Transportation Segment, and Places Place. URL check: all three official pages r | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://docs.overturemaps.org/schema/reference/transportation/segment/ | Citation/documentation: Schema overview, Transportation Segment, and Places Place. URL check: all three official pages r | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://doi.org/10.1016/j.buildenv.2023.110295 | Citation: Biljecki et al. (2023), Building and Environment, article 110295, DOI 10.1016/j.buildenv.2023.110295; listed b | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://doi.org/10.1038/s41467-023-39698-6 | Citation: Benjamin Herfort et al., “A spatio-temporal analysis investigating completeness and inequalities of global urb | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://doi.org/10.1080/13658816.2016.1189556 | Citation: H. Senaratne et al. (2017), “A review of volunteered geographic information quality assessment methods,” Inter | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://doi.org/10.1080/13658816.2017.1395883 | Citation: Zhou (2018), International Journal of Geographical Information Science, density–completeness study, DOI 10.108 | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://doi.org/10.1111/tgis.12073 | Citation: Christoph Barron, Pascal Neis, and Alexander Zipf (2014), “A comprehensive framework for intrinsic OpenStreetM | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://doi.org/10.1371/journal.pone.0180698 | Citation: Christopher Barrington-Leigh and Adam Millard-Ball, “The world’s user-generated road map is more than 80% comp | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://doi.org/10.2737/RDS-2020-0016 | Citation/documentation: Scott et al. (2020), Wildfire Risk to Communities: Spatial datasets of landscape-wide wildfire r | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://doi.org/10.3390/ijgi12040143 | Citation: Ullah et al. (2023), ISPRS International Journal of Geo-Information 12(4), article 143, MapSwipe/completeness | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://doi.org/10.3390/ijgi2041066 | Citation: Robert Hecht, Christian Kunze, and Stefan Hahmann, “Measuring Completeness of Building Footprints in OpenStree | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://doi.org/10.3390/ijgi7080289 | Citation: Maria Antonia Brovelli and Giacomo Zamboni (2018), ISPRS International Journal of Geo-Information 7(8), articl | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://github.com/microsoft/USBuildingFootprints | Citation/documentation: microsoft/USBuildingFootprints README. URL check: resolved; GitHub README fetched (repository is | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/release/2026-09-23.1/theme=buildings/type=building/ | This supporting transfer test uses Overture Maps Foundation buildings release 2026-09-23.1, the same release used by the | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/POSTFIRE_MASTER_DATA_SHARE/FeatureServer/0 | CAL FIRE DINS public point-feature service; filtered to Camp incident and non-address fields | 2026-10-29 | public external discovery data endpoint; not used in scored candidate construction |
| https://svi.cdc.gov/Documents/Data/2022/csv/states/SVI_2022_US.csv | CDC/ATSDR SVI 2022 | 2026-10-27 | discovery-only external context; used for EP_HISP control in the border-metro descriptive regression; never used in scored predictions |
| https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title17-section105&num=0&edition=prelim | 17 U.S.C. Section 105, statutory U.S. federal-work public-domain basis (subject to third-party exceptions) | 2026-10-27 | discovery-only external context; used for EP_HISP control in the border-metro descriptive regression; never used in scored predictions |
| https://www.census.gov/library/reference/code-lists/mt-feature-class-codes.html | Citation/documentation: U.S. Census Bureau, MAF/TIGER Feature Class Code Definitions. URL check: official Census page re | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://www.census.gov/programs-surveys/cbp/technical-documentation/methodology.html | Citation/documentation: U.S. Census Bureau, County Business Patterns Methodology. URL check: official Census page resolv | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://www.ers.usda.gov/media/5443/2020-rural-urban-commuting-area-codes-census-tracts.csv | USDA ERS RUCA 2020 | 2026-10-27 | discovery-only external context; never used in scored predictions |
| https://www2.census.gov/adrm/PDB/2026/2026_PDB_Documentation.pdf | Census Planning Database 2026 documentation | 2026-10-27 | discovery-only external context; County_name used solely for naming preselected high-SVI tract examples; never used in scored predictions |
| https://www2.census.gov/adrm/PDB/2026/pdb2026tr.csv | Census Planning Database 2026 | 2026-10-27 | discovery-only external context; County_name used solely for naming preselected high-SVI tract examples; never used in scored predictions |
| https://www2.census.gov/geo/tiger/TIGER2020/AIANNH/tl_2020_us_aiannh.zip | Census TIGER/Line 2020 AIANNH polygons | 2026-10-27 | discovery-only external context; never used in scored predictions |
| https://www2.census.gov/geo/tiger/TIGER2024/PLACE/tl_2024_04_place.zip | U.S. Census Bureau TIGER/Line 2024 place polygons, Arizona | 2026-10-29 | public domain Census geography used only to label preselected example tracts; no finding metric recomputed |
| https://www2.census.gov/geo/tiger/TIGER2024/PLACE/tl_2024_06_place.zip | U.S. Census Bureau TIGER/Line 2024 place polygons, California | 2026-10-29 | public domain Census geography used only to label preselected example tracts; no finding metric recomputed |
| https://www2.census.gov/geo/tiger/TIGER2024/PLACE/tl_2024_40_place.zip | U.S. Census Bureau TIGER/Line 2024 place polygons, Oklahoma | 2026-10-29 | public domain Census geography used only to label preselected example tracts; no finding metric recomputed |
| https://www2.census.gov/geo/tiger/TIGER2024/PLACE/tl_2024_48_place.zip | U.S. Census Bureau TIGER/Line 2024 place polygons, Texas | 2026-10-29 | public domain Census geography used only to label preselected example tracts; no finding metric recomputed |
| https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_04_tract.zip | U.S. Census Bureau TIGER/Line 2024 tract polygons, Arizona | 2026-10-25 | public domain Census geography used only to match preselected GEOIDs to Census places; no finding metric recomputed |
| https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_06_tract.zip | U.S. Census Bureau TIGER/Line 2024 tract polygons, California | 2026-10-25 | public-domain geography for outside-region footprint-to-tract assignment and matching preselected GEOIDs to Census places; not target reference data |
| https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_35_tract.zip | U.S. Census Bureau TIGER/Line 2024 tract polygons, New Mexico | 2026-10-25 | public-domain geography used only for outside-region footprint-to-tract assignment; not target reference data |
| https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_40_tract.zip | U.S. Census Bureau TIGER/Line 2024 tract polygons, Oklahoma | 2026-10-25 | public domain Census geography used only to match preselected GEOIDs to Census places; no finding metric recomputed |
| https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_48_tract.zip | U.S. Census Bureau TIGER/Line 2024 tract polygons, Texas | 2026-10-25 | public-domain geography for outside-region footprint-to-tract assignment and matching preselected GEOIDs to Census places; not target reference data |

Provided-data-only check for the scored predictions (`reports/v2-input-provenance.json`): field-lineage fraction 1.0, 0 prohibited source dependencies, 0 unclassified inputs, external data added to scored predictions: False.

## Appendix G: Reproducibility audit

A machine-checked claim-to-evidence audit backs every number in this writeup.

- `claims_total`: 83
- `claims_fully_traceable`: 83
- `fraction_quantitative_claims_traceable`: 1.0
- `numeric_tokens_total`: 967
- `numeric_tokens_covered`: 967
- `fraction_numeric_tokens_covered`: 1.0
- `distinct_verify_commands`: 83
- `distinct_reproduce_commands`: 21
- `derived_aggregates_verified`: 39
- `claims_asserting_accuracy`: 0

Current audit status: 83 of 83 quantitative claims are fully traceable (1.0); 967 of 967 numeric tokens are covered (1.0). There are 0 unresolved claim-evidence items. The full claim audit does not pass overall reproduction acceptance: 10 report-product differences remain outside accepted disclosure paths and 2 of 21 reproduction commands did not exit successfully (`reports/writeup-claim-evidence-audit.json#/commands`). This is a reproduction limitation, not an accuracy claim. The final-two result is separately traced to `reports/final-two.json` and machine-compared by the packet verifier.

The claim inventory, audit configuration, and execution details are available in [`claim-evidence-audit.md`](claim-evidence-audit.md) and the linked JSON reports: `reports/writeup-claim-evidence-audit.json`, `reports/writeup-claim-inventory.json`, and `reports/writeup-claim-evidence-execution.json`.

Deterministic audit artifacts: `reports/writeup-claim-evidence-audit.json`, `reports/writeup-claim-inventory.json`, `reports/writeup-claim-evidence-execution.json`.

## Appendix H: Data provenance and prohibited-source statement

- Scored predictions are built only from the organiser-provided Overture roads, buildings and POI extracts, the provided census/ACS table, the provided strata table and the platform submission template, plus fixed documented priors.
- The organisers removed the reference layers (TIGER, MS Buildings, USGS, CBP). None of those layers, nor an equivalent copy, is used to build scored predictions or to reconstruct, derive, approximate, or calibrate target values.
- The organiser's 2026-10-08 Challenge Update (Zindi discussion #35250) permits removed reference layers as supporting comparison evidence in Best Bias Discovery, provided they are not used for target reconstruction or calibration. The Camp Fire comparison uses the provided USFS Wildfire Risk to Communities count and incident-filtered CAL FIRE DINS inspection points; the border finding uses a provided USFS building-count stratum. These are descriptive discovery comparisons only, not model inputs or target reconstruction. DINS provenance is recorded in Appendix F.
- Overture source tags named Google Open Buildings and USGS lidar are source labels in the provided source-provenance report; no external Google or USGS footprint/reference layer was accessed or used to build predictions or reconstruct the target.
- No sample score placeholder, no label, and no leaderboard-probe outcome is used as model evidence. The retired leaderboard-fitted constants and probe values of the retired probe set are listed in `reports/probe-retirement-2026-10-24.json` and appear nowhere in the model chain, in this writeup, or in any claim evidence, by name or by value.
- Equity and hazard strata columns are used only for the descriptive discovery screen, never as model inputs; the descriptive reports state that they are not accuracy measurements.
- All code and tooling used is open source, as the rules require.

### Quoted official page text

Source: https://zindi.africa/competitions/bias-bounty-mapping-equity-challenge (documented 2026-10-25). official challenge page; quoted text only, no data added to scored predictions.

> One membership note: the Maricopa package is 1,592 Arizona tracts plus one New Mexico tract, 35023970000 (Hidalgo County, Tract 9700), which the region boundary meets at the state line.
> Participants must not use reference layers, or equivalent copies obtained elsewhere, to recreate or approximate the organiser-computed targets.

- Used for: cross-state scored tract disclosure in the edge-case section; independently verified present in the official Maricopa sample.
- Used for: prohibited-source statement.
## Appendix I: Named high-SVI tracts with lowest OSM attribution

Selection and OSM-attributed shares below are copied from the 40 `named_lowest_osm_high_svi` report entries; no share or selection was recomputed. The list is the ten lowest non-null shares among finite-SVI Q4 tracts in each region, with Q4 defined within region. Source: `reports/source-provenance.json`, SHA-256 `e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565`.

County names are the Census PDB 2026 `County_name` field joined by `GEO_ID` (file SHA-256 `2faf8d075dfdf9841dc265c40b97f181a5716d604672eb130444138e363f65d5`). Place names are TIGER/Line 2024 `NAMELSAD` values from the Census place polygon with the greatest area overlap with each TIGER tract polygon, after projecting both to EPSG:5070; ties are alphabetical. An em dash means no place polygon overlaps. These geographic labels only annotate the preselected report records. The four state place-file URLs, retrieval dates, and manifest are listed in Appendix F and `$COMPETITION_WORKSPACE/external-discovery-2026-10-29/census-tiger-2024-places/MANIFEST.json`.

| Region | GEOID | County | Census place | OSM-attributed share |
|---|---|---|---|---:|
| Eastern Oklahoma | `40109108803` | Oklahoma County | Oklahoma City city | 0.063% |
| Eastern Oklahoma | `40141070200` | Tillman County | Tipton town | 0.083% |
| Eastern Oklahoma | `40109107103` | Oklahoma County | Oklahoma City city | 0.090% |
| Eastern Oklahoma | `40107081000` | Okfuskee County | Weleetka town | 0.242% |
| Eastern Oklahoma | `40101000300` | Muskogee County | Muskogee city | 0.281% |
| Eastern Oklahoma | `40001376600` | Adair County | Chewey CDP | 0.316% |
| Eastern Oklahoma | `40079040299` | Le Flore County | Spiro town | 0.411% |
| Eastern Oklahoma | `40001376700` | Adair County | Proctor CDP | 0.434% |
| Eastern Oklahoma | `40079040602` | Le Flore County | Poteau city | 0.460% |
| Eastern Oklahoma | `40079040401` | Le Flore County | Poteau city | 0.498% |
| Maricopa, Arizona | `04003000700` | Cochise County | Douglas city | 2.250% |
| Maricopa, Arizona | `04021001305` | Pinal County | Casa Grande city | 2.803% |
| Maricopa, Arizona | `04003000902` | Cochise County | Douglas city | 4.471% |
| Maricopa, Arizona | `04021941401` | Pinal County | Ak-Chin Village CDP | 5.639% |
| Maricopa, Arizona | `04007000900` | Gila County | Claypool CDP | 5.928% |
| Maricopa, Arizona | `04013941200` | Maricopa County | — | 6.238% |
| Maricopa, Arizona | `04021000904` | Pinal County | Florence town | 7.212% |
| Maricopa, Arizona | `04003000600` | Cochise County | Douglas city | 7.212% |
| Maricopa, Arizona | `04021001200` | Pinal County | Coolidge city | 7.820% |
| Maricopa, Arizona | `04003000800` | Cochise County | Douglas city | 7.995% |
| Northern California | `06007003200` | Butte County | Palermo CDP | 0.062% |
| Northern California | `06115040301` | Yuba County | Linda CDP | 0.102% |
| Northern California | `06007003300` | Butte County | Palermo CDP | 0.811% |
| Northern California | `06033000501` | Lake County | Lucerne CDP | 1.339% |
| Northern California | `06115040500` | Yuba County | Olivehurst CDP | 1.435% |
| Northern California | `06007003100` | Butte County | Oroville East CDP | 2.065% |
| Northern California | `06089012301` | Shasta County | Redding city | 2.122% |
| Northern California | `06103000600` | Tehama County | Red Bluff city | 2.192% |
| Northern California | `06115040901` | Yuba County | Linda CDP | 2.287% |
| Northern California | `06103001000` | Tehama County | Corning city | 2.325% |
| South-Central Texas | `48201433505` | Harris County | Houston city | 0.000% |
| South-Central Texas | `48215020106` | Hidalgo County | Mission city | 0.000% |
| South-Central Texas | `48215021315` | Hidalgo County | Pharr city | 0.000% |
| South-Central Texas | `48215022011` | Hidalgo County | South Alamo CDP | 0.000% |
| South-Central Texas | `48215022206` | Hidalgo County | Midway North CDP | 0.000% |
| South-Central Texas | `48215022503` | Hidalgo County | Olivarez CDP | 0.000% |
| South-Central Texas | `48215023520` | Hidalgo County | San Carlos CDP | 0.000% |
| South-Central Texas | `48215023525` | Hidalgo County | — | 0.000% |
| South-Central Texas | `48215024406` | Hidalgo County | Olivarez CDP | 0.000% |
| South-Central Texas | `48479001822` | Webb County | Laredo city | 0.000% |

## Appendix J: Supporting discovery tables

#### Rio Grande border-county detail

Values are from `reports/discovery-border-metros.json`; Google attribution is regional, and provided-field Overture/USFS ratios are context, not Microsoft-target proxies.

| Border county | Tracts | Population | Mean OSM share (95% tract CI) | Other-source share* | Median Overture / provided USFS count† |
|---|---:|---:|---:|---:|---:|
| Hidalgo | 212 | 870,781 | 6.10% (4.25–8.19%) | 20.42% | 1.04 |
| Cameron | 121 | 421,017 | 13.27% (9.86–16.95%) | 28.97% | 1.39 |
| Webb | 69 | 267,114 | 6.16% (4.77–7.73%) | 40.80% | 1.56 |
| Starr | 17 | 65,920 | 1.91% (1.12–2.91%) | 47.99% | 1.75 |
| Maverick | 13 | 57,887 | 33.03% (21.53–44.33%) | 61.25% | 1.71 |
| Val Verde | 14 | 47,586 | 17.29% (6.78–33.24%) | 13.85% | 1.08 |
| Zapata | 6 | 13,889 | 27.63% (17.23–37.78%) | 32.26% | 1.42 |
| Jim Hogg | 2 | 4,838 | 3.35% (3.28–3.43%) | 0.00% | 0.98 |
| Duval | 3 | 9,831 | 3.71% (0.50–5.34%) | 0.00% | 1.02 |
| Dimmit | 3 | 8,615 | 3.51% (1.34–7.12%) | 3.78% | 0.63 |

*Weighted; Google attribution is regional. †Provided-field tract medians; small-county intervals are unstable.

#### Paradise/Magalia tract detail

The focal tract values are from `reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`; DINS counts and relative errors are from `reports/discovery-dins-mechanism.json#/focal_butte_tracts`.

| GEOID | Overture / reference | Reference − Overture | DINS destroyed | DINS major | Relative error |
|---|---:|---:|---:|---:|---:|
| `06007001800` | 0.620 | 666.4 | 3,314 | 3 | 0.799 |
| `06007002100` | 0.643 | 588.3 | 2,337 | 4 | 0.748 |
| `06007002000` | 0.662 | 579.5 | 2,870 | 4 | 0.798 |
| `06007001900` | 0.677 | 390.2 | 1,887 | 3 | 0.793 |
| `06007002300` | 0.720 | 668.9 | 2,386 | 2 | 0.720 |
| `06007002200` | 0.747 | 766.1 | 2,472 | 6 | 0.690 |

#### Out-of-region border transfer

This supporting transfer test uses Overture Maps Foundation buildings release 2026-09-23.1, the same release used by the lane cache. Dataset URL: `https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/release/2026-09-23.1/theme=buildings/type=building/`; retrieved 2026-10-29T20:36:09Z; Overture buildings ODbL 1.0 (Google Open Buildings CC BY 4.0/ODbL per Overture attribution). Downloaded by the STAC-pruned `overturemaps` CLI; the workspace manifest records exact bounding boxes, row counts and file hashes. The five counties are outside the four scored regions. Footprints are assigned by bbox centre to Census TIGER/Line 2024 county tracts solely for geography, not target reconstruction; these public-domain polygons are not the removed reference layer. Source classes use `scripts/estimate_gaps.py::classify_building_sources`. Shares are footprint-weighted; tract median OSM is unweighted; “tracts <5%” is a strict tract OSM-share threshold. Google-only means the single source name contains `google`.

| County | Role | Footprints | OSM-touched | Microsoft-only | Google-only | Esri-touched | Tract-median OSM | Tracts <5% OSM / all |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **El Paso TX** | Border, unscored | 392,897 | 0.093 | 0.475 | **0.330** | 0.000 | 0.043 | 101 / 188 |
| **Imperial CA** | Border, unscored | 80,116 | 0.200 | 0.437 | **0.292** | 0.000 | 0.073 | 17 / 40 |
| **Doña Ana NM** | Border, unscored | 118,361 | 0.179 | 0.419 | **0.108** | 0.261 | 0.053 | 29 / 61 |
| Ector TX (Odessa) | Non-border, Hispanic-majority control | 78,050 | 0.067 | 0.456 | **0.000** | 0.477 | 0.036 | 18 / 33 |
| Bernalillo NM (Albuquerque) | Non-border control | 282,316 | 0.809 | 0.191 | **0.000** | 0.000 | 0.942 | 2 / 176 |

The lane `other` class is a mixed-provenance bin, not one provider: the border `other` count (254,346) is within 526 of Google Open Buildings memberships (254,872), while non-border South-Central Texas `other` (977,049) is within 526 of USGS lidar memberships (976,523). The Bexar provided-cache `other` share is 0.336. These count reconciliations support, but do not prove, the provider attribution; do not read `other` as Google statewide. Original source counts are in `reports/discovery-border-metros.json`; Bexar cache inputs and hash are recorded in `reports/discovery-border-transfer.json`.

Named impact context: Hidalgo tracts `48215021906`, `48215023525`, and `48215024404` have zero OSM-touched buildings and 1,727, 1,765, and 2,212 footprints; Cameron tract `48061012002` has 3,084 footprints and 0.03% OSM. Annual WBGT>30°C averages 53.5 days across 218 covered border tracts vs 7.3 across 3,654 covered non-border tracts; dispatch outcomes were not measured.

The source manifest at `$COMPETITION_WORKSPACE/external-discovery-2026-10-29-fable/MANIFEST.json` records the Overture public URL, CLI retrieval UTC, ODbL/attribution terms, bbox row counts and SHA-256 hashes. Reproduce the analysis with `uv run --python 3.12 --no-project --isolated --with-requirements requirements.txt python scripts/discovery/transfer_outside_regions.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-border-transfer.json` (report SHA-256 `64037e7360dab85eae3f748ec5d0ec14155999587302ae12e83bd3f9a893b795`). External Census TIGER/Line 2024 tract files for CA, NM and TX were retrieved 2026-10-25 and are public-domain geography, not target reference data: https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_06_tract.zip, https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_35_tract.zip, https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_48_tract.zip. Archive SHA-256 values are in the report. The supporting test is `python -m unittest discover -s tests -p 'test_discovery_transfer_outside_regions.py' -v`. This discovery used public source provenance only; no candidate predictions or score values.

## Appendix K: San Antonio-area SVI gradient

### Evidence and method

This descriptive check uses only official sample GEOIDs, provided Overture building OSM-source share, and provided `svi_overall`, `svi_covered`, `pct_urban`, and population. Quartiles are computed within each region; the Bexar and urban-Texas subsets retain South-Central Texas cutpoints. Table values are equal-weight tract means. Q4−Q1 intervals are 5,000 independent tract-bootstrap resamples (seed 20261024). The measure is source attribution, not building accuracy or completeness.

| Scope | Q1 | Q2 | Q3 | Q4 | Q4−Q1, pp (95% CI) | n (Q1/Q4) |
|---|---:|---:|---:|---:|---:|---:|
| Eastern Oklahoma | 26.8% | 18.6% | 18.2% | 19.1% | −7.7 [−11.7, −3.8] | 295/296 |
| Arizona | 84.8% | 86.5% | 80.8% | 81.9% | −2.9 [−6.4, +0.6] | 391/391 |
| Northern California | 55.6% | 55.6% | 50.6% | 47.8% | −7.8 [−15.3, +0.1] | 148/148 |
| South-Central Texas | 69.0% | 57.0% | 53.1% | 50.3% | −18.8 [−21.4, −16.0] | 1,490/1,490 |
| Texas, urban (`pct_urban >= 0.5`) | 73.5% | 67.0% | 62.1% | 53.0% | −20.5 [−23.2, −17.8] | 1,327/1,362 |
| Bexar County, Texas | 56.1% | 65.9% | 48.6% | 36.5% | −19.6 [−31.4, −7.9] | 55/139 |

Bexar County's 372 tracts show a higher-vulnerability tail: Q4 has 139 tracts and 653,292 residents; 33 Q4 tracts (142,547 residents) have <5% OSM-attributed share, versus 9 of 55 Q1 tracts. The 12 lowest-share Q4 GEOIDs, with shares from 0.30% to 1.70%, are `48029140600`, `48029160400`, `48029121506`, `48029160600`, `48029170700`, `48029140500`, `48029130300`, `48029151302`, `48029160502`, `48029171000`, `48029171301`, and `48029150700`. The Bexar SVI slope is −0.360 (HC1 SE 0.072); one county cannot support county-clustered uncertainty. The unadjusted Texas slope is −0.247 (county-clustered SE 0.103; 185 counties).

The Texas contrast is comparable in magnitude to the rural OSM-share contrast in Oklahoma, while Arizona's SVI interval crosses zero. This is a San Antonio-area pattern, not a cross-region rule. Source attribution does not establish map completeness, errors, causation, or effects on service delivery. No dispatch, evacuation, or relief outcomes were observed; use named tracts only as partner-led review priorities.

Reproduce: `uv run --python 3.12 --no-project --with-requirements requirements.txt python scripts/audit_san_antonio_svi_gradient.py --workspace "$COMPETITION_WORKSPACE" --out reports/san-antonio-svi-gradient.json`. The JSON report records exact estimates, bootstrap seeds, input SHA-256 values, scopes, and limitations.
