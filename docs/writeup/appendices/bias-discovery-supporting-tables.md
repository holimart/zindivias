### Rio Grande border-county detail

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

### Paradise/Magalia tract detail

The focal tract values are from `reports/discovery-postfire.json#/focal_butte_2018_burn_geoids`; DINS counts and relative errors are from `reports/discovery-dins-mechanism.json#/focal_butte_tracts`.

| GEOID | Overture / reference | Reference − Overture | DINS destroyed | DINS major | Relative error |
|---|---:|---:|---:|---:|---:|
| `06007001800` | 0.620 | 666.4 | 3,314 | 3 | 0.799 |
| `06007002100` | 0.643 | 588.3 | 2,337 | 4 | 0.748 |
| `06007002000` | 0.662 | 579.5 | 2,870 | 4 | 0.798 |
| `06007001900` | 0.677 | 390.2 | 1,887 | 3 | 0.793 |
| `06007002300` | 0.720 | 668.9 | 2,386 | 2 | 0.720 |
| `06007002200` | 0.747 | 766.1 | 2,472 | 6 | 0.690 |

### Out-of-region border transfer

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
