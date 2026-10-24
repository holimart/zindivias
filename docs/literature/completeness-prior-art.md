# Completeness estimation and intrinsic quality: prior art

**Reviewed and URL-checked: 2026-10-24.** This is a literature and documentation review, not a transfer of external labels into a candidate. No removed TIGER, Microsoft building, USGS facility, or CBP reference layer was obtained; no article companion data were downloaded. The competition rules allow public sources in the Best Bias Discovery writeup only and restrict scored candidates to challenge-provided data.

## URL check and evidence standard

All 16 references requested by the literature backlog were checked on 2026-10-24. Fifteen URLs were confirmed to reach the cited publication host or official documentation. Six DOI destinations returned publisher anti-bot/access-denied pages rather than readable article content; those are marked as **resolved, content fetch blocked**, not as full-text reviewed. The Biljecki et al. DOI returned only a redirect response, so its final destination could not be confirmed and it is **not counted** toward the verified-URL total. The 15 confirmed URLs each have an applicability note below; the unconfirmed item is retained and explicitly flagged rather than represented as verified.

The status verifies URL resolution, not the correctness of every bibliographic detail or access to a paywalled full text. Where the publisher preview was blocked, notes are deliberately limited to the cited topic and available bibliographic metadata; consult the linked source before relying on paper-specific details beyond these notes.

## Studies and operational documentation

### 1. Barrington-Leigh & Millard-Ball (2017) — road completeness

**Citation:** Christopher Barrington-Leigh and Adam Millard-Ball, “The world’s user-generated road map is more than 80% complete,” *PLOS ONE* 12(8), e0180698. [DOI](https://doi.org/10.1371/journal.pone.0180698). **URL check:** resolved; PLOS page and abstract fetched.

**Method and limits:** The paper combines a visual road comparison with model-based extrapolation and a second estimate from sigmoid saturation curves fitted to cumulative OSM contributions. Its headline global estimate is not a local ground-truth rate; contribution saturation, imagery interpretation, road class, and study geography all constrain transfer. The PLOS page also links a 2019 correction; consult the corrected record before quoting exact estimates.

**Applicability (2 lines):** Informs `bias-est-transport-route-ref`, `bias-est-tx-fm-roads-sensitivity`, and `bias-model-road-topology-connectivity`: completeness estimates need an explicit road universe and independent or structural evidence. Do not import its global percentage or infer that Overture routes/classes have the same completeness as OSM road edits.

### 2. Herfort et al. (2023) — building completeness inequality

**Citation:** Benjamin Herfort et al., “A spatio-temporal analysis investigating completeness and inequalities of global urban building data in OpenStreetMap,” *Nature Communications* 14, 3985. [DOI](https://doi.org/10.1038/s41467-023-39698-6). **URL check:** resolved; Nature article page and abstract fetched.

**Method and limits:** The study uses a machine-learning approach to estimate building-stock completeness across 13,189 urban agglomerations and analyzes temporal inequality across human-development-index groups, population sizes, and regions. Its urban, global OSM estimates are not U.S. tract labels and cannot validate the competition’s Overture-based proxies.

**Applicability (2 lines):** Informs `bias-est-building-near-zero-memo`, `bias-model-density-context-candidate`, and `bias-model-feature-ensemble-stability`: evaluate completeness hypotheses across geography and development context, not only in aggregate. Treat socioeconomic and density associations as stratification hypotheses, not as a substitute denominator or a causal effect.

### 3. Hecht, Kunze & Hahmann (2013) — building footprints over space and time

**Citation:** Robert Hecht, Christian Kunze, and Stefan Hahmann, “Measuring Completeness of Building Footprints in OpenStreetMap over Space and Time,” *ISPRS International Journal of Geo-Information* 2(4), 1066–1091. [DOI](https://doi.org/10.3390/ijgi2041066). **URL check:** resolved to the MDPI publication host; the page returned access denied to the fetch service, so article text was not retrieved.

**Method and limits:** The cited work measures building-footprint completeness spatially and temporally by comparison with reference information. Such rates depend on reference coverage, definition, spatial unit, and vintage; “present in OSM” is not equivalent to current real-world structure presence.

**Applicability (2 lines):** Informs `bias-est-building-near-zero-memo` and `bias-model-source-freshness-candidate`: keep time/vintage uncertainty separate from absence and accuracy. Do not use an external footprint comparison to build scored values; document the version limits of the supplied summaries instead.

### 4. Zhou (2018) — density/completeness relationship

**Citation:** Zhou (2018), *International Journal of Geographical Information Science*, density–completeness study, DOI [10.1080/13658816.2017.1395883](https://doi.org/10.1080/13658816.2017.1395883). **URL check:** resolved to the Taylor & Francis publication endpoint; anti-bot page prevented reading the article body.

**Method and limits:** The work is relevant to empirical relationships between mapped-feature density and completeness. Density can be a useful contextual signal, but the relation is geography-, feature-, and reference-dependent; the publisher preview could not be inspected in this environment, so no numerical result is reproduced here.

**Applicability (2 lines):** Informs `bias-model-density-context-candidate` and `bias-model-feature-ensemble-stability`: test density as a model-derived prior using only provided features. Do not treat density as observed completeness or transfer an external coefficient to these four regions.

### 5. Brovelli & Zamboni (2018) — geospatial quality assessment

**Citation:** Maria Antonia Brovelli and Giacomo Zamboni (2018), *ISPRS International Journal of Geo-Information* 7(8), article 289, DOI [10.3390/ijgi7080289](https://doi.org/10.3390/ijgi7080289). **URL check:** resolved to the MDPI publication host; article page was access-blocked to the fetch service.

**Method and limits:** Included as related OSM/geospatial quality-assessment prior art. Because the publication content was blocked, this note does not assert a paper-specific algorithm or result; the linked DOI is retained for direct follow-up rather than used as evidence for a particular estimator.

**Applicability (2 lines):** Informs `bias-model-schema-missingness-candidate` and `bias-model-feature-ensemble-stability`: keep completeness, positional accuracy, semantic quality, and internal consistency as distinct questions. No quality metric from this citation is assumed to be a hidden-score target.

### 6. Barron, Neis & Zipf (2014) — intrinsic OSM quality

**Citation:** Christoph Barron, Pascal Neis, and Alexander Zipf (2014), “A comprehensive framework for intrinsic OpenStreetMap quality analysis,” *Transactions in GIS*, DOI [10.1111/tgis.12073](https://doi.org/10.1111/tgis.12073). **URL check:** resolved to Wiley; the publisher returned an anti-bot page, so full text was not fetched.

**Method and limits:** The framework concerns quality properties that can be examined from the volunteered data itself, rather than assuming a complete external reference is always available. Intrinsic indicators can flag unusual structure or metadata but do not prove positional or feature completeness against the ground truth.

**Applicability (2 lines):** Informs `bias-model-schema-missingness-candidate`, `bias-model-poi-confidence-weighting`, and `bias-model-source-freshness-candidate`: distinguish Overture’s own confidence/provenance/schema indicators from validated accuracy. An internal indicator may guide review, but it must not be described as an independent completeness label.

### 7. Senaratne et al. (2017) — VGI quality review

**Citation:** H. Senaratne et al. (2017), “A review of volunteered geographic information quality assessment methods,” *International Journal of Geographical Information Science*, DOI [10.1080/13658816.2016.1189556](https://doi.org/10.1080/13658816.2016.1189556). **URL check:** resolved to Taylor & Francis; article text was hidden behind an anti-bot page.

**Method and limits:** This review organizes VGI quality dimensions and assessment approaches, including the distinction between intrinsic indicators and comparisons with reference data. A review taxonomy does not establish that one indicator is valid for a particular provider, feature type, or region.

**Applicability (2 lines):** Informs `bias-model-cross-theme-support`, `bias-model-source-freshness-candidate`, and `bias-doc-methodology-rubric`: report what a feature’s source, confidence, or schema state actually measures. Do not equate provenance or confidence with truth, freshness, or real-world service availability.

### 8. Biljecki et al. (2023) — building attribute quality

**Citation:** Biljecki et al. (2023), *Building and Environment*, article 110295, DOI [10.1016/j.buildenv.2023.110295](https://doi.org/10.1016/j.buildenv.2023.110295); listed by the backlog as building-attribute-quality prior art. **URL check:** not confirmed. The fetch returned only “Redirecting” and did not expose the destination; this entry is **not counted** among the 15 verified URLs.

**Method and limits:** The citation topic is building-attribute quality, which is distinct from footprint presence/count completeness. As the destination and article content were not available to this review, no method, result, or quantitative claim is attributed to the paper.

**Applicability (2 lines):** Potentially informs `bias-model-building-source-prior` and `bias-model-building-morphology-prior` once the citation is rechecked. Until then, use only as a flagged lead; do not cite it as verified evidence or adopt its findings in the estimator.

### 9. Ullah et al. (2023) — MapSwipe completeness

**Citation:** Ullah et al. (2023), *ISPRS International Journal of Geo-Information* 12(4), article 143, MapSwipe/completeness study, DOI [10.3390/ijgi12040143](https://doi.org/10.3390/ijgi12040143). **URL check:** resolved to the MDPI publication host; fetch was access-blocked, so the paper body was not read.

**Method and limits:** The backlog identifies this as MapSwipe-based completeness work: crowdsourced visual interpretation can support targeted assessment of mapped buildings. Such samples depend on imagery, task design, contributor coverage, and reference interpretation; they are not a national or tract-level ground truth by themselves.

**Applicability (2 lines):** Informs `bias-model-building-morphology-prior` and `bias-model-feature-ensemble-stability` as a potential approach for future public-writeup validation. No MapSwipe labels or imagery were obtained or used in this competition analysis.

### 10. Microsoft US Building Footprints — README and release caveats

**Citation/documentation:** [microsoft/USBuildingFootprints README](https://github.com/microsoft/USBuildingFootprints). **URL check:** resolved; GitHub README fetched (repository is archived/read-only as of the checked page).

**Method and limits:** The README describes computer-generated footprints from satellite imagery, an ODbL license, and imagery vintage that varies by location; it notes a large 2019–2020 focal region and older imagery elsewhere. The README describes the source family but does not make each footprint a contemporaneous, independently verified structure observation.

**Applicability (2 lines):** Informs `bias-est-building-near-zero-memo` and the building component of `bias-est-v2-assembly-probe`: reference vintage and construction method can create disagreement that resembles a map gap. This is documentation only; the removed footprint files were not retrieved or used.

### 11. Overture Maps schema — routes, sources, and confidence

**Citation/documentation:** [Schema overview](https://docs.overturemaps.org/schema/), [Transportation Segment](https://docs.overturemaps.org/schema/reference/transportation/segment/), and [Places Place](https://docs.overturemaps.org/schema/reference/places/place/). **URL check:** all three official pages resolved; the overview and v2.0.0 Segment/Place field descriptions were fetched.

**Method and limits:** The Segment schema documents optional `routes[]` along with road `class` and feature `sources[]`; the Place schema defines `sources[]` and an optional confidence score for confidence that a place exists. A schema specifies field meaning and constraints, not that a route assignment is complete or that a POI is correct/current.

**Applicability (2 lines):** Directly informs `bias-est-transport-route-ref`, `bias-model-schema-missingness-candidate`, `bias-model-poi-confidence-weighting`, and `bias-model-cross-theme-support`. These fields may support provided-data features, but confidence and source membership are discovery/model covariates, never target labels.

### 12. Census TIGER/Line MTFCC codes

**Citation/documentation:** U.S. Census Bureau, [MAF/TIGER Feature Class Code Definitions](https://www.census.gov/library/reference/code-lists/mt-feature-class-codes.html). **URL check:** official Census page resolved.

**Method and limits:** The code list defines TIGER feature classes, including the S1100/S1200 road classes referenced by the organizer. It clarifies nomenclature only; it does not establish one-to-one semantic equivalence with Overture road classes, especially where regional primary/secondary conventions differ.

**Applicability (2 lines):** Informs `bias-est-transport-route-ref` and `bias-est-tx-fm-roads-sensitivity`, and interpretation in `bias-disc-strata-region-confound`. The page was consulted as documentation; no TIGER road geometry or removed reference values were obtained.

### 13. USDA Forest Service Wildfire Risk to Communities (2020)

**Citation/documentation:** Scott et al. (2020), *Wildfire Risk to Communities: Spatial datasets of landscape-wide wildfire risk components for the United States*, USDA Forest Service Research Data Archive, [RDS-2020-0016](https://doi.org/10.2737/RDS-2020-0016). **URL check:** DOI resolved to the official Forest Service Research Data Archive page; metadata fetched.

**Method and limits:** The archive describes modeled landscape-wide risk components with stated input vintage and warns that national products have uncertainty and are intended for relative-risk comparison, not as locally calibrated risk assessments. A risk surface or derived building-count field is not an independent current building census.

**Applicability (2 lines):** Informs `bias-disc-campfire-stale-reference` and the wildfire-context interpretation in `bias-doc-methodology-rubric`: provenance and vintage must accompany a contextual comparison. Only archive documentation was read; no USFS reference layer was downloaded for scoring.

### 14. U.S. Census County Business Patterns methodology

**Citation/documentation:** U.S. Census Bureau, [County Business Patterns Methodology](https://www.census.gov/programs-surveys/cbp/technical-documentation/methodology.html). **URL check:** official Census page resolved.

**Method and limits:** CBP is an establishment-statistics program organized by geography and industry, with disclosure protection and published aggregate counts. County/industry aggregates are not geocoded facility points and cannot identify which tract contains each establishment.

**Applicability (2 lines):** Informs `bias-est-establishments-half` and `bias-est-v2-assembly-probe`: a CBP-derived expected count must respect geography, suppression, and aggregation mismatch. The methodology page was reviewed, but no CBP counts or removed CBP layer were used.

### 15. Blum & Hardt (2015) — adaptive competition leaderboards

**Citation:** Avrim Blum and Moritz Hardt, “The Ladder: A Reliable Leaderboard for Machine Learning Competitions,” arXiv:1502.04585. [arXiv page](https://arxiv.org/abs/1502.04585). **URL check:** resolved; arXiv abstract fetched.

**Method and limits:** The paper formalizes leaderboard accuracy under sequential/adaptive submissions and proposes the Ladder mechanism with guarantees in its stated setting. Its mechanism is not the Bias Bounty platform’s evaluator and does not authorize repeated query-based model tuning.

**Applicability (2 lines):** Informs `bias-final-two-protocol` and `bias-lb-snapshot-and-scorecard`: repeated adaptive feedback can overfit a holdout, so preserve the model-first/no-information-probe policy. Evaluate only completed, locally validated candidates through the approved process.

### 16. Whitehill (2018) — extracting labels from a score oracle

**Citation:** Jacob Whitehill, “Climbing the Kaggle Leaderboard by Exploiting the Log-Loss Oracle,” arXiv:1707.01825. [arXiv page](https://arxiv.org/abs/1707.01825). **URL check:** resolved; arXiv abstract fetched.

**Method and limits:** The paper demonstrates that repeated, precise log-loss feedback can reveal test labels in a particular competition setup, and analyzes attacks under subset/precision assumptions. Its concrete attack is not identical to this RMSE task, but it illustrates the information channel created by adaptive oracle queries.

**Applicability (2 lines):** Informs `bias-final-two-protocol` and `bias-lb-snapshot-and-scorecard`: do not use submissions to estimate hidden means, subgroup scores, or per-tract targets. Use the leaderboard only to evaluate a finished model candidate, never for information probes.

## Transfer rules for this competition

1. **Keep completeness separate from quality.** Presence, positional accuracy, category correctness, source provenance, confidence, and update time are different properties. Without an independent reference, internal Overture fields are indicators or covariates—not proof of completeness.
2. **Do not transfer rates or coefficients.** Global OSM results, urban-agglomeration estimates, non-U.S. studies, and density relationships do not calibrate four U.S. study regions, Overture taxonomy, or the hidden challenge target.
3. **Respect vintage and denominator semantics.** Census/TIGER, Microsoft footprints, USGS, and CBP documentation may explain the organizer's published methodology, but the removed data and equivalent copies remain out of scored-candidate work. Official documentation alone does not supply a usable reference count.
4. **Keep score feedback non-adaptive.** The leaderboard-oracle papers support the lane's no-information-probe policy; they do not justify any upload other than evaluation of a completed, validated model.

No estimator coefficient, reference count, or external dataset from this review was added to a scored candidate. The literature is mapped to named backlog rows above as design and interpretation guidance only.
