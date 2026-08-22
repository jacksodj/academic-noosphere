"""Graph algorithms over exported edge lists.

Ladybug's ALGO extension cannot download in this environment, so all
algorithms run in igraph over edges exported from ``GraphStore``
(``citation_edges`` / ``work_topic_rows``). Results are deterministic: a
fixed-seed RNG is installed before every stochastic igraph call.
"""

import math
import random
from itertools import combinations

import igraph

_SEED = 20260821


def _build_graph(
    edges: list[tuple[str, str]], nodes: set[str], *, directed: bool
) -> tuple[igraph.Graph, list[str]]:
    names = sorted(nodes | {n for edge in edges for n in edge})
    index = {name: i for i, name in enumerate(names)}
    g = igraph.Graph(
        n=len(names),
        edges=[(index[a], index[b]) for a, b in edges],
        directed=directed,
    )
    return g, names


def louvain_communities(
    edges: list[tuple[str, str]], nodes: set[str], resolution: float = 1.0
) -> dict[str, int]:
    """Seeded Louvain; higher ``resolution`` favors more, smaller communities."""
    g, names = _build_graph(edges, nodes, directed=False)
    if not names:
        return {}
    g.simplify()
    igraph.set_random_number_generator(random.Random(_SEED))
    clustering = g.community_multilevel(resolution=resolution)
    return {name: clustering.membership[i] for i, name in enumerate(names)}


def pagerank(edges: list[tuple[str, str]], nodes: set[str]) -> dict[str, float]:
    g, names = _build_graph(edges, nodes, directed=True)
    if not names:
        return {}
    scores = g.pagerank(damping=0.85)
    return {name: scores[i] for i, name in enumerate(names)}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def community_centroid_similarity(
    embeddings: dict[str, list[float]], communities: dict[str, int]
) -> dict[tuple[int, int], float]:
    members: dict[int, list[list[float]]] = {}
    for node, community in communities.items():
        vec = embeddings.get(node)
        if vec is not None:
            members.setdefault(community, []).append(vec)
    centroids: dict[int, list[float]] = {}
    for community, vecs in members.items():
        dim = len(vecs[0])
        centroids[community] = [
            sum(v[i] for v in vecs) / len(vecs) for i in range(dim)
        ]
    return {
        (a, b): _cosine(centroids[a], centroids[b])
        for a, b in combinations(sorted(centroids), 2)
    }


def inter_community_edge_density(
    edges: list[tuple[str, str]], communities: dict[str, int]
) -> dict[tuple[int, int], float]:
    sizes: dict[int, int] = {}
    for community in communities.values():
        sizes[community] = sizes.get(community, 0) + 1
    cross: dict[tuple[int, int], set[tuple[str, str]]] = {}
    for a, b in edges:
        ca, cb = communities.get(a), communities.get(b)
        if ca is None or cb is None or ca == cb:
            continue
        pair = (min(ca, cb), max(ca, cb))
        cross.setdefault(pair, set()).add((min(a, b), max(a, b)))
    return {
        (a, b): len(cross.get((a, b), set())) / (sizes[a] * sizes[b])
        for a, b in combinations(sorted(sizes), 2)
    }
