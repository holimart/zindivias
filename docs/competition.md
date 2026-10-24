# Bias Bounty Mapping Equity Challenge — official rules and data contract

- **Platform:** Zindi; [competition page](https://zindi.africa/competitions/bias-bounty-mapping-equity-challenge)
- **Deadline:** 2026-10-31 23:59 UTC
- **Eligibility/team:** open to all; up to four people per team.
- **Prizes:** leaderboard 1st/2nd/3rd: $4,500/$2,500/$1,500; Best Bias Discovery: $1,000; Best Documentation: $500. The two special prizes are judged from the methodology writeup, not a separate leaderboard.
- **Rules source:** `$COMPETITION_WORKSPACE/RULES.md`, verbatim official page text captured 2026-10-24. The official per-region samples define the 9,379 scored IDs and supersede the Data-page template as a scoring-scope definition. A later steward directive uses that 9,794-row template only for platform processing; see the documented full-ID protocol below. Re-check the live official rules before any future submission.

## Objective and scoring

There are deliberately **no training labels**. Estimate each tract's organiser-computed coverage-gap score from the data currently provided. The score is in `[0,1]` (0 = full coverage; 1 = no coverage). The leaderboard metric is RMSE against hidden organiser scores. The descriptions below explain organiser scoring for context only; removed reference layers or equivalent copies must not be used to reconstruct or approximate the hidden targets.

The score is the mean of the defined transport, building, and POI components for a tract. A component with no reference to compare against is undefined and is omitted from that mean; the divisor is not always three.

- **Transport:** named Overture road classes `motorway`, `trunk`, `primary`, and `secondary` compared with Census TIGER/Line MTFCC `S1100`/`S1200` roads. Primary/secondary boundaries differ by region, so component ratios are not directly comparable across regions.
- **Buildings:** Overture building counts compared with Microsoft US Building Footprints. ACS housing-unit counts are a sanity check only and must not be used as a denominator floor or in the ratio; units and structures differ.
- **POI:** a mean of two halves: (1) facilities, the mean of fire-station, EMS-station, and school category gaps against USGS National Map counts; (2) all Overture places against Census County Business Patterns establishment counts. Hospitals are excluded. If no facility or establishment reference exists, the corresponding POI contribution is undefined rather than zero-filled.

At least one reference component is undefined in 21% of Eastern Oklahoma tracts, 37% of Northern California, 28% of South-Central Texas, and 55% of Maricopa. The challenge data includes Overture and strata data; some reference layers have been removed specifically to prevent target reconstruction.

## Authoritative sample IDs and columns

The official scored list is the four public bucket files `reference/<region>/<region>-sample-submission.csv`. They were fetched into `$COMPETITION_WORKSPACE/samples/`; checksums, byte sizes, source URLs, and counts are in `reports/sample-contract-audit.json`.

| Region | Authoritative rows | Bytes | SHA-256 |
|---|---:|---:|---|
| Maricopa AZ | 1,593 | 44,664 | `1640b801d56ab308e68c65583b88cfb9e1555b05a6d0b922eaba22ac09960a31` |
| Northern CA | 591 | 16,608 | `f38017b5284667b80b451685fd146388c108e1b30308301289c7a96ecfe60902` |
| Eastern OK | 1,192 | 33,436 | `56cb40fba4008d646bc63c62c91e211871459224b3de8b3a886ff813803fd0d4` |
| South-Central TX | 6,003 | 168,144 | `afa59c0eb30c40be25ce82098f144447591b79cb9692e669b2f2fe7b7b1ee58e` |
| **Total** | **9,379** | **262,852** | — |

All four authoritative files use the exact header `GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score`. Read `GEOID` as text to retain leading zeros. The official page requires `GEOID` and `coverage_gap_score`, and says component scores may be included; the frozen local candidates use the five-column authoritative sample header. Maricopa includes 1,592 Arizona tracts plus New Mexico tract `35023970000`.

The workspace-level `SampleSubmission.csv` has 9,794 unique rows, 17 columns, and 415 additional `eastern-wa` GEOIDs. The four official regional samples still define the 9,379 scored tracts; the eastern-WA rows are outside those regions and no eastern-WA feature inputs are present. The steward's 2026-10-24 `bias-bounty-full-id-candidates` directive reports that Zindi processing requires the full Data-page template. For that platform-processing path, C0-C3 are therefore extended to the template order while preserving all 9,379 official-region values byte-for-byte. Only `GEOID` and `region` are read from the legacy template; score placeholders are ignored. The audit preserves earlier C0-C3 fill values as historical artifact metadata. Future builds use the `--wa-fill` option in `scripts/build_full_id_candidates.py` (default value `0.0`) for every score field on the 415 eastern-WA template-only rows; this is a deterministic formatting value, not a target estimate. This operational format does not redefine the official scored list, authorize target reconstruction, or permit external/reference data.

Reproduce the contract audit from the competition repo:

```sh
python scripts/audit_sample_contract.py --workspace "$COMPETITION_WORKSPACE" \
  --out reports/sample-contract-audit.json \
  --reconciliation reports/sample-id-reconciliation.csv \
  --legacy-only-ids reports/legacy-only-sample-ids.csv
```

It reads only GEOIDs and headers from the samples; dummy sample score values are not used as labels.

## Submission and leaderboard rules

- The official page specifies RMSE, a public leaderboard on approximately 30% of test, and a private leaderboard on the remaining 70%, revealed at challenge close. Select two submissions for private scoring before the end; otherwise the two best public submissions are used.
- Official maximum: 10 submissions per day and 300 total; a processing failure does not count. The stricter operator policy is at most 2 submissions per UTC day, only for finished model candidates, enforced by `$ORCHESTRATOR_ROOT/scripts/zindi_submit.py`. See [`submission-policy.md`](submission-policy.md); no leaderboard information probes are allowed.
- Use the authoritative per-region lists for every local candidate check. Fill every included score field; blanks are rejected. The score field is required and component score columns are optional under the official rules.
- Every submission automatically receives an equity Bias Scorecard. It does not affect the leaderboard rank; it reports five equity measures across urban/rural, SVI, CVI, tribal, and hazard-exposure strata.
- `scripts/build_full_id_candidates.py` and `reports/full-id-candidate-audit.json` reproduce and validate the steward-directed 9,794-row platform template, including exact IDs/order, finite bounded fields, candidate hashes, official-sample lineage, and eastern-WA fill policy. The local `scripts/submit_zindi.py` remains a dry-run-only validator for the official 9,379-row contract and refuses live submission. All live uploads must go only through `$ORCHESTRATOR_ROOT/scripts/zindi_submit.py`; the full-ID candidate bundle is not an accuracy claim, and the official scored-region scope remains 9,379 IDs.

## Data use, tooling, and prizes

The **scored submission** may use only datasets provided in the challenge package. Additional public datasets are permitted only for the Best Bias Discovery writeup; document public accessibility, open licence, URL, retrieval date, and reproducible use. Do not use removed TIGER, Microsoft building, USGS facility, or CBP reference layers (or equivalent copies) to recreate or approximate hidden scores. The operator has cancelled all information-probe uploads; only completed model-candidate evaluation is permitted. No new candidate may use LB-derived calibration constants. Use open-source languages and tools; automated ML is not permitted. Data is shared under CC-BY-SA 4.0.

For Best Bias Discovery, identify a pattern outside the fixed scorecard grid, name affected tracts/areas, quantify the evidence, explain concrete impacts on emergency dispatch/evacuation/disaster relief, and make the analysis reproducible. Best Documentation rewards clear reproducible methodology, including data sources, component calculations, zero-population/water/no-Overture edge cases, and alternative weighting rationale. No label-based accuracy claims are possible without labels or a valid score.
