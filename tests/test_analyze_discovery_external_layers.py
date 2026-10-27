import importlib.util
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze_discovery_external_layers.py"
spec = importlib.util.spec_from_file_location("external_layers", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_bh_adjustment_is_monotone_and_bounded():
    q = module.bh_adjust([0.01, 0.04, 0.03, 0.002])
    assert np.all((q >= 0) & (q <= 1))
    assert q[3] <= q[0] <= q[2] <= q[1]


def test_partial_slope_detects_standardized_linear_association():
    rng = np.random.default_rng(12)
    n = 120
    regions = np.asarray([module.REGIONS[i % 4] for i in range(n)])
    control = rng.normal(size=(n, 1))
    exposure = rng.normal(size=n)
    y = 0.7 * exposure + 0.2 * control[:, 0] + rng.normal(scale=0.2, size=n)
    y = (y - y.mean()) / y.std()
    X = module.design(control, regions, exposure, np.arange(n), True)
    beta, se, p = module.fit_beta(X, y, np.asarray([f"{r}|{i // 8}" for i, r in enumerate(regions)]))
    assert beta[-1] > 0.5
    assert se > 0
    assert p < 0.01


def test_grouped_county_holdout_is_deterministic_and_reports_delta():
    rng = np.random.default_rng(15)
    regions = np.asarray([r for r in module.REGIONS for _ in range(60)])
    controls = rng.normal(size=(len(regions), 2))
    exposure = rng.normal(size=len(regions))
    counties = np.asarray([f"{r}|{i // 10:03d}" for r in module.REGIONS for i in range(60)])
    y = 0.5 * exposure + controls[:, 0] + rng.normal(scale=0.2, size=len(regions))
    a = module.grouped_oof_delta_r2(controls, regions, exposure, y, counties)
    b = module.grouped_oof_delta_r2(controls, regions, exposure, y, counties)
    assert a == b
    assert a["folds"] == 5
    assert a["group_unit"] == "region|county"
    assert a["delta_oof_r2"] > 0


def test_county_bootstrap_partial_correlation_reproducible():
    rng = np.random.default_rng(17)
    regions = np.asarray([r for r in module.REGIONS for _ in range(40)])
    controls = rng.normal(size=(len(regions), 1))
    exposure = rng.normal(size=len(regions))
    y = 0.4 * exposure + rng.normal(size=len(regions))
    counties = np.asarray([f"{r}|{i // 5:03d}" for r in module.REGIONS for i in range(40)])
    X0 = module.design(controls, regions, exposure, np.arange(len(regions)), False)
    a = module.county_boot_partial_ci(X0, exposure, y, regions, counties, reps=100, seed=19)
    b = module.county_boot_partial_ci(X0, exposure, y, regions, counties, reps=100, seed=19)
    assert a == b
    ci, p = a
    assert len(ci) == 2 and ci[0] <= ci[1]
    assert 0.0 <= p <= 1.0
    assert np.isfinite(ci).all()


def test_exactly_redundant_exposure_is_not_estimable():
    rng = np.random.default_rng(23)
    n = 160
    regions = np.asarray([module.REGIONS[i % 4] for i in range(n)])
    exposure = rng.normal(size=n)
    controls = np.column_stack([exposure, rng.normal(size=n)])
    y = rng.normal(size=n)
    counties = np.asarray([f"{r}|{i // 8:03d}" for i, r in enumerate(regions)])
    X0 = module.design(controls, regions, exposure, np.arange(n), False)
    effect, se, p, ratio = module.partial_residual_correlation(X0, exposure, y, counties)
    assert effect is None
    assert se is None
    assert p == 1.0
    assert ratio < 1e-10
