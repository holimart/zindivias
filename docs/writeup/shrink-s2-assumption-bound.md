# S2: H1b assumption-bound rule counterfactual

S2 is a label-free alternative construction built from frozen C2 component estimates, not an accuracy estimate. It applies two explicit, unverified assumptions: a Microsoft-sourced Overture building record is treated as exact overlap with the removed Microsoft reference, and a zero Overture fire/EMS/school category count is treated as a zero USGS reference count for dropping that facility type from the POI facilities half. The building component is multiplied by one minus the per-tract source share; missing shares leave C2 unchanged. The transport component remains unchanged from C2. The POI facilities half is the arithmetic mean of retained defined facility types; the establishment half is the unchanged feature-only CBP proxy; POI is the mean of whichever halves are defined. The final score is the mean of whichever transport, building, and POI components are defined. There are no fitted coefficients and no S-style shrinkage is stacked on top.

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

### Frozen evidence cross-check and sensitivity limits

The table's displayed composite/component means and changed-tract shares are read directly from `reports/shrink-s2-assumption-bound-comparison.json` (SHA-256 `2328a34cae77509691fdd3a24608b925fc2b3d07e9a4d9e155bba71f1e507162`; JSON pointers `/side_by_side_by_region/{region}/models/{candidate}/...`). That report says: “S2 is a deterministic assumption-bound alternate construction, not evidence that its levels or masks match the organizer target.” The S2 artifact digest above is also present at `/candidate/sha256`; the report marks both `target_accuracy_identifiable` and `upload_authorized` false.

A separate frozen mask endpoint diagnostic reports the share of official tracts whose composite changes by strictly more than 0.05 when all frozen-defined zero-count fire/EMS/school components are restored: Eastern Oklahoma 59.4%, Maricopa 77.5%, Northern California 63.5%, and South-Central Texas 70.1%. The maximum regional p95 absolute delta is about 0.166. These are mask-counterfactual sensitivities, not evidence that any score is closer to the hidden target. Source: `reports/s2-poi-facility-mask-endpoints.json` (SHA-256 `2b17cec8db5b1289bb38e4c9aeac14dd7e466b7f897525b6262f8158fd45ec07`, `/per_region/{region}/delta_summary_vs_frozen_S2`); the report explicitly sets `target_accuracy_identifiable` false.

The independent building-source-share sensitivity varies that provided share by ±0.10 while holding the frozen S2 non-building components and defined masks fixed. Across regions and both offsets, p95 absolute score delta is at most 0.03048, maximum absolute delta at most 0.04887, and no tract exceeds the strict 0.05 delta threshold. The source report labels this descriptive-only and says target accuracy is not identifiable: `reports/s2-building-overlap-sensitivity.json` (SHA-256 `6625dc305e8c29e70a0dcecca1efecda6ceb7d2deb36bcd015fb42fa327725f0`, `/sensitivity_design`, `/curve_by_region/{region}/scenarios/{+0.10|-0.10}/summary`, `/interpretation`). The different magnitudes across these two counterfactuals do not validate either assumption against organizer scores.

Accordingly, neither these sensitivities nor agreement/differences among S2, S, and feature-only C2 establish target accuracy. Hidden-target RMSE is not identifiable; this writeup reports mechanism and sensitivity evidence only, and makes no candidate-selection or upload recommendation.
