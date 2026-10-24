# Edge-case register for the coverage-gap methodology

This table makes the handling explicit for low-information and structurally unusual tracts. Counts are aggregate diagnostics over the 9,379 official sample GEOIDs, not scores, labels, or evidence of predictive accuracy. The 415 eastern-WA rows exist only in the larger platform template and are excluded from these statistics and from the official scored-domain definition.

## Observed edge cases

| Region | Official n | Population 0 | `ALAND` 0 | Water proxy >50% | No named roads | No route-network km | Overture buildings 0 | Overture places 0 | No named roads/buildings/places | Missing `pct_urban` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Maricopa AZ | 1,593 | 11 | 0 | 0 | 33 | 901 | 0 | 0 | 0 | 11 |
| Northern CA | 591 | 0 | 0 | 2 | 7 | 182 | 0 | 0 | 0 | 0 |
| Eastern OK | 1,192 | 1 | 0 | 1 | 28 | 286 | 0 | 0 | 0 | 1 |
| South-Central TX | 6,003 | 14 | 2 | 40 | 64 | 1,275 | 2 | 2 | 2 | 14 |
| **Total** | **9,379** | **26** | **2** | **43** | **132** | **2,644** | **2** | **2** | **2** | **26** |

Water is an approximation, not an independently observed water mask: for the provided ACS tract geometry, project WKB geometry from EPSG:4326 to EPSG:5070, then calculate `max(0, min(1, 1 - ALAND / projected_geometry_area))`. A tract is listed as water-proxy-dominated when that value exceeds 0.50. This can be affected by polygon/land-area measurement differences; it is not used to alter any gap estimate. The two zero-`ALAND` Texas tracts are separately disclosed; the clamped proxy reaches 1.0 in those rows. Maximum proxy shares by region were 0.1196, 0.7606, 0.6028, and 1.0 respectively. Geometry was used only for this score-free audit and spatial feature aggregation, not as an external target proxy.

### Handling policy

| Case | Method behavior | Explicit limitation / safeguard |
|---|---|---|
| No organizer reference for a component | Organizer score omits the undefined component; the composite averages only defined terms. | Never convert an undefined component to a meaningful zero. Serialized zeros are placeholders and are excluded by defined flags. If no top-level component is defined, no composite is emitted. |
| Zero population | Use the v0 expected-count floor/baseline and the stated urbanity fallback; facility-v2 uses `ALAND` floor `0.01 km²` in its density index. In the standalone establishments v2 rule, positive Overture places at zero population yield a clipped gap of 0 and remain defined; zero population plus zero places is undefined. | 26 official tracts have zero population. The establishments sensitivity audit found 24 with positive POIs and 2 with no POIs; this is a model convention, not evidence those tracts have no establishments. |
| Zero land area | v0 expected-reference calculations floor area at `0.01 km²`; the facility density calculation uses the same floor. | Two tracts have zero `ALAND`; do not divide by zero, infer a target, or label the tract as water-only based on this alone. |
| Water-dominated / geometry-area mismatch | Report the clamped land-area complement as a descriptive proxy; no water-specific correction is applied. | 43 tracts exceed 50% by this proxy. Geometry-area mismatch can cause error; there is no observed water label in this calculation. |
| No observed named roads | A zero Overture road count is retained. In v0 the transport proxy is still defined when expected route length reaches 6 km; route-v2 is undefined when its selected route denominator is zero. | There are 132 tracts with no v2 named-road length and 2,644 with no selected v2 route-network length. The named/route mismatch can yield a clipped zero deficit where numerator length exceeds the denominator; this is a known limitation (notably Maricopa's aggregate length ratio). |
| No Overture buildings | Keep the observed zero. The v0 building term can still be defined by its expected-count threshold; standalone building v2 is undefined at exactly zero Overture buildings. | Two such tracts occur, both in South-Central TX. ACS housing units are not a denominator or floor. |
| No Overture places / no POI facility category | Apply each version's documented definedness rule. In the fixed-prior facility model, a facility type with `P(reference present)=0` is undefined; otherwise it contributes its expected gap. Establishments is undefined when both population and total Overture places are zero. | Two South-Central TX tracts have zero places and also zero named roads/buildings; do not treat missing place data as proof of absent reference establishments. |
| Missing `pct_urban` | Use `pop_urban/pop_total` if population is positive; for zero population use a neutral urban fraction of 0.5. Record fallback provenance. | 26 rows have missing `pct_urban`; they are not silently assigned rural in the model. Earlier rural discovery comparisons instead excluded missing values. |
| Extra rows in supplied data | Join on text GEOID and restrict all scored analyses to official sample IDs. South-Central TX strata/ACS tables contain 7 rows beyond the 6,003 official IDs. | No placeholder score fields are used. The 415 eastern-WA template rows are formatting-only and are not scored-region evidence. |
| Leading zeros / mixed state coverage | Keep GEOIDs as strings. Maricopa's official set includes 1,592 Arizona tracts and New Mexico tract `35023970000` (verified present in the official sample). | Never infer membership from county text or numeric conversion. Validate exact ordered official sample coverage. |

## Reproduce the aggregate audit

From the competition repository, with the authorized workspace available:

```sh
uv run --with-requirements requirements.txt \
  python scripts/audit_writeup_edge_cases.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/writeup-edge-cases.json
```

The audit reads only sample GEOID column 0 (not score-placeholder cells), the listed strata columns, aggregate provided-feature columns, and the provided ACS `GEOID`/`geometry` columns. It reads no labels, hidden organizer results, removed reference layers or equivalent copies, leaderboard information, or additional public data. It writes counts only, with no per-GEOID score/prediction output. The JSON records source URIs, object/cache bytes, and SHA-256 for the local sample, strata, and feature-cache files. SHA-256 of the remote Overture/ACS source objects is not published in the source inventory and is not claimed here.
