from __future__ import annotations

import importlib.util
import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'audit_v2_intersections', ROOT / 'scripts/audit_v2_intersections.py')
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class V2IntersectionAuditTests(unittest.TestCase):
    def test_standardized_mean_difference_uses_pooled_sample_sd(self) -> None:
        a = {'n': 3, 'mean_score': 1.0, 'sample_sd_score': 1.0}
        b = {'n': 3, 'mean_score': 2.0, 'sample_sd_score': 1.0}
        self.assertEqual(module.standardized_mean_difference(a, b), 1.0)

    def test_cells_are_partitioned_and_small_cells_are_suppressed(self) -> None:
        pairs = [(f'{i:011d}', 'maricopa-az') for i in range(30)]
        pairs += [(f'{i + 30:011d}', 'eastern-ok') for i in range(30)]
        pairs += [('00000000060', 'northern-ca')]
        scores = {geoid: (i % 10) / 10 for i, (geoid, _) in enumerate(pairs)}
        labels = {
            geoid: {'toy': ('a' if index < 30 else 'b' if index < 60 else 'rare')}
            for index, (geoid, _) in enumerate(pairs)
        }
        family = {'id': 'toy', 'axes': ['region', 'toy']}
        result, buckets = module.make_cells(family, pairs, scores, labels)
        self.assertEqual(sum(map(len, buckets.values())), len(pairs))
        self.assertEqual(result['observed_cells'], 3)
        self.assertEqual(result['eligible_cell_count'], 2)
        self.assertEqual(result['suppressed_cell_count'], 1)
        self.assertEqual(result['pairwise_comparisons'], 1)
        self.assertEqual(result['suppressed_cells'][0]['n'], 1)
        self.assertTrue(math.isfinite(result['maximum_absolute_smd']['absolute_smd']))

    def test_report_accounts_for_all_scored_ids_and_cell_dispositions(self) -> None:
        report = json.loads((ROOT / 'reports/v2-intersectional-descriptive-audit.json').read_text())
        self.assertTrue(all(report['acceptance'].values()))
        self.assertEqual(report['candidate']['official_rows'], 9379)
        self.assertTrue(0 <= report['candidate']['score_min'] <= report['candidate']['score_max'] <= 1)
        self.assertEqual(report['accounting']['zero_defined_component_geoids'], 0)
        self.assertEqual(len(report['intersection_families']), 4)
        for family in report['intersection_families']:
            self.assertTrue(family['observed_cell_partition_exact_once'])
            self.assertEqual(
                sum(cell['n'] for cell in family['eligible_cells'])
                + sum(cell['n'] for cell in family['suppressed_cells']), 9379)
            self.assertEqual(family['observed_cells'],
                             family['eligible_cell_count'] + family['suppressed_cell_count'])
            self.assertTrue(all(cell['n'] >= 30 for cell in family['eligible_cells']))
            self.assertTrue(all(0 < cell['n'] < 30 for cell in family['suppressed_cells']))
            self.assertEqual(family['pairwise_comparisons'],
                             family['eligible_cell_count'] * (family['eligible_cell_count'] - 1) // 2)
        self.assertEqual(report['method']['cells_total_observed'], 1991)
        self.assertEqual(report['method']['cells_total_eligible'], 293)
        self.assertEqual(report['method']['cells_total_suppressed'], 1698)
        self.assertEqual(report['method']['overall_maximum_absolute_smd']['absolute_smd'],
                         max(item['maximum_absolute_smd']['absolute_smd']
                             for item in report['intersection_families']))
        self.assertFalse(report['data_use']['organizer_targets_or_labels_used'])
        self.assertFalse(report['data_use']['leaderboard_values_used'])
        self.assertFalse(report['data_use']['removed_reference_layers_or_equivalents_used'])
        self.assertFalse(report['data_use']['external_data_used'])
        self.assertFalse(report['data_use']['per_cell_fitting_or_tuning_performed'])


if __name__ == '__main__':
    unittest.main()
