"""Pure graph primitives for recipe-exact Family T v2 topology estimators.

These helpers intentionally operate only on feature metadata and metric-coordinate geometry passed
by the provided-data reader. They do not open any source themselves.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence


def deflection_degrees(vector_a: Sequence[float], vector_b: Sequence[float]) -> float:
    """Return road deflection at a junction (0° is straight continuation).

    Vectors point from the shared connector outward along each segment. Thus collinear opposite
    vectors are 0° deflection, perpendicular vectors are 90°, and same-direction vectors are
    180°. Degenerate tangents are rejected rather than silently joined.
    """
    ax, ay = float(vector_a[0]), float(vector_a[1])
    bx, by = float(vector_b[0]), float(vector_b[1])
    an, bn = math.hypot(ax, ay), math.hypot(bx, by)
    if an == 0.0 or bn == 0.0:
        raise ValueError("stroke tangent vectors must be non-zero")
    cosine = max(-1.0, min(1.0, -(ax * bx + ay * by) / (an * bn)))
    return math.degrees(math.acos(cosine))


def stroke_components(identities: Sequence[str | None],
                      endpoint_connectors: Sequence[tuple[str | None, str | None]],
                      endpoint_tangents: Sequence[tuple[Sequence[float], Sequence[float]]],
                      *, threshold_degrees: float = 30.0) -> list[int]:
    """Label connected named-stroke components using identity and strict deflection < threshold.

    Each feature contributes its two endpoint connector IDs and outward tangent vectors in the
    same order. Components never join different semantic identities, even at a shared connector.
    """
    n = len(identities)
    if len(endpoint_connectors) != n or len(endpoint_tangents) != n:
        raise ValueError("identity, connector and tangent arrays must have equal lengths")
    if not 0.0 < threshold_degrees <= 180.0:
        raise ValueError("threshold_degrees must be in (0, 180]")
    parent = list(range(n))
    rank = [0] * n

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    at_connector: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for edge, identity in enumerate(identities):
        if identity is None:
            continue
        for endpoint, connector in enumerate(endpoint_connectors[edge]):
            if connector:
                at_connector[(identity, connector)].append((edge, endpoint))

    for incidents in at_connector.values():
        for left in range(len(incidents)):
            edge_a, end_a = incidents[left]
            for right in range(left + 1, len(incidents)):
                edge_b, end_b = incidents[right]
                if edge_a == edge_b:
                    continue
                angle = deflection_degrees(endpoint_tangents[edge_a][end_a],
                                           endpoint_tangents[edge_b][end_b])
                # Keep an exactly-threshold turn out despite inverse-trig round-off.
                if angle < threshold_degrees - 1e-12:
                    union(edge_a, edge_b)
    roots: dict[int, int] = {}
    labels = []
    for edge in range(n):
        root = find(edge)
        if root not in roots:
            roots[root] = len(roots)
        labels.append(roots[root])
    return labels
