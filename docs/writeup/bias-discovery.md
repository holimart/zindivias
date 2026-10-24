# Draft discovery — a rural × SVI minority-domain screen in South-Central Texas

**Draft for operator review; not a claim about true organiser scores.** The competition has no training labels, and the reference layers used to calculate the hidden target are removed. Every score below is a C2/C3 provided-data proxy. The analysis identifies a subgroup for follow-up; it does not establish an observed real-world mapping disparity or causation.

## Proposed pattern outside the published scorecard grid

The official scorecard description lists five equity metrics across separate dimensions such as rural/urban, SVI, CVI, tribal status, and hazard exposure. It does not specify a joint cell for rural tracts in the upper quartile of the provided `svi_minority` domain field. This draft examines that intersection within the South-Central Texas sample, rather than restating either marginal rural/urban or overall-SVI comparisons.

The analytic group is defined using only provided strata fields: `pct_urban < 0.5` defines the rural screen; among rural sample tracts with nonmissing `svi_minority`, the top quartile is `svi_minority >= 0.6549`. The threshold is computed within this region's rural tracts; it is not a published competition cutpoint. Four of 991 rural tracts lacked `svi_minority` and were excluded. This field is used as supplied and is not a claim about individual residents or legal tribal status.

## Quantified proxy evidence

| Group | Tracts | Mean C2 predicted composite |
|---|---:|---:|
| Rural, `svi_minority >= 0.6549` | 248 | 0.06522 |
| Other rural tracts with the field | 739 | 0.03034 |
| Difference (upper-quartile minus comparison) | — | **+0.03488** |

A 50,000-resample tract bootstrap gives a 95% percentile interval of **[+0.01676, +0.05509]** for the C2 difference. Under the preregistered C3 sensitivity candidate (all expected-reference proxies scaled by 1.25), the group means are 0.08578 vs 0.03931, difference **+0.04647**, interval **[+0.02678, +0.06808]**. These intervals reflect resampling variability among tract-level predictions only, not error in the estimator or uncertainty about the hidden targets. Spatial correlation is not modeled.

The C2 component comparison uses only tracts where that component is defined:

| Component | Upper quartile (n; mean) | Other rural (n; mean) | Difference | Bootstrap 95% interval |
|---|---:|---:|---:|---:|
| Transport | 210/248; 0.03353 | 657/739; 0.01533 | +0.01820 | [-0.00192, +0.04114] |
| Buildings | 248/248; 0.02856 | 739/739; 0.00000 | +0.02856 | [+0.01187, +0.04828] |
| POI | 248/248; 0.11783 | 739/739; 0.07153 | +0.04630 | [+0.01961, +0.07450] |

In this proxy screen, the difference is concentrated in building and POI gaps; the transport interval includes zero. The result is a hypothesis about the model's provided-data screen, not evidence that the organiser's score is higher in these tracts.

The highest C2 predictions in the upper-quartile group include these named GEOIDs (all have provided `pct_urban = 0.0`):

| South-Central TX GEOID | `svi_minority` | C2 composite | C2 transport | C2 building | C2 POI |
|---|---:|---:|---:|---:|---:|
| `48253020102` | 0.8039 | 0.9701 | 0.0000 | 0.9691 | 0.9711 |
| `48001950402` | 0.7942 | 0.9599 | 1.0000 | 0.9239 | 0.9558 |
| `48169980000` | 0.9353 | 0.9396 | 0.0000 | 0.9775 | 0.9018 |
| `48001950401` | 0.8334 | 0.9392 | 1.0000 | 0.8658 | 0.9517 |
| `48185180400` | 0.7483 | 0.8739 | 0.0000 | 0.8261 | 0.9217 |

The exact inputs, estimates, flags, group definitions, bootstrap seeds, and all top-10 candidate tracts are in `reports/discovery-rural-svi-minority.json`.

## Why this may matter; what it does not show

The official challenge describes South-Central Texas as a heat/drought region along I-35/I-37. If the building/place deficits suggested by this screen are present in operational maps, incomplete addressable-building and place information could make dispatch geocoding, locating services, evacuation planning, or relief routing less reliable for affected rural tracts. The named GEOIDs let organizers or local reviewers inspect these places using authorized data. No emergency incident records, dispatch outcomes, or observed routing failures were available or analyzed, so these consequences remain plausible risks—not measured impacts.

The predictor does not use `svi_minority`, but it is still a heuristic based on Overture summaries and population/land/urbanity covariates. It has no label validation; reported score values must not be interpreted as organiser targets. The interval does not capture estimator or spatial uncertainty. The group is descriptive, and the cutoff should be checked with stakeholders before making a stronger narrative. No additional public data was used, and nothing from this discovery was added to the scored candidate CSVs.

## Reproduction

```sh
/home/martin/hddProjects/langywrap/uv run --with pyarrow --with numpy \
  python scripts/discovery_rural_svi_minority.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/discovery-rural-svi-minority.json
```

The 50,000-draw bootstrap uses fixed seeds; a second run generated byte-identical JSON. See `docs/writeup/methodology.md` for data provenance, proxy formulas, and broader limitations. Before any prize-form submission, review the interpretation, context, and tract IDs; do not present the estimates as verified gaps.
