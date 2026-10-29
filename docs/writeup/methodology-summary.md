# Methodology

## Scope and safeguards

The organisers provide no labels. Four official samples define 9,379 scored GEOIDs (`reports/sample-contract-audit.json#/authoritative_unique_geoids`). `V2_model` uses challenge features only; placeholders, public scores, hidden targets and removed references do not enter predictions. A is `V2_model_cgs`; B is the pre-registered 50% within-region-shrink companion `B_from_A_cgs`. Operator confirmation is pending (`reports/final-two.json#/final_candidates`, `#/corrected_cgs_selection`); neither candidate claims hidden-score accuracy.

## Components and definedness

The composite averages defined transport/building/POI terms; undefined terms are omitted, not zero-filled. Transport uses the frozen named-road gap and provided route denominator; building/POI use fixed provided-Overture specifications and priors. ACS housing units are not a building denominator. Equal weights are transparent, not accuracy-fitted. Appendix A records formulas, hashes, preregistrations, sensitivities, and approval gates; Appendix B covers zero/missingness cases.

## Provenance and reproduction

Scored predictions use approved organiser data only. Public CAL FIRE Camp Fire DINS is discovery-only, joined to provided NorCal geometry and never used in scored candidates (`reports/discovery-dins-mechanism.json#/external_source`, `#/scored_candidates`). Its catalogue states CC BY, version unspecified, and retrieval date; the manifest preserves URL, query, licence, limits, and hash. No removed reference layer was accessed.

Rebuild components with the pinned workflow and discovery reports using their documented commands. Appendix A and the reports record scripts, fixed parameters, hashes, and outputs. These checks do not validate against labels: no labelled holdout exists, and public leaderboard scores do not establish hidden-target accuracy.

## Public-score transparency (descriptive only)

Public-split RMSEs: all-zero C0 (`uRRw9KpB`) `0.1072`; corrected-order `V2_model_cgs` (`hvLtPVmr`) `0.1110`; its pre-registered 50% within-region-mean shrink, `B_from_A_cgs` (`eAaWXWxM`), `0.0975`. This is consistent with regional means doing more work; V2's within-region variation is not shown to carry target signal. Public results are not private-split evidence or a private-score forecast. No candidate values changed: the frozen protocol bars leaderboard-derived algebra or retuning. Sources: `reports/final-two.json`; shared `data/ledger/submissions.jsonl` (mirror `reports/submissions.jsonl`); `reports/lb-scorecard-snapshot.json`; `docs/final-two-protocol.md`.
