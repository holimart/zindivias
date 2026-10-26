"""Fixture and provided-input tests for demographic-prior ideas D1-D5."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bias60.ideas import IMPLEMENTATIONS  # noqa: E402
from bias60.inputs import ProvidedInputs  # noqa: E402

CONFIG = json.loads((ROOT / 'config/bias60-harness-preregistered.json').read_text())


class StubInputs:
    def __init__(self, idea_id: str, *, strata: dict[str, np.ndarray],
                 features: dict[str, np.ndarray] | None = None,
                 housing_units: np.ndarray | None = None) -> None:
        self.config = CONFIG
        self.region = 'eastern-ok'
        self.geoids = [f'400010000{i:02d}' for i in range(len(next(iter(strata.values()))))]
        self._strata = strata
        self._features = features or {}
        self._housing_units = housing_units
        self.idea_id = idea_id

    def strata(self, columns: list[str]) -> dict[str, np.ndarray]:
        return {column: self._strata[column] for column in columns}

    def features(self, columns: list[str]) -> dict[str, np.ndarray]:
        return {column: self._features[column] for column in columns}

    def acs_housing_units(self) -> np.ndarray:
        assert self._housing_units is not None
        return self._housing_units


class LocalFilesystem:
    def __init__(self, path: str) -> None:
        self.path = path

    def open_input_file(self, _uri: str):
        return pa.OSFile(self.path, 'rb')


class DemographicPriorTests(unittest.TestCase):
    def validate(self, idea_id: str, inputs: StubInputs):
        output = IMPLEMENTATIONS[idea_id](inputs)
        output.validate(config=CONFIG)
        self.assertEqual(len(output.geoids), len(inputs.geoids))
        self.assertEqual(len(output.emissions), 1)
        emission = output.emissions[0]
        self.assertEqual(emission.channel, 'covariate')
        self.assertTrue(np.isfinite(emission.values).all())
        self.assertTrue((emission.values[~emission.defined] == 0).all())
        return output, emission

    def test_d1_minority_rural_and_ruca_interaction(self) -> None:
        inputs = StubInputs('D1', strata={
            'svi_minority': np.array([0.2, 0.8, 0.6, 0.5]),
            'pct_urban': np.array([0.0, 0.0, 0.5, 0.5]),
            'ruca_primary': np.array([1.0, 4.0, 8.0, 99.0]),
            'svi_covered': np.array([True, True, True, True]),
            'ruca_covered': np.array([True, True, True, True]),
        })
        output, emission = self.validate('D1', inputs)
        self.assertEqual(emission.component, 'Est')
        np.testing.assert_array_equal(emission.defined, [True, True, True, False])
        self.assertAlmostEqual(float(emission.values[:3].mean()), 0.0, places=12)
        self.assertEqual(output.diagnostics['ruca_ge_4_rows'], 2)

    def test_d2_tribal_legal_share_interacts_with_zero_fire_or_ems(self) -> None:
        inputs = StubInputs('D2', strata={
            'tribal_legal_pct': np.array([0.2, 0.8, 0.5, 0.4]),
            'tribal_legal': np.array([True, True, False, True]),
        }, features={
            'overture_poi_fire': np.array([0.0, 1.0, 0.0, 1.0]),
            'overture_poi_ems': np.array([1.0, 0.0, 0.0, 1.0]),
        })
        output, emission = self.validate('D2', inputs)
        self.assertEqual(emission.component, 'F')
        np.testing.assert_allclose(emission.values, [0.2, 0.8, 0.0, 0.2])
        self.assertEqual(output.diagnostics['zero_fire_or_ems_rows'], 3)
        self.assertEqual(output.diagnostics['unavailable_spec_fields'], ['aiannh_kind', 'in_tribal_tract'])

    def test_d3_log_housing_per_building_standardized_within_ruca(self) -> None:
        inputs = StubInputs('D3', housing_units=np.array([200.0, 100.0, 100.0, 100.0]),
                            strata={'ruca_primary': np.array([1.0, 1.0, 4.0, 99.0])},
                            features={'overture_buildings': np.array([100.0, 100.0, 50.0, 10.0])})
        _output, emission = self.validate('D3', inputs)
        self.assertEqual(emission.component, 'B')
        np.testing.assert_array_equal(emission.defined, [True, True, True, False])
        np.testing.assert_allclose(emission.values[:2], [1.0, -1.0], atol=1e-12)
        self.assertEqual(emission.values[2], 0.0)  # single-row RUCA group is defined, zero-spread

    def test_d4_population_per_housing_unit_is_region_standardized(self) -> None:
        inputs = StubInputs('D4', housing_units=np.array([100.0, 100.0, 0.0, 100.0]),
                            strata={'pop_total': np.array([100.0, 50.0, 20.0, 0.0])})
        _output, emission = self.validate('D4', inputs)
        self.assertEqual(emission.component, 'Est')
        np.testing.assert_array_equal(emission.defined, [True, True, False, True])
        self.assertAlmostEqual(float(emission.values[emission.defined].mean()), 0.0, places=12)

    def test_d5_requires_covered_cvi_with_xwalk_at_least_09(self) -> None:
        inputs = StubInputs('D5', strata={
            'cvi_baseline_infrastructure': np.array([0.2, 0.4, 0.8, 0.4]),
            'cvi_baseline_socioeconomic': np.array([0.3, 0.5, 0.7, 0.5]),
            'cvi_xwalk_coverage': np.array([1.0, 0.9, 0.89, 1.0]),
            'cvi_covered': np.array([True, True, True, False]),
        })
        output, emission = self.validate('D5', inputs)
        self.assertEqual(emission.component, 'Est')
        np.testing.assert_array_equal(emission.defined, [True, True, False, False])
        self.assertEqual(output.diagnostics['minimum_cvi_xwalk_coverage'], 0.9)

    def test_provided_acs_housing_accessor_aligns_ids_and_logs_only_declared_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'acs.parquet'
            pq.write_table(pa.table({'GEOID': ['001', '002', '003'],
                                     'housing_units': [11, 22, 33],
                                     'unused': ['x', 'y', 'z']}), path)
            config = {
                'forbidden': {'path_tokens': ['tiger', 'usgs_facilit', 'cbp']},
                'inputs': {
                    'package_bucket': 'provided-test-bucket',
                    'package_polygon_path_template': '{bucket}/reference/{region}/{region}-{layer}.parquet',
                    'polygon_layer': 'census-acs-housing',
                },
            }
            inputs = ProvidedInputs(config, Path(tmp), 'test-region', ['003', '001'],
                                    filesystem=LocalFilesystem(str(path)))
            np.testing.assert_array_equal(inputs.acs_housing_units(), [33.0, 11.0])
            self.assertEqual(inputs.accessed['parquet_columns'],
                             ['test-region-census-acs-housing.parquet:GEOID,housing_units'])
            self.assertEqual(inputs.accessed['layers'], ['census-acs-housing'])


if __name__ == '__main__':
    unittest.main()
