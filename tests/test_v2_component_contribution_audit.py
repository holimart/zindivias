from __future__ import annotations

import importlib.util
import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'audit_v2_component_contributions', ROOT / 'scripts/audit_v2_component_contributions.py')
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class V2ComponentContributionTests(unittest.TestCase):
    def test_leave_one_out_recomputes_only_over_remaining_defined_components(self) -> None:
        values = {'transport': 0.8, 'building': 0.2, 'poi': 0.6}
        flags = {'transport': 1, 'building': 0, 'poi': 1}
        self.assertEqual(module.defined_component_mean(values, flags, 'transport'), 0.6)
        self.assertEqual(module.defined_component_mean(values, flags, 'building'), 0.7)
        self.assertIsNone(module.defined_component_mean(values, {'transport': 0, 'building': 0,
                                                                 'poi': 1}, 'poi'))

    def test_nearest_rank_quantiles_and_bounds(self) -> None:
        stats = module.delta_stats([0.0, 0.1, 0.2, 0.3])
        self.assertEqual(stats['n'], 4)
        self.assertEqual(stats['mean_abs_delta'], 0.15)
        self.assertEqual(stats['max_abs_delta'], 0.3)
        self.assertEqual(stats['quantiles_abs_delta']['p50'], 0.1)
        with self.assertRaisesRegex(ValueError, r'finite and in \[0, 1\]'):
            module.delta_stats([1.01])

    def test_audit_report_covers_every_official_geoid_per_variant(self) -> None:
        report = json.loads((ROOT / 'reports/v2-component-contribution-audit.json').read_text())
        self.assertTrue(all(report['acceptance'].values()))
        self.assertEqual(report['candidate']['sha256'], report['candidate']['sha256_manifest'])
        self.assertTrue(report['baseline_reconstruction']['within_1e_11'])
        self.assertEqual(report['baseline_reconstruction']['max_abs_composite_reconstruction_residual'], 0.0)
        self.assertEqual(len(report['per_geoid_deltas']), 9379)
        geoids = [row['GEOID'] for row in report['per_geoid_deltas']]
        self.assertEqual(len(set(geoids)), 9379)
        for component in module.COMPONENTS:
            variant = report['variant_accounting'][component]
            self.assertEqual(variant['official_rows'], 9379)
            self.assertEqual(variant['unique_geoids'], 9379)
            self.assertTrue(variant['partition_exact_once'])
            summary = report['component_summaries'][component]['overall']
            self.assertEqual(summary['geoids'], 9379)
            self.assertEqual(summary['n'], variant['loo_defined_rows'])
            self.assertTrue(all(region['defined_rows'] + region['undefined_rows'] == region['rows']
                                for region in report['definedness']['by_region'][component].values()))
            for stratum in report['component_summaries'][component]['by_provided_strata']:
                self.assertEqual(stratum['partition_geoids'], 9379)
        self.assertEqual(report['variant_accounting']['poi']['loo_undefined_rows'], 2)
        self.assertFalse(report['data_use']['sample_score_placeholders_read'])
        self.assertFalse(report['data_use']['labels_or_leaderboard_values_used'])
        self.assertFalse(report['data_use']['removed_reference_layers_or_equivalents_read'])
        self.assertTrue(all(math.isfinite(row[component]['absolute_delta'])
                            for row in report['per_geoid_deltas'] for component in module.COMPONENTS
                            if row[component]['absolute_delta'] is not None))


if __name__ == '__main__':
    unittest.main()
