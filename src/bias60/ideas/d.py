"""D1-D5 demographic and social covariates from challenge-provided tract data only."""
from __future__ import annotations

import numpy as np

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.ideas._common import as_coverage, as_float, quantiles


def _zscore(values: np.ndarray, defined: np.ndarray, groups: np.ndarray | None = None
            ) -> tuple[np.ndarray, dict[str, int]]:
    """Population-SD z-scores within declared groups; constant groups map to defined zero."""
    output = np.zeros(values.shape, dtype=np.float64)
    if groups is None:
        groups = np.zeros(values.shape, dtype=np.int64)
    group_labels = sorted({str(value) for value in groups[defined]})
    counts: dict[str, int] = {}
    for label in group_labels:
        mask = defined & (groups.astype(str) == label)
        sample = values[mask]
        if not sample.size:
            continue
        deviation = float(np.std(sample, ddof=0))
        output[mask] = ((sample - float(np.mean(sample))) / deviation
                        if deviation > 0.0 else 0.0)
        counts[label] = int(sample.size)
    if not np.isfinite(output[defined]).all():
        raise ValueError("demographic z-score produced a non-finite defined value")
    return output, counts


def _covariate_output(idea_id: str, region: str, geoids: list[str], component: str,
                      values: np.ndarray, defined: np.ndarray,
                      diagnostics: dict[str, object]) -> IdeaOutput:
    if values.dtype != np.float64 or defined.dtype != np.bool_:
        raise TypeError(f"{idea_id}: expected float64 values and boolean defined mask")
    values = values.copy()
    values[~defined] = 0.0
    diagnostics = {"rows": len(geoids), "defined": int(defined.sum()),
                   "undefined": int((~defined).sum()),
                   "coverage_share": float(defined.mean()) if defined.size else 0.0,
                   "covariate_distribution": quantiles(values[defined]), **diagnostics}
    return IdeaOutput(
        idea_id=idea_id, region=region, geoids=list(geoids),
        emissions=[Emission(component=component, channel="covariate", values=values,
                            defined=defined, metadata={"interpretation": "label-free provided-data covariate; not an organizer-target estimate"})],
        diagnostics=diagnostics)


@implements("D1")
def d1(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["D1"]
    columns = [seed["svi_column"], seed["urban_share_column"], seed["ruca_column"],
               seed["svi_coverage_column"], seed["ruca_coverage_column"]]
    table = inputs.strata(columns)
    minority = as_float(table[seed["svi_column"]])
    urban = as_float(table[seed["urban_share_column"]])
    ruca = as_float(table[seed["ruca_column"]])
    covered = (as_coverage(table[seed["svi_coverage_column"]])
               & as_coverage(table[seed["ruca_coverage_column"]]))
    valid_codes = np.isfinite(ruca) & (ruca >= 1.0) & (ruca <= 10.0)
    defined = (covered & valid_codes & np.isfinite(minority) & (minority >= 0.0)
               & (minority <= 1.0) & np.isfinite(urban) & (urban >= 0.0) & (urban <= 1.0))
    base = np.zeros(minority.shape, dtype=np.float64)
    base[defined] = minority[defined] * (1.0 - urban[defined])
    ruca4 = defined & (ruca >= float(seed["ruca_threshold"]))
    # Average the preregistered base signal and its RUCA>=4 interaction, then standardize
    # within this region so regional composition is not itself the D1 signal.
    combined = np.zeros(minority.shape, dtype=np.float64)
    combined[defined] = 0.5 * (base[defined] + base[defined] * ruca4[defined])
    values, groups = _zscore(combined, defined)
    return _covariate_output("D1", inputs.region, inputs.geoids, "Est", values, defined, {
        "recipe": "within-region z-score of 0.5*(svi_minority*(1-pct_urban) + that signal*1[ruca_primary>=4])",
        "ruca_ge_4_rows": int(ruca4.sum()), "zscore_group_sizes": groups,
        "no_fitted_parameter": True,
    })


@implements("D2")
def d2(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["D2"]
    table = inputs.strata([seed["legal_share_column"], seed["legal_flag_column"]])
    features = inputs.features([seed["fire_count_column"], seed["ems_count_column"]])
    legal_share = as_float(table[seed["legal_share_column"]])
    legal_flag = as_coverage(table[seed["legal_flag_column"]])
    fire = as_float(features[seed["fire_count_column"]])
    ems = as_float(features[seed["ems_count_column"]])
    valid = (np.isfinite(legal_share) & (legal_share >= 0.0) & (legal_share <= 1.0)
             & np.isfinite(fire) & (fire >= 0.0) & np.isfinite(ems) & (ems >= 0.0))
    zero_service = (fire <= 0.0) | (ems <= 0.0)
    legal_share = np.where(legal_flag, legal_share, 0.0)
    values = np.zeros(legal_share.shape, dtype=np.float64)
    # Legal-land share is the main institutional prior; a missing either fire or EMS Overture
    # category activates its preregistered interaction. No USGS/HIFLD/reference facility count.
    values[valid] = legal_share[valid] * (0.5 + 0.5 * zero_service[valid])
    return _covariate_output("D2", inputs.region, inputs.geoids, "F", values, valid, {
        "recipe": "tribal_legal_pct*(0.5 + 0.5*1[overture_poi_fire==0 or overture_poi_ems==0])",
        "legal_flag_rows": int(legal_flag.sum()),
        "legal_land_rows": int((legal_flag & valid).sum()),
        "zero_fire_or_ems_rows": int((zero_service & valid).sum()),
        "unavailable_spec_fields": ["aiannh_kind", "in_tribal_tract"],
        "substitution_note": "Used the merged challenge-provided tribal_legal and tribal_legal_pct columns; aiannh_kind/in_tribal_tract are absent from the harness strata cache and no external boundary layer was opened.",
        "no_reference_facility_counts": True,
        "no_fitted_parameter": True,
    })


@implements("D3")
def d3(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["D3"]
    housing = as_float(inputs.acs_housing_units())
    buildings = as_float(inputs.features([seed["building_count_column"]])
                         [seed["building_count_column"]])
    ruca = as_float(inputs.strata([seed["ruca_column"]])[seed["ruca_column"]])
    valid_ruca = np.isfinite(ruca) & (ruca >= 1.0) & (ruca <= 10.0)
    defined = (np.isfinite(housing) & (housing > 0.0) & np.isfinite(buildings)
               & (buildings > 0.0) & valid_ruca)
    log_ratio = np.zeros(housing.shape, dtype=np.float64)
    log_ratio[defined] = np.log(housing[defined] / buildings[defined])
    groups = ruca.astype(str)
    values, group_sizes = _zscore(log_ratio, defined, groups)
    return _covariate_output("D3", inputs.region, inputs.geoids, "B", values, defined, {
        "recipe": "within-region x RUCA z-score of ln(provided ACS housing_units / provided Overture building count)",
        "zscore_group_sizes": group_sizes,
        "undefined_reasons": {
            "housing_units_not_positive_or_missing": int((~np.isfinite(housing) | (housing <= 0)).sum()),
            "overture_buildings_not_positive_or_missing": int((~np.isfinite(buildings) | (buildings <= 0)).sum()),
            "ruca_outside_1_to_10_or_missing": int((~valid_ruca).sum()),
        },
        "housing_units_role": "demographic covariate only; never a denominator floor or replacement for a removed structure reference",
        "no_fitted_parameter": True,
    })


@implements("D4")
def d4(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["D4"]
    population = as_float(inputs.strata([seed["population_column"]])[seed["population_column"]])
    housing = as_float(inputs.acs_housing_units())
    defined = np.isfinite(population) & (population >= 0.0) & np.isfinite(housing) & (housing > 0.0)
    ratio = np.zeros(population.shape, dtype=np.float64)
    ratio[defined] = population[defined] / housing[defined]
    values, groups = _zscore(ratio, defined)
    return _covariate_output("D4", inputs.region, inputs.geoids, "Est", values, defined, {
        "recipe": "within-region z-score of provided pop_total / provided ACS housing_units",
        "zscore_group_sizes": groups,
        "undefined_reasons": {
            "population_missing_or_negative": int((~np.isfinite(population) | (population < 0)).sum()),
            "housing_units_not_positive_or_missing": int((~np.isfinite(housing) | (housing <= 0)).sum()),
        },
        "housing_units_role": "demographic covariate only; not a target denominator or gap estimate",
        "no_fitted_parameter": True,
    })


@implements("D5")
def d5(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["D5"]
    columns = [seed["infrastructure_column"], seed["socioeconomic_column"],
               seed["xwalk_coverage_column"], seed["coverage_column"]]
    table = inputs.strata(columns)
    infrastructure = as_float(table[seed["infrastructure_column"]])
    socioeconomic = as_float(table[seed["socioeconomic_column"]])
    xwalk = as_float(table[seed["xwalk_coverage_column"]])
    covered = as_coverage(table[seed["coverage_column"]])
    defined = (covered & np.isfinite(xwalk) & (xwalk >= float(seed["minimum_xwalk_coverage"]))
               & np.isfinite(infrastructure) & (infrastructure >= 0.0) & (infrastructure <= 1.0)
               & np.isfinite(socioeconomic) & (socioeconomic >= 0.0) & (socioeconomic <= 1.0))
    infra_z, infra_groups = _zscore(infrastructure, defined)
    socio_z, socio_groups = _zscore(socioeconomic, defined)
    values = np.zeros(infrastructure.shape, dtype=np.float64)
    values[defined] = 0.5 * (infra_z[defined] + socio_z[defined])
    return _covariate_output("D5", inputs.region, inputs.geoids, "Est", values, defined, {
        "recipe": "average of within-region z-scores of CVI baseline infrastructure and socioeconomic domains",
        "minimum_cvi_xwalk_coverage": float(seed["minimum_xwalk_coverage"]),
        "xwalk_coverage_min_observed": float(np.min(xwalk[np.isfinite(xwalk)]))
        if np.isfinite(xwalk).any() else None,
        "infrastructure_zscore_group_sizes": infra_groups,
        "socioeconomic_zscore_group_sizes": socio_groups,
        "no_fitted_parameter": True,
    })
