from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.summarize_numerator_definition_screen import summarize

ROOT = Path(__file__).resolve().parents[1]


class NumeratorDefinitionScreenTests(unittest.TestCase):
    def test_measured_shares_and_null_gate(self) -> None:
        screen = json.loads((ROOT / 'reports/numerator-definition-screen.json').read_text())
        harness = json.loads((ROOT / 'reports/ideas-harness.json').read_text())
        report = summarize(screen, harness, 'a' * 64, 'b' * 64)
        self.assertEqual(screen['per_idea']['Sc1']['rows'], 9379)
        self.assertEqual(screen['per_idea']['Sc4']['rows'], 9379)
        self.assertTrue(report['summary']['Sc4_below_0_01_in_every_region'])
        self.assertFalse(report['summary']['Sc1_below_0_01_in_every_region'])
        self.assertFalse(report['summary']['joint_measured_null_condition_met'])
        self.assertFalse(report['summary']['registry_measured_null_update_applied'])
        self.assertGreater(report['regions']['eastern-ok']['sc1_unnamed_share_of_named_class_length'], 0.01)
        self.assertLess(report['regions']['maricopa-az']['sc4_unknown_km_relative_to_named_class_km'], 0.01)


if __name__ == '__main__':
    unittest.main()
