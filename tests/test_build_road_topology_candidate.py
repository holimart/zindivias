import csv
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_road_topology_candidate import (  # noqa: E402
    CONFIG,
    MAX_ADJUSTMENT,
    parse_connectors,
    summarize_local_graph,
)


class RoadTopologyCandidateTests(unittest.TestCase):
    def test_connector_schema_validation_and_order(self):
        connectors, positions, error = parse_connectors([
            {"connector_id": "b", "at": 1.0},
            {"connector_id": "a", "at": 0.0},
            {"connector_id": "c", "at": 0.5},
        ])
        self.assertIsNone(error)
        self.assertEqual(connectors, ["a", "c", "b"])
        self.assertEqual(positions, [0.0, 0.5, 1.0])
        self.assertEqual(parse_connectors(None)[2], "missing_connector_list")
        self.assertEqual(parse_connectors([{"connector_id": "x", "at": 0.0}])[2],
                         "fewer_than_two_connectors")
        self.assertEqual(parse_connectors([
            {"connector_id": "x", "at": 0.0}, {"connector_id": "x", "at": 1.0}
        ])[2], "duplicate_connector_id")
        self.assertEqual(parse_connectors([
            {"connector_id": "x", "at": 0.0}, {"connector_id": "y", "at": 1.1}
        ])[2], "at_outside_0_1")

    def test_local_simple_graph_metrics_include_isolates_and_deduplicate_edges(self):
        graph = summarize_local_graph(
            [np.asarray([0, 1, 2, 3, 4, 5], dtype=np.int64)],
            [np.asarray([[0, 1], [1, 2], [3, 4], [3, 4]], dtype=np.int64)],
            cross_edges=2,
        )
        self.assertEqual(graph["connector_nodes"], 6)
        self.assertEqual(graph["raw_local_adjacencies"], 4)
        self.assertEqual(graph["unique_local_edges"], 3)
        self.assertEqual(graph["duplicate_adjacencies_collapsed"], 1)
        self.assertEqual(graph["connected_components"], 3)
        self.assertEqual(graph["isolated_components_singleton"], 1)
        self.assertEqual(graph["isolated_connector_nodes_degree_zero"], 1)
        self.assertEqual(graph["dead_end_nodes_degree_one"], 4)
        self.assertEqual(graph["cross_tract_adjacencies_touching_tract"], 2)
        self.assertAlmostEqual(graph["largest_component_node_share"], 0.5)
        self.assertAlmostEqual(graph["fragmentation"], 0.5)

    def test_generated_candidate_contract_and_hashes(self):
        report_path = REPO / "reports/road-topology-candidate.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        after = report["measure_after"]
        candidate_path = REPO / report["candidate_csv"]
        manifest_path = REPO / report["candidate_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(report["candidate_id"], CONFIG["candidate_id"])
        self.assertEqual(report["candidate_rows"], 9379)
        self.assertEqual(hashlib.sha256(candidate_path.read_bytes()).hexdigest(), report["candidate_csv_sha256"])
        self.assertEqual(hashlib.sha256(manifest_path.read_bytes()).hexdigest(), after["candidate_manifest_sha256"])
        self.assertEqual(manifest["candidate_csv_sha256"], report["candidate_csv_sha256"])
        self.assertTrue(after["all_adjustments_within_cap"])
        self.assertLessEqual(after["maximum_absolute_adjustment"], MAX_ADJUSTMENT)
        self.assertTrue(after["all_candidate_values_finite_and_in_0_1"])
        self.assertTrue(after["exact_official_ids_and_order"])
        self.assertTrue(after["deterministic_rerun"]["verified"])
        self.assertFalse(after["target_rmse_computed"])
        self.assertFalse(after["accuracy_claim_supported"])
        self.assertEqual(set(report["restrictions"]), {
            "tiger_or_mtfcc_values_used", "external_road_layers_used",
            "removed_reference_layers_or_equivalents_used", "target_reconstruction_or_labels_used",
            "sample_score_placeholders_read", "public_leaderboard_fitting_or_information_probes_used",
            "upload_or_submission_made", "route_reference_ratio_replaced_or_refit",
        })
        self.assertFalse(any(report["restrictions"].values()))
        with candidate_path.open("r", newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 9379)
        self.assertEqual(len({row["GEOID"] for row in rows}), 9379)
        for row in rows:
            gap = float(row["topology_transport_gap"])
            delta = float(row["candidate_adjustment"])
            self.assertTrue(math.isfinite(gap) and 0 <= gap <= 1)
            self.assertTrue(math.isfinite(delta) and abs(delta) <= MAX_ADJUSTMENT + 1e-12)
            self.assertIn(int(row["topology_transport_defined"]), (0, 1))
        for candidate_group in after["diagnostics_and_candidate_by_region_and_urban_rural"].values():
            self.assertIn("topology_graph", candidate_group)


if __name__ == "__main__":
    unittest.main()
