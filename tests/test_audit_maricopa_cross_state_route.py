from __future__ import annotations

import importlib.util
import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'audit_maricopa_cross_state_route', ROOT / 'scripts/audit_maricopa_cross_state_route.py')
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MaricopaCrossStateRouteAuditTests(unittest.TestCase):
    def test_leave_one_out_uses_aggregate_ratio_and_both_lengths(self) -> None:
        rows = [
            {'GEOID': 'AZ-A', module.NAMED_COLUMN: 1.0, module.ROUTE_COLUMN: 2.0},
            {'GEOID': module.EXCLUDED_GEOID, module.NAMED_COLUMN: 4.0, module.ROUTE_COLUMN: 2.0},
            {'GEOID': 'AZ-B', module.NAMED_COLUMN: 3.0, module.ROUTE_COLUMN: 6.0},
        ]
        result = module.ratio_sensitivity(rows, module.EXCLUDED_GEOID)
        self.assertEqual(result['with_tract']['L_named_km'], 8.0)
        self.assertEqual(result['with_tract']['L_route_km'], 10.0)
        self.assertEqual(result['with_tract']['sum_L_named_over_sum_L_route'], 0.8)
        self.assertEqual(result['excluded_tract']['L_named_km'], 4.0)
        self.assertEqual(result['excluded_tract']['L_route_km'], 2.0)
        self.assertEqual(result['without_tract']['sum_L_named_over_sum_L_route'], 0.5)
        self.assertTrue(math.isclose(result['absolute_ratio_delta'], 0.3))

    def test_duplicate_or_missing_excluded_geoid_fails_closed(self) -> None:
        rows = [{'GEOID': 'A', module.NAMED_COLUMN: 1.0, module.ROUTE_COLUMN: 2.0}]
        with self.assertRaisesRegex(ValueError, 'absent'):
            module.ratio_sensitivity(rows, module.EXCLUDED_GEOID)
        duplicated = rows + [dict(rows[0])]
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            module.ratio_sensitivity(duplicated, 'A')

    def test_committed_aggregate_report_reconstructs_frozen_totals(self) -> None:
        report = json.loads((ROOT / 'reports/maricopa-cross-state-route-influence.json').read_text())
        accounting = report['accounting']
        verification = report['verification']
        self.assertEqual(accounting['official_sample_rows'], 1593)
        self.assertEqual(accounting['official_sample_unique_geoids'], 1593)
        self.assertEqual(accounting['excluded_tract']['GEOID'], module.EXCLUDED_GEOID)
        self.assertTrue(verification['totals_reconstructed_within_1e_8_km'])
        self.assertEqual(verification['reconstructed_L_named_absolute_error_km'], 0.0)
        self.assertEqual(verification['reconstructed_L_route_absolute_error_km'], 0.0)
        self.assertAlmostEqual(accounting['absolute_ratio_delta'], 0.038730577684854106, places=14)
        self.assertTrue(report['data_use']['candidate_or_submission_csv_written_or_modified'] is False)
        self.assertTrue(all(item['unchanged'] and item['sha256_before'] == item['sha256_after']
                            for item in report['protected_artifact_hashes'].values()))
        self.assertEqual(report['method']['per_tract_output_written'], False)
        self.assertFalse(report['data_use']['removed_reference_layers_or_equivalents_read'])


if __name__ == '__main__':
    unittest.main()
