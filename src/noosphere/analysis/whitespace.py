"""Phase-1 structural whitespace detection over a coarse Run Snapshot.

Two candidate kinds (#12):

- **bridge** — a pair of citation communities whose embedding centroids are
  semantically close (top quartile of pairwise cosine similarity) but whose
  inter-community citation density is low (bottom quartile): ideas that sit
  next to each other in idea-space yet rarely cite across.
- **thin_cell** — a topic x community cell holding far fewer works than the
  independence expectation (topic_total * community_size / N).

``low_citedness_signal`` is a *feature*, never a filter: the mean inverse-log
citation count ``1 / ln(e + cited_by_count)`` of the works near the candidate
(1.0 for uncited works, decaying toward 0 as citations grow).
"""

import math
from itertools import combinations

from noosphere.analysis.algos import (
    community_centroid_similarity,
    inter_community_edge_density,
    louvain_communities,
)
from noosphere.graph import GraphStore
from noosphere.models import EvidenceItem, WhitespaceCandidate, Work
from noosphere.sidecar import Sidecar

_MIN_BRIDGE_SIMILARITY = 0.5
_THIN_CELL_MIN_EXPECTED = 2.0
_THIN_CELL_MAX_RATIO = 0.5


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _quantile(values: list[float], q: float) -> float:
    xs = sorted(values)
    if not xs:
        return 0.0
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _centroid(vecs: list[list[float]]) -> list[float]:
    dim = len(vecs[0])
    return [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]


def inverse_log_citedness(cited_by_counts: list[int]) -> float:
    """Mean of 1 / ln(e + cited_by_count): 1.0 for uncited, -> 0 as citations grow."""
    if not cited_by_counts:
        return 0.0
    return sum(1.0 / math.log(math.e + c) for c in cited_by_counts) / len(
        cited_by_counts
    )


def _topic_names(graph: GraphStore) -> dict[str, str]:
    rows = graph.query("MATCH (t:Topic) RETURN t.openalex_id, t.display_name")
    return {row[0]: row[1] for row in rows}


def _top_topic(
    members: list[str],
    work_topics: dict[str, list[tuple[str, float]]],
    names: dict[str, str],
) -> str:
    scores: dict[str, float] = {}
    for wid in members:
        for topic_id, score in work_topics.get(wid, []):
            scores[topic_id] = scores.get(topic_id, 0.0) + score
    if not scores:
        return "unlabeled"
    top = min(scores, key=lambda t: (-scores[t], t))
    return names.get(top, top)


def _bridge_evidence(
    members_a: list[str],
    members_b: list[str],
    embeddings: dict[str, list[float]],
) -> list[str]:
    """Up to 2 works per side, those closest to the *other* side's centroid."""
    vecs_a = [embeddings[m] for m in members_a if m in embeddings]
    vecs_b = [embeddings[m] for m in members_b if m in embeddings]
    if not vecs_a or not vecs_b:
        return sorted(members_a)[:2] + sorted(members_b)[:2]
    centroid_a, centroid_b = _centroid(vecs_a), _centroid(vecs_b)

    def toward(members: list[str], other: list[float]) -> list[str]:
        scored = sorted(
            (-_cosine(embeddings[m], other), m) for m in members if m in embeddings
        )
        return [m for _, m in scored[:2]]

    picked: list[str] = []
    for wid in toward(members_a, centroid_b) + toward(members_b, centroid_a):
        if wid not in picked:
            picked.append(wid)
    return picked[:4]


def detect_whitespace(
    run_id: str, graph: GraphStore, sidecar: Sidecar, *, top_n: int = 12
) -> list[WhitespaceCandidate]:
    """Detect bridge and thin-cell Whitespace Candidates over a coarse run.

    Deterministic: seeded Louvain plus total sort orders; persisted via
    ``sidecar.put_whitespace`` with status ``"candidate"``.
    """
    snapshot = sidecar.get_run_works(run_id)
    if not snapshot:
        return []
    snapshot_set = set(snapshot)

    works: dict[str, Work] = {}
    for wid in snapshot:
        w = graph.get_work(wid)
        if w is not None:
            works[wid] = w
    embeddings = {
        wid: w.embedding for wid, w in works.items() if w.embedding is not None
    }
    edges = graph.citation_edges(within=snapshot_set)
    communities = louvain_communities(edges, snapshot_set)

    members: dict[int, list[str]] = {}
    for wid in sorted(communities):
        members.setdefault(communities[wid], []).append(wid)

    work_topics: dict[str, list[tuple[str, float]]] = {}
    topic_works: dict[str, set[str]] = {}
    for wid, topic_id, score in graph.work_topic_rows():
        if wid not in snapshot_set:
            continue
        work_topics.setdefault(wid, []).append((topic_id, score))
        topic_works.setdefault(topic_id, set()).add(wid)
    names = _topic_names(graph)

    raw: list[dict] = []

    # -- bridge candidates ----------------------------------------------------
    sims = community_centroid_similarity(embeddings, communities)
    dens = inter_community_edge_density(edges, communities)
    pairs = sorted(set(sims) & set(dens))
    if pairs:
        sim_hi = _quantile([sims[p] for p in pairs], 0.75)
        den_lo = _quantile([dens[p] for p in pairs], 0.25)
        for a, b in pairs:
            sim, den = sims[(a, b)], dens[(a, b)]
            if sim < sim_hi or den > den_lo or sim < _MIN_BRIDGE_SIMILARITY:
                continue
            side_a, side_b = members.get(a, []), members.get(b, [])
            name_a = _top_topic(side_a, work_topics, names)
            name_b = _top_topic(side_b, work_topics, names)
            nearby = side_a + side_b
            raw.append(
                {
                    "kind": "bridge",
                    "description": (
                        f"Bridge whitespace between community {a} ({name_a}) and "
                        f"community {b} ({name_b}): centroids semantically close "
                        f"(cosine {sim:.2f}) but citation-sparse across "
                        f"(inter-community edge density {den:.4f})."
                    ),
                    "community_a": a,
                    "community_b": b,
                    "topic_id": None,
                    "sparsity_score": _clamp01(sim) * (1.0 - _clamp01(den)),
                    "low_citedness_signal": inverse_log_citedness(
                        [works[w].cited_by_count for w in nearby if w in works]
                    ),
                    "evidence_ids": _bridge_evidence(side_a, side_b, embeddings),
                }
            )

    # -- thin-cell candidates -------------------------------------------------
    n_total = len(snapshot_set)
    for topic_id in sorted(topic_works):
        in_topic = topic_works[topic_id]
        for community in sorted(members):
            comm_set = set(members[community])
            expected = len(in_topic) * len(comm_set) / n_total
            observed_ids = sorted(in_topic & comm_set)
            if expected < _THIN_CELL_MIN_EXPECTED:
                continue
            if len(observed_ids) > _THIN_CELL_MAX_RATIO * expected:
                continue
            evidence_ids = observed_ids[:4]
            if len(evidence_ids) < 2:
                filler = sorted(in_topic - comm_set)[:2] + members[community][:2]
                for wid in filler:
                    if wid not in evidence_ids and len(evidence_ids) < 4:
                        evidence_ids.append(wid)
            nearby = observed_ids or members[community]
            topic_name = names.get(topic_id, topic_id)
            raw.append(
                {
                    "kind": "thin_cell",
                    "description": (
                        f"Thin cell: topic {topic_name} in community {community} "
                        f"holds {len(observed_ids)} works vs {expected:.1f} "
                        "expected from topic and community sizes."
                    ),
                    "community_a": community,
                    "community_b": None,
                    "topic_id": topic_id,
                    "sparsity_score": _clamp01(1.0 - len(observed_ids) / expected),
                    "low_citedness_signal": inverse_log_citedness(
                        [works[w].cited_by_count for w in nearby if w in works]
                    ),
                    "evidence_ids": evidence_ids,
                }
            )

    raw.sort(
        key=lambda c: (
            -c["sparsity_score"],
            c["kind"],
            c["community_a"] if c["community_a"] is not None else -1,
            c["community_b"] if c["community_b"] is not None else -1,
            c["topic_id"] or "",
        )
    )

    candidates: list[WhitespaceCandidate] = []
    for i, c in enumerate(raw[:top_n]):
        candidate = WhitespaceCandidate(
            whitespace_id=f"{run_id}-ws{i:03d}",
            run_id=run_id,
            kind=c["kind"],
            description=c["description"],
            community_a=c["community_a"],
            community_b=c["community_b"],
            topic_id=c["topic_id"],
            sparsity_score=c["sparsity_score"],
            low_citedness_signal=c["low_citedness_signal"],
            evidence=[
                EvidenceItem(kind="work", work_id=wid) for wid in c["evidence_ids"]
            ],
            status="candidate",
        )
        sidecar.put_whitespace(candidate)
        candidates.append(candidate)
    return candidates
