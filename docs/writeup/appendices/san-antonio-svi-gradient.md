## Evidence and method

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
