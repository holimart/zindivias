# S2: H1b assumption-bound rule counterfactual

S2 is a label-free alternative construction built from frozen C2 component estimates, not an accuracy estimate. It applies two explicit, unverified assumptions: a Microsoft-sourced Overture building record is treated as exact overlap with the removed Microsoft reference, and a zero Overture fire/EMS/school category count is treated as a zero USGS reference count for dropping that facility type from the POI facilities half. The building component is multiplied by one minus the per-tract source share; missing shares leave C2 unchanged. POI is recomputed from retained defined facility types and the unchanged feature-only establishment half, and the final score is the mean of whichever transport, building, and POI components are defined. There are no fitted coefficients and no S-style shrinkage is stacked on top.

Neither assumption is verified: Overture provenance does not prove footprint-level overlap, and an Overture category zero does not establish a USGS zero. C2 expected counts are feature-derived proxies, not organizer reference counts. S itself is a composite-only cross-construction artifact; its component decomposition is not identifiable from its file. Accordingly, component means for S are reported as unavailable rather than inferred. No labels, removed/equivalent references, external data, sample score placeholders, C0, leaderboard scores, or scorecards were used. S2 versus S versus C2 means and changed-tract shares are descriptive candidate-output comparisons only; they do not establish hidden-target RMSE or select an upload. No upload is authorized.

Frozen artifact: `reports/candidates/S2_h1b_assumption_bound_v1.csv` (SHA-256 `44ba76abcd9e1fb0aa7194e3c0fadf567debce40e29ed4bdf93ef02ae9514577`); preregistration: `config/shrink-s2-assumption-bound-preregistered.json`.

| Region | Model | Mean composite | Transport defined mean | Building defined mean | POI defined mean | Changed vs S | Changed vs C2 |
|---|---|---:|---:|---:|---:|---:|---:|
| maricopa-az | S_crossmethod_reliability_shrink_v1 | 0.324339 | not identifiable | not identifiable | not identifiable | 0.000 | 1.000 |
| maricopa-az | S2_h1b_assumption_bound_v1 | 0.095875 | 0.242019 | 0.028953 | 0.083259 | 1.000 | 0.852 |
| maricopa-az | C2_component_estimator_v0 | 0.196684 | 0.242019 | 0.049050 | 0.312851 | 1.000 | 0.000 |
| northern-ca | S_crossmethod_reliability_shrink_v1 | 0.264791 | not identifiable | not identifiable | not identifiable | 0.000 | 1.000 |
| northern-ca | S2_h1b_assumption_bound_v1 | 0.066558 | 0.171366 | 0.007164 | 0.057450 | 1.000 | 0.690 |
| northern-ca | C2_component_estimator_v0 | 0.153839 | 0.171366 | 0.046569 | 0.233682 | 1.000 | 0.000 |
| eastern-ok | S_crossmethod_reliability_shrink_v1 | 0.232068 | not identifiable | not identifiable | not identifiable | 0.000 | 1.000 |
| eastern-ok | S2_h1b_assumption_bound_v1 | 0.036992 | 0.118874 | 0.003631 | 0.028724 | 1.000 | 0.620 |
| eastern-ok | C2_component_estimator_v0 | 0.117341 | 0.118874 | 0.019221 | 0.199346 | 1.000 | 0.000 |
| south-central-tx | S_crossmethod_reliability_shrink_v1 | 0.239882 | not identifiable | not identifiable | not identifiable | 0.000 | 1.000 |
| south-central-tx | S2_h1b_assumption_bound_v1 | 0.065002 | 0.134475 | 0.035876 | 0.051138 | 1.000 | 0.767 |
| south-central-tx | C2_component_estimator_v0 | 0.173367 | 0.134475 | 0.091668 | 0.255992 | 1.000 | 0.000 |

The comparison table is in `reports/shrink-s2-assumption-bound-comparison.json`. These descriptive summaries are not validation against the organizer target.
