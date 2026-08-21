"""Wave-2 analysis tests: whitespace detection, narrative mining, zoom
confirmation, and composite ranking.

Real GraphStore + Sidecar in tmp dirs; a fake LlmClient with canned JSON
responses stands in for Bedrock.
"""

import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from noosphere.analysis.confirm import confirm_candidate, temporal_profile
from noosphere.analysis.narrative import mine_narrative
from noosphere.analysis.ranking import composite_score, rank_gaps
from noosphere.analysis.whitespace import detect_whitespace, inverse_log_citedness
from noosphere.graph import GraphStore
from noosphere.models import (
    Citation,
    EvidenceItem,
    Gap,
    GapKind,
    Provenance,
    Run,
    RunPhase,
    Topic,
    WhitespaceCandidate,
    Work,
    WorkTopic,
)
from noosphere.sidecar import Sidecar

PROV = Provenance(
    source_api="openalex",
    source_id="test",
    retrieved_at=datetime(2026, 8, 21, 12, 0, 0),
)
THIS_YEAR = datetime.now(timezone.utc).year


class FakeLlm:
    """Canned-response stand-in for noosphere.llm.bedrock.LlmClient."""

    def __init__(
        self,
        haiku_payloads: list[dict] | None = None,
        opus_payloads: list[dict] | None = None,
    ) -> None:
        self.haiku_payloads = list(haiku_payloads or [])
        self.opus_payloads = list(opus_payloads or [])
        self.haiku_calls: list[dict] = []
        self.opus_calls: list[dict] = []

    async def haiku_json(self, system: str, user: str, max_tokens: int = 2048) -> dict:
        self.haiku_calls.append({"system": system, "user": user})
        return self.haiku_payloads.pop(0)

    async def opus_json(self, system: str, user: str, max_tokens: int = 8192) -> dict:
        self.opus_calls.append({"system": system, "user": user})
        return self.opus_payloads.pop(0)


def make_work(oid: str, **kwargs) -> Work:
    defaults = {"title": f"Work {oid}", "year": 2024, "provenance": PROV}
    defaults.update(kwargs)
    return Work(openalex_id=oid, **defaults)


@pytest.fixture
def sidecar(tmp_path: Path) -> Sidecar:
    return Sidecar(tmp_path / "sidecar.duckdb")


@pytest.fixture
def store(tmp_path: Path) -> GraphStore:
    s = GraphStore(tmp_path / "graph.lb")
    s.init_schema()
    return s


CLUSTER_A = [f"A{i}" for i in range(5)]
CLUSTER_B = [f"B{i}" for i in range(5)]
CITED_COUNTS = {**{w: 20 + i for i, w in enumerate(CLUSTER_A)}, "B0": 15, "B1": 8, "B2": 3, "B3": 0, "B4": 0}


def seed_two_cluster_corpus(store: GraphStore, sidecar: Sidecar, run_id: str) -> None:
    """Two 5-cliques, semantically close embeddings, zero cross citations,
    one topic per cluster, and two zero-citation works (B3, B4)."""
    works = []
    for i, oid in enumerate(CLUSTER_A):
        works.append(
            make_work(
                oid,
                year=2022 + i % 3,
                cited_by_count=CITED_COUNTS[oid],
                embedding=[1.0, 0.06 * i, 0.05],
                abstract=f"Agent memory paper {oid}.",
            )
        )
    for i, oid in enumerate(CLUSTER_B):
        works.append(
            make_work(
                oid,
                year=2021 + i % 3,
                cited_by_count=CITED_COUNTS[oid],
                embedding=[0.9, 0.35 + 0.02 * i, 0.1],
                abstract=f"Hippocampal replay paper {oid}.",
            )
        )
    store.upsert_works(works)
    store.upsert_topics(
        [
            Topic(openalex_id="TA", display_name="Agent Memory Architectures", level="topic", provenance=PROV),
            Topic(openalex_id="TB", display_name="Hippocampal Replay", level="topic", provenance=PROV),
        ]
    )
    citations = []
    for cluster in (CLUSTER_A, CLUSTER_B):
        for i, u in enumerate(cluster):
            for v in cluster[i + 1 :]:
                citations.append(Citation(citing_id=u, cited_id=v, provenance=PROV))
    store.add_citations(citations)
    store.add_work_topics(
        [WorkTopic(work_id=w, topic_id="TA", score=0.9, provenance=PROV) for w in CLUSTER_A]
        + [WorkTopic(work_id=w, topic_id="TB", score=0.9, provenance=PROV) for w in CLUSTER_B]
    )
    sidecar.create_run(
        Run(run_id=run_id, field_name="memory for AI agents", phase=RunPhase.COARSE)
    )
    sidecar.add_run_works(run_id, CLUSTER_A + CLUSTER_B)


class TestDetectWhitespace:
    def test_finds_bridge_between_close_unlinked_clusters(
        self, store: GraphStore, sidecar: Sidecar
    ) -> None:
        seed_two_cluster_corpus(store, sidecar, "run-coarse")
        candidates = detect_whitespace("run-coarse", store, sidecar)

        bridges = [c for c in candidates if c.kind == "bridge"]
        assert len(bridges) == 1
        bridge = bridges[0]
        assert bridge.community_a != bridge.community_b
        assert "Agent Memory Architectures" in bridge.description
        assert "Hippocampal Replay" in bridge.description
        assert 0.0 < bridge.sparsity_score <= 1.0
        assert 2 <= len(bridge.evidence) <= 4
        assert all(
            e.kind == "work" and e.work_id in set(CLUSTER_A + CLUSTER_B)
            for e in bridge.evidence
        )
        # evidence spans both sides of the bridge
        sides = {e.work_id[0] for e in bridge.evidence}
        assert sides == {"A", "B"}

    def test_low_citedness_signal_is_inverse_log_of_nearby_counts(
        self, store: GraphStore, sidecar: Sidecar
    ) -> None:
        seed_two_cluster_corpus(store, sidecar, "run-coarse")
        bridge = next(
            c for c in detect_whitespace("run-coarse", store, sidecar) if c.kind == "bridge"
        )
        expected = sum(
            1.0 / math.log(math.e + CITED_COUNTS[w]) for w in CLUSTER_A + CLUSTER_B
        ) / 10
        assert bridge.low_citedness_signal == pytest.approx(expected)
        assert 0.0 < bridge.low_citedness_signal <= 1.0

    def test_finds_thin_cells_and_persists_candidates(
        self, store: GraphStore, sidecar: Sidecar
    ) -> None:
        seed_two_cluster_corpus(store, sidecar, "run-coarse")
        candidates = detect_whitespace("run-coarse", store, sidecar)

        thin = [c for c in candidates if c.kind == "thin_cell"]
        assert thin  # each topic is absent from the other cluster's community
        assert all(t.topic_id in ("TA", "TB") for t in thin)
        assert all(t.sparsity_score == pytest.approx(1.0) for t in thin)

        persisted = sidecar.list_whitespace("run-coarse")
        assert {c.whitespace_id for c in persisted} == {
            c.whitespace_id for c in candidates
        }
        assert all(c.status == "candidate" for c in persisted)

    def test_deterministic_ordering(self, store: GraphStore, sidecar: Sidecar) -> None:
        seed_two_cluster_corpus(store, sidecar, "run-coarse")
        first = detect_whitespace("run-coarse", store, sidecar)
        second = detect_whitespace("run-coarse", store, sidecar)
        assert first == second
        scores = [c.sparsity_score for c in first]
        assert scores == sorted(scores, reverse=True)

    def test_empty_snapshot_yields_nothing(
        self, store: GraphStore, sidecar: Sidecar
    ) -> None:
        assert detect_whitespace("run-missing", store, sidecar) == []


class TestMineNarrative:
    async def test_maps_claims_to_evidence_and_skips_missing_abstracts(
        self, store: GraphStore
    ) -> None:
        store.upsert_works(
            [
                make_work("W1", abstract="Future work should study consolidation."),
                make_work("W2", abstract=None),
                make_work("W3", abstract="A limitation is the tiny corpus."),
            ]
        )
        llm = FakeLlm(
            haiku_payloads=[
                {"claims": [{"quote": "Future work should study consolidation.", "source_index": 0, "kind": "future_work"}]},
                {"claims": [{"quote": "A limitation is the tiny corpus.", "source_index": 0, "kind": "limitation"}]},
            ]
        )
        items = await mine_narrative(["W1", "W2", "W3"], store, llm, batch=1)

        assert len(llm.haiku_calls) == 2  # W2 skipped, batch size 1
        assert "consolidation" in llm.haiku_calls[0]["user"]
        assert [i.work_id for i in items] == ["W1", "W3"]
        assert all(i.kind == "work" and i.quote for i in items)

    async def test_ignores_out_of_range_source_index(self, store: GraphStore) -> None:
        store.upsert_works([make_work("W1", abstract="abstract text")])
        llm = FakeLlm(
            haiku_payloads=[{"claims": [{"quote": "q", "source_index": 5, "kind": "limitation"}]}]
        )
        assert await mine_narrative(["W1"], store, llm) == []

    async def test_web_booster_emits_identifier_only_web_items(
        self, store: GraphStore
    ) -> None:
        store.upsert_works([make_work("W1", abstract="corpus abstract")])
        llm = FakeLlm(
            haiku_payloads=[
                {"claims": []},
                {"claims": [{"quote": "an open problem remains", "source_index": 0, "kind": "open_problem"}]},
            ]
        )
        snippets = [
            {
                "url": "https://example.org/post",
                "retrieved_at": "2026-08-20T00:00:00Z",
                "text": "an open problem remains in replay-informed agent memory",
            }
        ]
        items = await mine_narrative(["W1"], store, llm, web_snippets=snippets)

        assert len(items) == 1
        web = items[0]
        assert web.kind == "web"
        assert web.url == "https://example.org/post"
        assert web.retrieved_at is not None
        assert web.quote is None  # Web Search content is never persisted


def make_candidate(**kwargs) -> WhitespaceCandidate:
    defaults = {
        "whitespace_id": "run-coarse-ws000",
        "run_id": "run-coarse",
        "kind": "bridge",
        "description": "Bridge whitespace between community 0 and community 1.",
        "community_a": 0,
        "community_b": 1,
        "sparsity_score": 0.9,
        "low_citedness_signal": 0.6,
        "evidence": [EvidenceItem(kind="work", work_id="Z0")],
        "status": "zooming",
    }
    defaults.update(kwargs)
    return WhitespaceCandidate(**defaults)


def seed_zoom_snapshot(
    store: GraphStore, sidecar: Sidecar, run_id: str, *, dense: bool
) -> list[str]:
    ids = [f"Z{i}" for i in range(6)]
    store.upsert_works(
        [
            make_work(
                oid,
                year=THIS_YEAR - (i % 3),
                cited_by_count=i,
                abstract=f"Zoom work {oid} abstract.",
            )
            for i, oid in enumerate(ids)
        ]
    )
    citations = (
        [
            Citation(citing_id=u, cited_id=v, provenance=PROV)
            for i, u in enumerate(ids)
            for v in ids[i + 1 :]
        ]
        if dense
        else [Citation(citing_id="Z0", cited_id="Z1", provenance=PROV)]
    )
    store.add_citations(citations)
    sidecar.create_run(
        Run(
            run_id=run_id,
            field_name="memory for AI agents",
            phase=RunPhase.ZOOM,
            parent_run_id="run-coarse",
            whitespace_id="run-coarse-ws000",
        )
    )
    sidecar.add_run_works(run_id, ids)
    return ids


class TestConfirmCandidate:
    async def test_happy_path_persists_gap_with_all_component_scores(
        self, store: GraphStore, sidecar: Sidecar
    ) -> None:
        seed_zoom_snapshot(store, sidecar, "run-zoom", dense=False)
        candidate = make_candidate()
        sidecar.put_whitespace(candidate)
        statement = "Replay-informed agent memory is citation-sparse [0] and named as future work [1]."
        llm = FakeLlm(
            haiku_payloads=[
                {
                    "claims": [
                        {"quote": "future work on replay", "source_index": 0, "kind": "future_work"},
                        {"quote": "a limitation is memory scope", "source_index": 2, "kind": "limitation"},
                    ]
                }
            ],
            opus_payloads=[
                {"statement": statement, "kinds": ["structural", "narrative"], "cited": [0, 1]}
            ],
        )

        gap = await confirm_candidate(candidate, "run-zoom", store, sidecar, llm)

        assert gap is not None
        assert set(gap.scores) == {"sparsity", "narrative_demand", "recency", "low_citedness"}
        assert all(0.0 <= v <= 1.0 for v in gap.scores.values())
        assert gap.scores["sparsity"] > 0.9
        assert gap.scores["narrative_demand"] == pytest.approx(0.5)  # 2 claims / 4
        assert gap.scores["recency"] == pytest.approx(1.0)
        assert gap.statement == statement
        assert GapKind.STRUCTURAL in gap.kinds and GapKind.NARRATIVE in gap.kinds
        assert gap.whitespace_id == candidate.whitespace_id
        assert gap.zoom_run_id == "run-zoom"
        assert gap.composite_score > 0.0
        # evidence merges the candidate's items with the mined claims
        assert len(gap.evidence) == 3
        assert gap.evidence[0].work_id == "Z0"
        assert {e.work_id for e in gap.evidence[1:]} <= {f"Z{i}" for i in range(6)}

        assert sidecar.list_gaps("run-zoom") == [gap]
        persisted = sidecar.list_whitespace("run-coarse")
        assert persisted[0].status == "confirmed"
        assert persisted[0].not_confirmed_reason is None
        assert len(llm.opus_calls) == 1
        assert candidate.whitespace_id in llm.opus_calls[0]["user"]

    async def test_failed_sparsity_check_marks_not_confirmed(
        self, store: GraphStore, sidecar: Sidecar
    ) -> None:
        seed_zoom_snapshot(store, sidecar, "run-zoom-dense", dense=True)
        candidate = make_candidate()
        llm = FakeLlm()

        result = await confirm_candidate(candidate, "run-zoom-dense", store, sidecar, llm)

        assert result is None
        assert llm.haiku_calls == [] and llm.opus_calls == []  # short-circuits
        persisted = sidecar.list_whitespace("run-coarse")
        assert persisted[0].status == "not_confirmed"
        assert "sparsity" in persisted[0].not_confirmed_reason
        assert sidecar.list_gaps("run-zoom-dense") == []

    async def test_no_narrative_demand_marks_not_confirmed(
        self, store: GraphStore, sidecar: Sidecar
    ) -> None:
        seed_zoom_snapshot(store, sidecar, "run-zoom", dense=False)
        candidate = make_candidate()
        llm = FakeLlm(haiku_payloads=[{"claims": []}])

        result = await confirm_candidate(candidate, "run-zoom", store, sidecar, llm)

        assert result is None
        assert llm.opus_calls == []
        assert "narrative" in sidecar.list_whitespace("run-coarse")[0].not_confirmed_reason

    async def test_empty_zoom_snapshot_marks_not_confirmed(
        self, store: GraphStore, sidecar: Sidecar
    ) -> None:
        candidate = make_candidate()
        result = await confirm_candidate(candidate, "run-empty", store, sidecar, FakeLlm())
        assert result is None
        assert "empty" in sidecar.list_whitespace("run-coarse")[0].not_confirmed_reason


class TestTemporalProfile:
    def test_profiles(self) -> None:
        assert temporal_profile([], ref_year=2026) == ("never_started", 0.0)
        profile, recency = temporal_profile([2025, 2026], ref_year=2026)
        assert profile == "never_started" and recency == pytest.approx(1.0)
        profile, recency = temporal_profile(
            [2010, 2011, 2012, 2013, 2014], ref_year=2026
        )
        assert profile == "went_quiet" and recency == 0.0
        profile, recency = temporal_profile([2023, 2025, 2026, 2026], ref_year=2026)
        assert profile == "emerging" and recency == pytest.approx(0.75)


def make_gap(gap_id: str, scores: dict[str, float]) -> Gap:
    return Gap(
        gap_id=gap_id,
        whitespace_id="ws",
        zoom_run_id="run-zoom",
        kinds=[GapKind.STRUCTURAL],
        statement="s [0]",
        evidence=[EvidenceItem(kind="work", work_id="W1")],
        scores=scores,
        composite_score=0.0,
    )


class TestRanking:
    def test_composite_score_weighted_mean(self) -> None:
        scores = {"sparsity": 1.0, "narrative_demand": 0.5}
        weights = {"sparsity": 2.0, "narrative_demand": 1.0, "recency": 1.0}
        assert composite_score(scores, weights) == pytest.approx((2.0 + 0.5) / 3.0)
        assert composite_score(scores, {}) == 0.0
        assert composite_score({}, weights) == 0.0

    def test_rank_gaps_ordering_respects_weights(self) -> None:
        structural = make_gap(
            "gap-a",
            {"sparsity": 0.9, "narrative_demand": 0.1, "recency": 0.5, "low_citedness": 0.5},
        )
        narrative = make_gap(
            "gap-b",
            {"sparsity": 0.1, "narrative_demand": 0.9, "recency": 0.5, "low_citedness": 0.5},
        )

        sparsity_heavy = {"sparsity": 5.0, "narrative_demand": 1.0, "recency": 1.0, "low_citedness": 1.0}
        ranked = rank_gaps([narrative, structural], sparsity_heavy)
        assert [g.gap_id for g in ranked] == ["gap-a", "gap-b"]
        assert ranked[0].composite_score == pytest.approx(
            (0.9 * 5 + 0.1 + 0.5 + 0.5) / 8
        )

        narrative_heavy = {"sparsity": 1.0, "narrative_demand": 5.0, "recency": 1.0, "low_citedness": 1.0}
        ranked = rank_gaps([narrative, structural], narrative_heavy)
        assert [g.gap_id for g in ranked] == ["gap-b", "gap-a"]
        assert all(g.composite_score > 0 for g in ranked)
