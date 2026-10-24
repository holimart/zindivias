# Building component decision for estimator v2

**Status:** proposed operator decision; fixed model rule implemented as a standalone v2 component, not yet assembled into or written to a scored candidate. The component level is set before evaluation. No leaderboard calibration or information probe was used.

## Official rule text

> Building footprint gap: compare Overture building counts per tract against Microsoft US Building Footprints. ACS housing unit counts are a sanity check only and are not part of the ratio: ACS counts housing units where Microsoft counts structures, so using ACS as a floor on the denominator manufactures an urban coverage gap out of dwelling density.

— `$COMPETITION_WORKSPACE/RULES.md`

## Decision requested

Please approve this fixed v2 rule: for an official tract with a positive provided `overture_buildings` count, set `building_gap_v2 = 0.01` and `building_defined = 1`; for a zero count, emit numeric placeholder `0.0` and `building_defined = 0`, so it is excluded from defined-component means. The primitive is implemented in `scripts/estimator_v2_building.py`. It consumes no USFS, CarbonPlan, ACS housing-unit, or other reference-derived value. Keep the fixed value at `0.01` unless a new **model-derived, pre-scoring** rationale is approved; do not calibrate it from leaderboard results.

The positive-Overture-count definedness rule is a proxy, not proof that the hidden reference denominator exists. A true Microsoft reference footprint omitted entirely by Overture could therefore be missed; this is disclosed rather than repaired with a prohibited per-tract derivative. The building component has no nonzero expected-count floor. In particular, ACS housing units are not used as a structures denominator or floor.

## Rationale and measurements

The mechanism supports a near-zero prior: the provided Overture `sources` metadata attributes many building records to Microsoft, while aggregate comparisons with the provided Microsoft-footprint derivatives are close to one in all four regions. Those diagnostics establish a plausible level, not a hidden-label measurement, exact geometric identity, or vintage match. To avoid the grey-zone risk of equivalent copies, `usfs_BuildingCount_sum` and `carbonplan_buildings` are used only to report region-level distributions below. Neither value nor any ratio, source share, or derived flag from them enters `scripts/estimator_v2_building.py` or a scored prediction.

The source table is reproducible with `scripts/audit_building_source_mix.py` and is saved as `reports/building-source-mix.json`. It samples 10% of records within **every** regional-object row group, with a deterministic per-region/per-row-group seed, reading only the provided Overture building `sources` column. It does not spatially assign sampled records to official tracts, so the values describe each regional Overture object, not an exact tract-matched population. Provider membership may overlap; exclusive source classes form a partition.

| Region | Sampled buildings | Microsoft any | OSM any | Esri any | Microsoft only | Microsoft + OSM | OSM only | Esri only | Other/mixed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Maricopa AZ | 290,849 | 44.78% | 79.60% | 4.76% | 13.12% | 29.20% | 11.89% | 3.31% | 42.49% |
| Northern CA | 116,483 | 89.47% | 46.55% | 2.57% | 50.88% | 37.36% | 9.19% | 1.35% | 1.22% |
| Eastern OK | 255,182 | 87.88% | 16.71% | 14.01% | 69.28% | 10.88% | 5.83% | 6.29% | 7.72% |
| South-Central TX | 1,146,495 | 71.95% | 51.00% | 2.41% | 43.33% | 26.91% | 16.85% | 1.83% | 11.08% |

`Microsoft any`, `OSM any`, and `Esri any` are overlapping membership shares. The remaining five columns are mutually exclusive classes. The row-sample estimates are descriptive; see the JSON for selected row groups, seeds, exact sample counts, and source labels. Source mentions do not establish completeness or prove that a record matches the hidden reference.

As aggregate level checks only, `scripts/audit_building_v2.py` compared the Overture count with the provided strata columns. These ratios are not scores or candidate features. Q10 / median / Q90 are reported, with the share of positive-denominator pairs where Overture/USFS is below 0.8:

| Region | Overture / USFS q10 / median / q90 | Share < 0.8 | Overture / CarbonPlan q10 / median / q90 | Pairs |
|---|---:|---:|---:|---:|
| Maricopa AZ | 0.944 / 1.008 / 1.208 | 1.51% | 0.991 / 0.999 / 1.007 | 1,593 |
| Northern CA | 0.932 / 1.020 / 1.160 | 2.71% | 0.987 / 0.997 / 1.009 | 591 |
| Eastern OK | 0.930 / 1.024 / 1.424 | 1.26% | 0.989 / 0.998 / 1.002 | 1,192 |
| South-Central TX | 0.907 / 1.022 / 1.340 | 3.40% | 0.987 / 0.998 / 1.005 | 6,001 |

The CarbonPlan q10–q90 ranges round to approximately 0.99–1.01, but this does **not** mean every tract is within one percent: the observed share within ±1% of one is 83.6%, 74.8%, 84.8%, and 78.4%, respectively. The provided USFS and CarbonPlan columns are footprint derivatives, so the close match is a level diagnostic only and must not be turned into per-tract model inputs.

### v0 to v2 component moments

The reproducible results are in `reports/building-v2-audit.json`. v0 is `reports/gap-estimates.csv`; its building term was defined on all 9,379 official rows. V2 uses the fixed rule above and excludes the two South-Central Texas zero-Overture-count rows from the defined-only mean.

| Region | v0 mean (defined) | v0 zero share (defined) | v2 defined n / n | v2 mean (defined) | v2 numeric mean including undefined zero placeholders |
|---|---:|---:|---:|---:|---:|
| Maricopa AZ | 0.04905 | 79.28% | 1,593 / 1,593 | 0.01000 | 0.01000 |
| Northern CA | 0.04657 | 78.68% | 591 / 591 | 0.01000 | 0.01000 |
| Eastern OK | 0.01922 | 93.29% | 1,192 / 1,192 | 0.01000 | 0.01000 |
| South-Central TX | 0.09167 | 74.55% | 6,001 / 6,003 | 0.01000 | 0.01000 |
| **All** | **0.07238** | **77.99%** | **9,377 / 9,379** | **0.01000** | **0.01000** |

The `0.01` value is a conservative residual allowance for source-vintage, attribution, and geometry mismatch despite close aggregate levels; it is not fitted to the v0 mean or hidden targets. The v0-to-v2 contrast is a model-specification comparison, not evidence of v2 accuracy.

## Governance and reproduction

- Official scored scope is the four challenge sample files (9,379 GEOIDs). The row-group source sample and aggregate strata diagnostics never populate a scored CSV.
- `usfs_BuildingCount_sum` and `carbonplan_buildings` are provided strata columns, but because they are Microsoft-footprint derivatives they remain outside scored per-tract predictions unless the operator explicitly approves otherwise in writing.
- No removed reference files, external datasets, sample-score values, labels, or leaderboard feedback were used. No candidate, manifest, or submission log was modified.
- No ACS housing-unit count is used as a denominator floor.
- Validation: `python scripts/audit_building_source_mix.py --out reports/building-source-mix.json`; `python scripts/audit_building_v2.py --workspace "$COMPETITION_WORKSPACE" --out reports/building-v2-audit.json`; `pytest -q`.

**Operator sign-off requested:** approve (or reject) the fixed `0.01` prior and the Overture-count-positive definedness proxy for integration into a future v2 candidate. The block is not a scored artifact; no upload is proposed by this decision memo.
