# Bias Bounty Mapping Equity Challenge — discovery and methodology

## Submission context

- Protocol A: `V2_model_cgs`; leaderboard submission `hvLtPVmr`.
- Protocol B: `B_from_A_cgs`; leaderboard submission `eAaWXWxM`.
We request consideration for both Best Bias Discovery ($1,000) and Best Documentation ($500).

## Contents

1. Best Bias Discovery — findings.
2. Methodology and score interpretation.
3. Appendix A: [detailed methodology](methodology.md).
4. Appendix B: [edge cases](edge-cases.md).
5. Appendix C: [supporting discovery screens](appendices/screens-that-did-not-produce-a-finding.md).
6. [Supporting diagnostic archive](appendix-diagnostics.md).
7. Appendices D–H: upload records, official criteria, external-layer compliance, reproducibility, and provenance.

## Best Bias Discovery entry

### Finding 1 — Rio Grande border metros: Google-sourced footprints mask a community-source gap

#### Impact and novelty

These ten South Texas counties include 1.77 million residents and colonia-serving communities. Prioritise partner-led verification; dispatch outcomes and OSM completeness were not measured.

#### Evidence and mechanism

Across 460 border tracts, mean OSM-touched share is 9.18%, versus 61.48% in 5,541 others (68.66% in 4,600 urban others); mean tract-level Microsoft-only share is 67.48%. “Other” accounts for 254,346/886,361 footprints (28.70%); 290 tracts (1,183,693 people) are under 5% OSM. Difference: −52.30 points (county-cluster 95% CI [−61.24, −36.99]). With urbanity, log-density, overall SVI, and CDC SVI `EP_HISP` controls: −0.446 (SE 0.070; n=5,958, 185 counties), descriptive, not causal.

Border “other” footprints (254,346) nearly match regional Google Open Buildings (254,872); non-border “other” (977,049) nearly matches USGS lidar (976,523), a ±526 reconciliation supporting—but not proving—Google spill-over. Webb/Starr/Maverick median provided Overture/USFS ratios are 1.56/1.75/1.71, not a Microsoft component or score proxy.

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

*Weighted; Google attribution is regional. †Provided-field tract medians, not the Microsoft target; small-county intervals are unstable.

Hidalgo `48215021906`, `48215023525`, and `48215024404` have zero OSM share and 1,727, 1,765, and 2,212 footprints; Cameron `48061012002` has 3,084 and 0.03% OSM. Heat averages 53.5 vs 7.3 WBGT>30°C days/year (218 vs 3,654 covered tracts). Prioritise local verification; dispatch effects are unmeasured.

Board 34744 (“Colonias: the coverage gap the scorecard cannot see”) withdrew reconstructed-gap claims and now concerns road dispatchability; 35071 (“The Texas Colonias Infrastructure Vacuum”) covers rural/tribal-SVI composition; 34972 (“Maps nobody has touched since 2011”) stale road edits; 35333 (“Phantom roads”) TxDOT-checked old unnamed roads. Ours quantifies Google building spill-over against the Microsoft comparator.

Report/input hashes: `reports/discovery-border-metros.json` (`27779986d787594abdadf741c983db13dedf25fb476cc4ff2ae096a1a4260244`); source report `reports/source-provenance.json` (`e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565`). SVI terms are in Appendix F. Rebuild: `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 python scripts/discovery/border_metros.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-border-metros.json`.

### Finding 2 — Rural Overture buildings are less often OSM-sourced

#### Impact and novelty

Rural tracts may have fewer OSM-touched buildings across all four regions, supporting local attribute verification. This is a plausible audit risk—not measured service failure or a marginal rurality/SVI scorecard restatement.

#### Evidence

Rural OSM-touched shares are 16.83–38.68 points lower across Maricopa, Northern California, Eastern Oklahoma, and South-Central Texas; all tract-bootstrap intervals exclude zero. SVI-quartile contrasts are smaller and less consistent (`reports/source-provenance.json#/statistics`, `#/uncertainty_and_limitations`).

#### What this does and does not show

Source tags indicate provenance, not accuracy, completeness, or age; rurality is not causal and SVI may confound. The analysis uses provided Overture sources/geometry only, with no external data or candidate changes (`reports/source-provenance.json#/data_and_restrictions`).

#### Reproducibility

Rebuild: `python scripts/discovery_source_provenance.py --workspace "$COMPETITION_WORKSPACE" --out reports/source-provenance.json --figure reports/source-provenance.png`.

### Finding 3 — Paradise/Magalia building-count discrepancy

#### Impact and evidence

In six Paradise/Magalia-area Butte County tracts affected by the 2018 Camp Fire, the provided Wildfire Risk to Communities counts exceed Overture counts (Overture/reference ratios 0.620–0.747). This geographic and temporal discrepancy is observed; fire damage is not established as its cause. Treating the larger count as current could misdirect field verification, a plausible risk rather than an observed dispatch failure. The six tracts have provided MTBS last-burn year 2018; public CAL FIRE DINS points are used only as a mechanism check (catalogue and incident layer retrieved 2026-10-29; catalogue CC BY, version unspecified).

| GEOID | Overture / reference | Reference − Overture | DINS destroyed | DINS major | Relative error |
|---|---:|---:|---:|---:|---:|
| `06007001800` | 0.620 | 666.4 | 3,314 | 3 | 0.799 |
| `06007002100` | 0.643 | 588.3 | 2,337 | 4 | 0.748 |
| `06007002000` | 0.662 | 579.5 | 2,870 | 4 | 0.798 |
| `06007001900` | 0.677 | 390.2 | 1,887 | 3 | 0.793 |
| `06007002300` | 0.720 | 668.9 | 2,386 | 2 | 0.720 |
| `06007002200` | 0.747 | 766.1 | 2,472 | 6 | 0.690 |

Inputs, definitions, and values are in `reports/discovery-postfire.json#/focal_butte_2018_burn_geoids` and `reports/discovery-dins-mechanism.json#/focal_butte_tracts`. The tract-bootstrap difference is +9.55 points (95% CI [+0.55, +20.89]); the county-cluster interval [−1.65, +16.67] includes zero, so magnitude remains exploratory (`reports/discovery-postfire-county-bootstrap.json#/county_cluster_bootstrap/cluster_95_percentile_interval`). The map marks the six tracts:

![NorCal/Butte tract map: fill is provided MTBS burned percentage; the six focal tracts are marked.](../../reports/discovery-postfire-map.png)

**Negative mechanism check.** DINS does not support the proposed fire-damage/rebuilding mechanism: 0/6 tracts met the ≤0.30 relative-error rule (which required at least 4/6), and Spearman was 0.4626068 across 28 last-burn-2018 tracts, below 0.60. Among 591 NorCal tracts, 576 with positive Overture counts have no Camp Fire DINS record (1,139,778 buildings); missing inspection records cannot establish absence or rebuilding. MTBS percentages are cumulative (64.4–153.8%); the count-field vintage is unverified. Details are in `reports/discovery-dins-mechanism.json`; this Camp Fire check uses DINS only, while Finding 1's SVI control is documented in Appendix F.

Reproduction: `scripts/discovery_postfire_stale_reference.py`, `scripts/audit_discovery_postfire_county_bootstrap.py`, and `scripts/discovery_dins_mechanism.py`; input paths, filters and hashes are recorded in the reports and workspace manifest.

### Finding 4 — The tribal/non-tribal pooled comparison is region-confounded

#### Impact and novelty

A pooled tribal/non-tribal contrast in Eastern Oklahoma and South-Central Texas can reflect different regional mixes, not a general tribal pattern. This is a composition caveat, not a disparity or causal finding.

#### Evidence

Of 858 `tribal_any` tracts, 805 (93.82%) are in Eastern Oklahoma; 6,002 of 8,521 non-tribal tracts (70.44%) are in South-Central Texas (`reports/discovery-strata-confound.json#/stratum_composition`).

#### What this does and does not show

These are composition counts, not equity or target-score evidence. `tribal_any` is an overlap indicator, not legal status; comparisons should be region-stratified (`reports/discovery-strata-confound.json#/regional_counts_and_prevalence`, `#/missing`).

#### Reproducibility

Rebuild: `python scripts/discovery_strata_confound.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-strata-confound.json`.

## Methodology (Best Documentation entry)

### Scope and safeguards

The organisers provide no labels. Four official samples define 9,379 scored GEOIDs (`reports/sample-contract-audit.json#/authoritative_unique_geoids`). `V2_model` uses challenge features only; placeholders, public scores, hidden targets and removed references do not enter predictions. The protocol comparison identifies `V2_model_cgs` and its pre-registered 50% within-region-shrink companion `B_from_A_cgs` (`reports/final-two.json#/final_candidates`, `#/corrected_cgs_selection`). Neither candidate claims hidden-score accuracy.

### Components and definedness

The composite averages defined transport/building/POI terms; undefined terms are omitted, not zero-filled. Transport uses the frozen named-road gap and provided route denominator; building/POI use fixed provided-Overture specifications and priors. ACS housing units are not a building denominator. Equal weights are transparent, not accuracy-fitted. Appendix A links to detailed formulas, provenance, and reproduction notes; Appendix B covers zero/missingness cases.

### Provenance and reproduction

Scored predictions use organiser-provided data only. Public CAL FIRE Camp Fire DINS is discovery-only, joined to provided NorCal geometry and never used in scored candidates (`reports/discovery-dins-mechanism.json#/external_source`, `#/scored_candidates`). Its catalogue states CC BY, version unspecified, and retrieval date; the manifest preserves URL, query, licence, limits, and hash. No removed reference layer was accessed.

Rebuild components with the pinned workflow and discovery reports using their documented commands. Appendix A and the reports record scripts, fixed parameters, hashes, and outputs. These checks do not validate against labels: no labelled holdout exists, and public leaderboard scores do not establish hidden-target accuracy.

### Public-score transparency (descriptive only)

Public-split RMSEs: all-zero C0 (`uRRw9KpB`) `0.1072`; corrected-order `V2_model_cgs` (`hvLtPVmr`) `0.1110`; its pre-registered 50% within-region-mean shrink, `B_from_A_cgs` (`eAaWXWxM`), `0.0975`. This is consistent with regional means doing more work; V2's within-region variation is not shown to carry target signal. These public results are not private-split evidence and do not establish hidden-target accuracy. They are reported for transparency only; candidate predictions are based on the frozen provided-feature methodology. Sources: `reports/final-two.json`; shared `data/ledger/submissions.jsonl` (mirror `reports/submissions.jsonl`); `reports/lb-scorecard-snapshot.json`; `docs/final-two-protocol.md`.

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

Four public external layers were used only for the discovery screen described in the linked supporting analysis; none enters scored predictions. Retrieval dates and licence/terms verification status are recorded below. Where terms were not verified, no reuse or public-domain claim is made.

| Layer | Source URL | Retrieved | Licence / terms status |
|---|---|---|---|
| CDC/ATSDR SVI 2022 | https://svi.cdc.gov/Documents/Data/2022/csv/states/SVI_2022_US.csv | 2026-10-27 | Dataset-specific terms were not stated on the fetched ATSDR page. Treated as a U.S. federal government work under 17 U.S.C. Section 105, subject to third-party exceptions; this is a statutory assessment, not a quoted dataset-specific licence. |
| USDA ERS RUCA 2020 | https://www.ers.usda.gov/media/5443/2020-rural-urban-commuting-area-codes-census-tracts.csv | 2026-10-27 | No terms/copyright statement found on fetched ERS page; unverified; ERS requests citation. |
| Census Planning Database 2026 | https://www2.census.gov/adrm/PDB/2026/pdb2026tr.csv | 2026-10-27 | Not verified; Census terms/copyright pages were not read. |
| Census TIGER/Line 2020 AIANNH polygons | https://www2.census.gov/geo/tiger/TIGER2020/AIANNH/tl_2020_us_aiannh.zip | 2026-10-27 | Not verified; Census terms/copyright pages were not read. |

Source-file hashes, download timestamps, and source-page licence checks are recorded in `$COMPETITION_WORKSPACE/external-discovery-2026-10-27/MANIFEST.json`; the data files are not redistributed in this packet.

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
| https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/POSTFIRE_MASTER_DATA_SHARE/FeatureServer/0 | CAL FIRE DINS public point-feature service; filtered to Camp incident and non-address fields | 2026-10-29 | public external discovery data endpoint; not used in scored candidate construction |
| https://svi.cdc.gov/Documents/Data/2022/csv/states/SVI_2022_US.csv | CDC/ATSDR SVI 2022 | 2026-10-27 | discovery-only external context; used for EP_HISP control in the border-metro descriptive regression; never used in scored predictions |
| https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title17-section105&num=0&edition=prelim | 17 U.S.C. Section 105, statutory U.S. federal-work public-domain basis (subject to third-party exceptions) | 2026-10-27 | discovery-only external context; used for EP_HISP control in the border-metro descriptive regression; never used in scored predictions |
| https://www.census.gov/library/reference/code-lists/mt-feature-class-codes.html | Citation/documentation: U.S. Census Bureau, MAF/TIGER Feature Class Code Definitions. URL check: official Census page re | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://www.census.gov/programs-surveys/cbp/technical-documentation/methodology.html | Citation/documentation: U.S. Census Bureau, County Business Patterns Methodology. URL check: official Census page resolv | 2026-10-24 | prior art and methodology know-how; not a dataset and never joined to scored predictions |
| https://www.ers.usda.gov/media/5443/2020-rural-urban-commuting-area-codes-census-tracts.csv | USDA ERS RUCA 2020 | 2026-10-27 | discovery-only external context; never used in scored predictions |
| https://www2.census.gov/adrm/PDB/2026/2026_PDB_Documentation.pdf | Census Planning Database 2026 documentation | 2026-10-27 | discovery-only external context; never used in scored predictions |
| https://www2.census.gov/adrm/PDB/2026/pdb2026tr.csv | Census Planning Database 2026 | 2026-10-27 | discovery-only external context; never used in scored predictions |
| https://www2.census.gov/geo/tiger/TIGER2020/AIANNH/tl_2020_us_aiannh.zip | Census TIGER/Line 2020 AIANNH polygons | 2026-10-27 | discovery-only external context; never used in scored predictions |

Provided-data-only check for the scored predictions (`reports/v2-input-provenance.json`): field-lineage fraction 1.0, 0 prohibited source dependencies, 0 unclassified inputs, external data added to scored predictions: False.

## Appendix G: Reproducibility audit

A machine-checked claim-to-evidence audit backs every number in this writeup.

- `claims_total`: 72
- `claims_fully_traceable`: 72
- `fraction_quantitative_claims_traceable`: 1.0
- `numeric_tokens_total`: 862
- `numeric_tokens_covered`: 862
- `fraction_numeric_tokens_covered`: 1.0
- `distinct_verify_commands`: 72
- `distinct_reproduce_commands`: 19
- `derived_aggregates_verified`: 27
- `claims_asserting_accuracy`: 1

Current audit status: 72 of 72 quantitative claims are fully traceable (1.0); 862 of 862 numeric tokens are covered (1.0). There are 0 unresolved claim-evidence items. The full claim audit does not pass overall acceptance because three registered report-product differences remain outside accepted disclosure paths (`reports/writeup-claim-evidence-audit.json#/commands/undisclosed_product_changes`). This is a reproduction limitation, not an accuracy claim. The final-two result is separately traced to `reports/final-two.json` and machine-compared by the packet verifier.

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
