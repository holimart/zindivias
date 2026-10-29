# Methodology

## Scope and safeguards

The organisers provide no training labels. The four official sample files define 9,379 scored GEOIDs (`reports/sample-contract-audit.json#/authoritative_unique_geoids`). The submitted `V2_model` estimate uses challenge-provided features only; sample score placeholders, public scores, hidden targets, and removed reference layers do not enter prediction construction. Its final format-corrected A artifact is `V2_model_cgs`; B is the pre-registered 50% within-region-shrink companion `B_from_A_cgs`. Operator confirmation remains pending (`reports/final-two.json#/final_candidates`, `#/corrected_cgs_selection`). Neither candidate is claimed to match the hidden score.

## Components and definedness

The composite averages only defined transport, building, and POI components; undefined terms are omitted, not replaced with zero. Transport uses the frozen named-road gap and provided route denominator. Building and POI terms use the frozen provided-Overture specifications and documented priors. ACS housing units are not a building-count floor or denominator. Equal component weights are transparent, not fitted accuracy-optimal weights. Appendix A retains exact formulas, input hashes, preregistrations, sensitivity tests, and approval gates; Appendix B records zero/missingness edge cases.

## Provenance and reproduction

Scored predictions read only the approved organiser data package. The public CAL FIRE Camp Fire DINS extract is used only in the discovery mechanism check, joined to provided NorCal tract geometry and never to a scored candidate (`reports/discovery-dins-mechanism.json#/external_source`, `#/scored_candidates`). The catalogue records CC BY, version unspecified, and the retrieval date; the external register and workspace manifest preserve URL, query, licence, limitations, and raw hash. No Microsoft footprint or other removed reference layer was accessed.

Regenerate components with the pinned repository workflow and each discovery report with its command in the relevant section. Scripts, fixed parameters, hashes, and outputs are recorded in Appendix A and the reports. This is a reproducibility record, not validation against labels: there is no labelled holdout, and public leaderboard scores do not establish hidden-target accuracy.
