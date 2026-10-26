# V2 intersectional score-distribution screen

This is a descriptive comparison of the **frozen V2 model's own scores**, not organizer targets, accuracy, causal effects, or evidence of operational impact. It extends the one-way summaries in [`reports/v2-strata-descriptive-audit.json`](../../reports/v2-strata-descriptive-audit.json) by intersecting each region with provided rural/urban, overall SVI, overall CVI, tribal-any, and one predeclared hazard band (drought, wildfire, heat, or flood). All bands and missing-value labels are inherited from [`config/v2-strata-descriptive-preregistered.json`](../../config/v2-strata-descriptive-preregistered.json); the four combinations and `n >= 30` eligibility rule were fixed in [`config/v2-intersectional-descriptive-preregistered.json`](../../config/v2-intersectional-descriptive-preregistered.json).

For each intersection family, the maximum is taken over every unordered pair of eligible observed cells in that family. Absolute SMD is `abs(mean_a - mean_b) / pooled_sample_sd`, using the conventional pooled sample variance and `ddof=1`. Cells with `n < 30` are listed with their counts and no score summary. Missing strata are explicit categories, not imputed or dropped. Unobserved Cartesian combinations are counted separately. The report includes the full eligible and suppressed cell inventory, per-family maxima, source hashes, and ID/score acceptance checks: [`reports/v2-intersectional-descriptive-audit.json`](../../reports/v2-intersectional-descriptive-audit.json) (SHA-256 `f874c4ec949f4ed0316e34ac38fb0a303cd48984d7d646f6c2c6493dea89d453`).

| Predeclared intersection family | Observed cells | Eligible (`n >= 30`) | Suppressed (`1 <= n < 30`) | Maximum pairwise absolute SMD (eligible cells) |
|---|---:|---:|---:|---:|
| Region × rural/urban × SVI × CVI × tribal × drought | 439 | 79 | 360 | 1.809 |
| Region × rural/urban × SVI × CVI × tribal × wildfire | 554 | 72 | 482 | 1.511 |
| Region × rural/urban × SVI × CVI × tribal × heat | 455 | 82 | 373 | **1.889** |
| Region × rural/urban × SVI × CVI × tribal × flood | 543 | 60 | 483 | 1.813 |
| **All four partitions** | **1,991** | **293** | **1,698** | **1.889 maximum** |

The largest observed pair occurs in the heat family: an Eastern Oklahoma rural / SVI `[0.6,0.8)` / CVI `[0.52,0.60)` / tribal-land-intersection / heat `[5,15)` cell (`n=56`, mean V2 score `0.0188`) versus a South-Central Texas rural / SVI `>=0.8` / CVI `>=0.60` / no-tribal-intersection / heat `>=30` cell (`n=40`, mean `0.1079`). This describes only a difference in model predictions under the chosen intersections; it does **not** establish a difference in actual mapping coverage or risk. No organizer targets, labels, leaderboard values, removed reference layers/equivalents, or external data were used.

Reproduce with `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_v2_intersections.py`. The V2 candidate is pinned at SHA-256 `3193ee78a64004f007829f1f9c12d4aa8d4f7c15136771e8da9108fdcea06f12`.
