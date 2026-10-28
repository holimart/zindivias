import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_s2_score_invariance.py"
SPEC = importlib.util.spec_from_file_location("s2_score_invariance", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class S2ScoreInvarianceTests(unittest.TestCase):
    def test_independent_toy_matches_hand_computation(self):
        result = MODULE.toy_hand_check()
        self.assertTrue(result["matches"])
        self.assertEqual(result["hand_calculated_score"], 0.45)
        self.assertAlmostEqual(result["score"], 0.45, places=12)
        self.assertAlmostEqual(result["components"]["poi"], 0.55, places=12)

    def test_row_and_column_permutations_preserve_keyed_records(self):
        rows = {"a": {"left": 1.0, "right": 2.0}, "b": {"left": 3.0, "right": 4.0}}
        row_perm = MODULE.reverse_rows(rows, ["a", "b"])
        col_perm = MODULE.reverse_columns(rows)
        self.assertEqual(set(row_perm), set(rows))
        self.assertEqual(row_perm["a"], rows["a"])
        self.assertEqual(col_perm["b"]["left"], rows["b"]["left"])
        self.assertEqual(col_perm["b"]["right"], rows["b"]["right"])

    def test_canonical_serialization_is_lossless(self):
        data = {"GEOID": "04001944902", "feature": 0.12345678901234567, "missing": None}
        self.assertEqual(MODULE.stable_round_trip(data), data)

    def test_exact_id_contract_rejects_duplicates_and_missing(self):
        expected = {"a", "b"}
        self.assertEqual(MODULE.exact_id_index(["a", "b"], expected, "fixture"), {"a": 0, "b": 1})
        with self.assertRaises(ValueError):
            MODULE.exact_id_index(["a", "a"], expected, "fixture")
        with self.assertRaises(ValueError):
            MODULE.exact_id_index(["a"], expected, "fixture")

    def test_score_delta_is_id_aligned(self):
        a = {"a": {"score": 0.1}, "b": {"score": 0.9}}
        b = {"b": {"score": 0.9}, "a": {"score": 0.1}}
        self.assertEqual(MODULE.max_score_delta(a, b, {"a", "b"}), 0.0)


if __name__ == "__main__":
    unittest.main()
