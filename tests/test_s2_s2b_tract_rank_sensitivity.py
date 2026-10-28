import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_s2_s2b_tract_rank_sensitivity.py"
SPEC = importlib.util.spec_from_file_location("s2_s2b_tract_rank_sensitivity", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class S2S2bTractRankSensitivityTests(unittest.TestCase):
    def test_spearman_uses_average_ranks_for_ties(self):
        self.assertAlmostEqual(MODULE.spearman([1, 1, 2, 3], [4, 4, 5, 6]), 1.0)
        self.assertAlmostEqual(MODULE.spearman([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertIsNone(MODULE.spearman([1, 1], [2, 3]))

    def test_top_decile_is_exact_size_and_tie_breaks_by_geoid(self):
        ids = ["c", "b", "a", "d", "e", "f", "g", "h", "i", "j", "k"]
        scores = [1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        # ceil(10% * 11) = 2; among tied maxima ascending GEOID wins.
        self.assertEqual(MODULE.top_decile(ids, scores), {"a", "b"})

    def test_region_summary_reports_order_and_level_metrics(self):
        ids = ["a", "b", "c", "d"]
        result = MODULE.summarize_region(ids, [0.1, 0.2, 0.3, 0.4], [0.2, 0.3, 0.4, 0.5])
        self.assertAlmostEqual(result["spearman_s2_vs_s2b"], 1.0)
        self.assertEqual(result["top_decile_size_each"], 1)
        self.assertEqual(result["top_decile_overlap_fraction_of_k"], 1.0)
        self.assertAlmostEqual(result["median_absolute_score_delta"], 0.1)
        self.assertAlmostEqual(result["maximum_absolute_score_delta"], 0.1)

    def test_official_scores_are_parsed_but_extra_score_values_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate.csv"
            path.write_text(
                "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n"
                "00000000001,0,0,0,0.25\n"
                "00000000002,0,0,0,0.75\n"
                "99999999999,not-a-score,not-a-score,not-a-score,not-a-score\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            scores, rows = MODULE.read_candidate_scores(path, digest, {"00000000001", "00000000002"}, 3)
            self.assertEqual(rows, 3)
            self.assertEqual(scores, {"00000000001": 0.25, "00000000002": 0.75})

    def test_linear_quantile_interpolates(self):
        self.assertEqual(MODULE.linear_quantile([0.0, 1.0], 0.25), 0.25)


if __name__ == "__main__":
    unittest.main()
