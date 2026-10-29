# Supporting discovery screens

These exploratory screens are retained for reproducibility and transparency. They are not among the three discovery findings presented in the main entry.

## Secondary exploratory lead — rural × SVI minority-domain screen in South-Central Texas

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

## Section 4 — External public context layers: one weak broadband lead, no robust incremental pattern

**Scope and interpretation.** This is a follow-on discovery-only screen using four additional public federal data layers staged outside git at `$COMPETITION_WORKSPACE/external-discovery-2026-10-27/`: CDC/ATSDR SVI 2022, USDA ERS RUCA 2020, Census Planning Database 2026, and Census 2020 AIANNH tribal-area polygons. The acquisition manifest records source URLs, retrieval UTC times, byte counts, SHA-256 values, and the licence-check status; the analysis verifies each listed raw-file hash. The fetched publisher pages did not establish dataset-specific licence terms. No public-domain status or redistribution permission is asserted; the terms should be confirmed before any redistribution. Raw data remain outside git in the authorized workspace. FEMA NRI and FCC broadband were not included because their acquisition failed. Four frozen, label-free candidates (A1/A2/A3/V2) provide proxy outcomes, not organiser scores or accuracy labels. The fixed 28-test family covers A1 composite and components plus A2/A3/V2 composites. Region-adjusted effect sizes are partial correlations; uncertainty is a 1,000-draw county-cluster bootstrap stratified within the four regions. The residual screen conditions on region and supplied `pct_urban`, SVI, RUCA and tribal-share controls. OOF ΔR² compares that baseline to adding one external layer, using five deterministic region-stratified folds that hold out whole state-county groups. BH-adjusted q-values cover all 28 conditional residual-screen tests. All estimates describe frozen proxies only; they do not validate hidden targets, imply causation, or alter any scored candidate.

**CDC/ATSDR SVI 2022.** On the 9,294 complete cases (85 selected SVI values were missing), the region-adjusted correlation with A1 composite was −0.003 (county-bootstrap 95% CI [−0.045, +0.035]); county-grouped OOF ΔR² was effectively zero. Some proxy subcomponents differed: A1 POI had r=−0.122 [−0.181, −0.079], and V2 composite r=−0.130 [−0.174, −0.096], but the external SVI exposure had no residual variance after conditioning on the supplied `svi_overall` field, so an incremental effect was not estimable. This is a source/vintage cross-check, not evidence that SVI explains real-world or hidden coverage gaps; no independent predictive gain was observed.

**USDA ERS RUCA 2020.** RUCA joined for all 9,379 official GEOIDs; complete analytic N was 9,294 after the shared provided-control screen. The region-adjusted association varied across the provided estimators and components: RUCA code with A1 transport r=+0.322 [+0.269, +0.374], A1 POI r=−0.156 [−0.210, −0.122], A1 composite r=+0.029 [−0.001, +0.074], and A3 composite r=+0.342 [+0.286, +0.404]. Because the external RUCA code was represented by the provided RUCA control, its conditional residual effect was not estimable and county-grouped OOF ΔR² was effectively zero. RUCA primary codes are ordinal, so these are screening associations rather than interval-scale changes; the sign disagreement across proxies and lack of incremental holdout signal preclude a common coverage-gap claim.

**Census PDB 2026 broadband context.** The selected exposure was tract percentage of households with no internet; 9,297 exposure values were available, and 9,292 tracts entered the complete-case model. Its region-adjusted association with A1 building-gap proxy was r=−0.042 (95% CI [−0.075, −0.007]); after the supplied controls, partial r=−0.051 [−0.074, −0.030], with BH q=0.056 across the 28 residual tests. The region-adjusted direction matched in three of four regions, while whole-county OOF ΔR² was only +0.0033. Thus this is a small, borderline, diagnostic lead about a model proxy—not a robust multiplicity-controlled finding, not evidence that offline households have better mapped buildings, and not a basis for changing predictions. Other PDB composite and transport associations did not add consistent held-out signal.

**Census AIANNH 2020 tribal-area polygons.** We dissolved overlapping AIANNH polygons, projected them with 2024 Census tract geometry to EPSG:5070, and calculated tract area-share overlap; exact geometry joins covered all 9,379 official GEOIDs, with 1,042 positive overlaps. The Maricopa-labeled sample includes `35023970000`, whose GEOID is New Mexico; we retained its official sample region label and used its matching New Mexico tract geometry rather than reassigning or dropping it. For A1 composite the region-adjusted r was +0.038 (95% CI [−0.020, +0.073]) and grouped OOF ΔR² was effectively zero. After provided `tribal_pct` and other controls, A1 composite partial r was −0.007 [−0.023, +0.005]; no conditional result survived BH correction. The layer's unique exposure variation after supplied controls was only 0.2% of raw exposure SD, demonstrating strong redundancy rather than an independent tribal-area signal. Polygon overlap does not establish any resident's identity or legal status, and no coverage-disparity claim is supported.

Reproduce the analysis from the competition repository with the manifested raw files in the authorized workspace:

```sh
/home/martin/hddProjects/langywrap/uv run --quiet \
  --with numpy==2.5.4 --with pyarrow==26.0.0 --with shapely==2.2.0 \
  --with pyproj==3.8.0 --with pyshp \
  python scripts/analyze_discovery_external_layers.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --out reports/discovery-external-layers.json
```

The config is `config/discovery-external-layers-preregistered.json`; the complete 28-test results, missingness, source URLs/licence status/hashes, and tract-geometry provenance are in `reports/discovery-external-layers.json`. External features were used only in this discovery audit, never in scored candidate construction or uploads. Census TIGER2024 tract boundaries were used only as geography for the AIANNH polygon overlay; no TIGER roads, Microsoft footprints, USGS facilities, CBP data, or equivalent copies were read or used to rebuild the scored target.

## T1 diagnostic archive

Detailed implementation, results, and reproduction commands are in [Appendix diagnostics](../appendix-diagnostics.md#t1-named-stroke-class-drop).
