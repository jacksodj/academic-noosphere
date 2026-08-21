"""GraphStore (real ladybug DB) and analysis/algos (igraph) tests."""

from datetime import datetime
from pathlib import Path

import pytest

from noosphere.analysis.algos import (
    community_centroid_similarity,
    inter_community_edge_density,
    louvain_communities,
    pagerank,
)
from noosphere.graph import GraphStore
from noosphere.models import (
    Author,
    Authorship,
    Citation,
    Provenance,
    Topic,
    Work,
    WorkTopic,
)

PROV = Provenance(
    source_api="openalex",
    source_id="test",
    retrieved_at=datetime(2026, 8, 21, 12, 0, 0),
)


def make_work(oid: str, **kwargs) -> Work:
    defaults = {"title": f"Work {oid}", "year": 2024, "provenance": PROV}
    defaults.update(kwargs)
    return Work(openalex_id=oid, **defaults)


@pytest.fixture
def store(tmp_path: Path) -> GraphStore:
    s = GraphStore(tmp_path / "graph.lb")
    s.init_schema()
    return s


class TestGraphStore:
    def test_init_schema_idempotent(self, store: GraphStore) -> None:
        store.init_schema()
        store.upsert_works([make_work("W1")])
        assert store.work_ids() == {"W1"}

    def test_upsert_works_insert_then_update(self, store: GraphStore) -> None:
        store.upsert_works(
            [make_work("W1", title="Original", embedding=[0.1, 0.2, 0.3])]
        )
        w = store.get_work("W1")
        assert w is not None
        assert w.title == "Original"
        assert w.embedding == [0.1, 0.2, 0.3]
        assert w.provenance.source_api == "openalex"
        assert w.provenance.retrieved_at == PROV.retrieved_at

        store.upsert_works(
            [
                make_work(
                    "W1",
                    title="Updated",
                    year=2025,
                    doi="10.1/x",
                    cited_by_count=7,
                    embedding=[0.9, 0.8, 0.7],
                )
            ]
        )
        assert store.work_ids() == {"W1"}
        w2 = store.get_work("W1")
        assert w2 is not None
        assert w2.title == "Updated"
        assert w2.year == 2025
        assert w2.doi == "10.1/x"
        assert w2.cited_by_count == 7
        assert w2.embedding == [0.9, 0.8, 0.7]

    def test_get_work_missing(self, store: GraphStore) -> None:
        assert store.get_work("W404") is None

    def test_authorship_edges(self, store: GraphStore) -> None:
        store.upsert_works([make_work("W1")])
        store.upsert_authors(
            [Author(openalex_id="A1", display_name="Ada", provenance=PROV)]
        )
        store.add_authorships(
            [Authorship(author_id="A1", work_id="W1", position=0, provenance=PROV)]
        )
        rows = store.query(
            "MATCH (a:Author)-[r:AUTHORED]->(w:Work) "
            "RETURN a.openalex_id, w.openalex_id, r.position, r.source_api"
        )
        assert rows == [["A1", "W1", 0, "openalex"]]

        store.add_authorships(
            [Authorship(author_id="A1", work_id="W1", position=2, provenance=PROV)]
        )
        rows = store.query("MATCH (:Author)-[r:AUTHORED]->(:Work) RETURN r.position")
        assert rows == [[2]]

    def test_citation_and_work_topic_edges(self, store: GraphStore) -> None:
        store.upsert_works([make_work("W1"), make_work("W2")])
        store.upsert_topics(
            [
                Topic(
                    openalex_id="T1",
                    display_name="Agent memory",
                    level="topic",
                    provenance=PROV,
                )
            ]
        )
        store.add_citations(
            [Citation(citing_id="W1", cited_id="W2", provenance=PROV)]
        )
        store.add_work_topics(
            [WorkTopic(work_id="W1", topic_id="T1", score=0.9, provenance=PROV)]
        )
        assert store.citation_edges() == [("W1", "W2")]
        assert store.work_topic_rows() == [("W1", "T1", 0.9)]
        # edge upserts are idempotent
        store.add_citations(
            [Citation(citing_id="W1", cited_id="W2", provenance=PROV)]
        )
        assert store.citation_edges() == [("W1", "W2")]

    def test_citation_edges_within_filter(self, store: GraphStore) -> None:
        store.upsert_works([make_work(w) for w in ("W1", "W2", "W3")])
        store.add_citations(
            [
                Citation(citing_id="W1", cited_id="W2", provenance=PROV),
                Citation(citing_id="W2", cited_id="W3", provenance=PROV),
                Citation(citing_id="W3", cited_id="W1", provenance=PROV),
            ]
        )
        assert set(store.citation_edges()) == {
            ("W1", "W2"),
            ("W2", "W3"),
            ("W3", "W1"),
        }
        assert store.citation_edges(within={"W1", "W2"}) == [("W1", "W2")]
        assert store.citation_edges(within={"W1"}) == []


def synthetic_communities() -> tuple[
    list[tuple[str, str]], set[str], dict[str, list[float]]
]:
    """Two dense 5-cliques joined by one bridge edge."""
    cluster_a = [f"A{i}" for i in range(5)]
    cluster_b = [f"B{i}" for i in range(5)]
    edges: list[tuple[str, str]] = []
    for cluster in (cluster_a, cluster_b):
        for i, u in enumerate(cluster):
            for v in cluster[i + 1 :]:
                edges.append((u, v))
    edges.append(("A0", "B0"))
    nodes = set(cluster_a) | set(cluster_b)
    embeddings = {n: [1.0, 0.05 * i, 0.0] for i, n in enumerate(cluster_a)}
    embeddings |= {n: [0.0, 0.05 * i, 1.0] for i, n in enumerate(cluster_b)}
    return edges, nodes, embeddings


class TestAlgos:
    def test_louvain_finds_clusters(self) -> None:
        edges, nodes, _ = synthetic_communities()
        communities = louvain_communities(edges, nodes)
        assert set(communities) == nodes
        assert len(set(communities.values())) >= 2
        for cluster_prefix in ("A", "B"):
            labels = {c for n, c in communities.items() if n.startswith(cluster_prefix)}
            assert len(labels) == 1
        assert communities["A0"] != communities["B0"]
        assert communities == louvain_communities(edges, nodes)  # deterministic

    def test_pagerank_sums_to_one(self) -> None:
        edges, nodes, _ = synthetic_communities()
        scores = pagerank(edges, nodes)
        assert set(scores) == nodes
        assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)
        assert all(s > 0 for s in scores.values())

    def test_pagerank_includes_isolated_nodes(self) -> None:
        scores = pagerank([("X", "Y")], {"X", "Y", "Z"})
        assert set(scores) == {"X", "Y", "Z"}
        assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)

    def test_inter_community_edge_density_low_across_clusters(self) -> None:
        edges, nodes, _ = synthetic_communities()
        communities = louvain_communities(edges, nodes)
        density = inter_community_edge_density(edges, communities)
        pair = (
            min(communities["A0"], communities["B0"]),
            max(communities["A0"], communities["B0"]),
        )
        assert density[pair] == pytest.approx(1 / 25)
        assert density[pair] < 0.1

    def test_community_centroid_similarity(self) -> None:
        edges, nodes, embeddings = synthetic_communities()
        communities = louvain_communities(edges, nodes)
        # within-cluster similarity is high: split cluster A artificially
        sub = {n: (0 if n in ("A0", "A1") else 1) for n in embeddings if n.startswith("A")}
        sims_same = community_centroid_similarity(embeddings, sub)
        assert sims_same[(0, 1)] > 0.99
        # across the two real clusters (orthogonal embeddings) similarity is low
        sims_cross = community_centroid_similarity(embeddings, communities)
        pair = (
            min(communities["A0"], communities["B0"]),
            max(communities["A0"], communities["B0"]),
        )
        assert sims_cross[pair] < 0.1

    def test_empty_inputs(self) -> None:
        assert louvain_communities([], set()) == {}
        assert pagerank([], set()) == {}
        assert community_centroid_similarity({}, {}) == {}
        assert inter_community_edge_density([], {}) == {}
