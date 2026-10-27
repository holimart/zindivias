from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

h2 = load("audit_shrink_diagnosis_h2", ROOT / "scripts/audit_shrink_diagnosis_h2.py")
reg = load("register_shrink_diagnosis_candidate", ROOT / "scripts/register_shrink_diagnosis_candidate.py")


def test_cross_construction_covariance_gives_signal_share_and_reliability_weights():
    x = __import__("numpy").array([[0.0, 0.1, 0.2], [1.0, 0.9, 0.8], [0.5, 0.6, 0.4]])
    cov, variances, shares = h2.covariance_and_shares(x)
    assert cov.shape == (3, 3)
    assert (variances > 0).all()
    assert ((shares >= 0) & (shares <= 1)).all()
    weights = h2.reliability_weights(shares)
    assert abs(float(weights.sum()) - 1.0) < 1e-12
    assert (weights >= 0).all()


def test_signal_share_is_cross_covariance_over_total_consensus_variance():
    np = __import__("numpy")
    cov = np.array([[1.0, 0.5], [0.5, 1.0]])
    signal, total, c = h2.signal_total_c(cov, np.array([0.5, 0.5]))
    assert signal == 0.5
    assert total == 0.75
    assert abs(c - (2.0 / 3.0)) < 1e-12


def test_candidate_registry_array_scanner_ignores_brackets_inside_strings():
    text = '{"candidate_set": [{"candidate_id":"A","x":"[ { ]"}], "other": [1]}'
    end = reg.array_end(text, "candidate_set")
    assert text[:end + 1].endswith('"[ { ]"}]')
    assert text[end] == "]"


def test_zero_reliability_falls_back_to_equal_weights():
    np = __import__("numpy")
    assert (h2.reliability_weights(np.zeros(4)) == 0.25).all()
