"""Reports wave-2 tests: ideonomy expansion (#15), Gap Report assembly and
Markdown rendering, and the grounding linter. Real Sidecar + GraphStore in
tmp; the LLM is a canned fake."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from noosphere.graph import GraphStore
from noosphere.ideonomy.expand import expand_gap
from noosphere.ideonomy.picker import pick_tuple
from noosphere.models import (
    EvidenceItem,
    Gap,
    Provenance,
    Run,
    RunPhase,
    Work,
)
from noosphere.reports.gaps import assemble_report, to_markdown
from noosphere.reports.linter import lint_report
from noosphere.sidecar import Sidecar

CATALOG = Path(__file__).resolve().parent.parent / "vendor" / "ideonomy"
FIELD = "memory for AI agents"
WEIGHTS = {"sparsity": 0.5, "narrative_demand": 0.3, "recency": 0.1, "low_citedness": 0.1}

PROV = Provenance(
    source_api="openalex",
    source_id="test",
    retrieved_at=datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc),
)

WEB_EVIDENCE = EvidenceItem(
    kind="web",
    url="https://example.org/agent-memory-survey",
    retrieved_at=datetime(2026, 8, 1, 9, 30, 0, tzinfo=timezone.utc),
)


def _gap(gap_id: str, evidence: list[EvidenceItem], composite: float) -> Gap:
    return Gap(
        gap_id=gap_id,
        whitespace_id="ws1",
        zoom_run_id="run-z",
        kinds=["structural"],
        statement=f"Gap {gap_id}: consolidation-inspired forgetting is untested "
        "for agent episodic stores.",
        evidence=evidence,
        scores={"sparsity": 0.8, "narrative_demand": 0.6},
        composite_score=composite,
    )


@pytest.fixture
def stores(tmp_path: Path) -> tuple[Sidecar, GraphStore]:
    from noosphere.models import WhitespaceCandidate

    sidecar = Sidecar(tmp_path / "sidecar.duckdb")
    graph = GraphStore(tmp_path / "graph.lb")
    graph.init_schema()
    graph.upsert_works(
        [
            Work(
                openalex_id="W1",
                doi="10.1234/w1",
                title="Episodic memory for agents",
                year=2024,
                abstract="We study episodic memory buffers for LLM agents. " * 20,
                provenance=PROV,
            ),
            Work(
                openalex_id="W2",
                title="Consolidation in human memory",
                year=2021,
                abstract="Systems consolidation transfers memories.",
                provenance=PROV,
            ),
            Work(openalex_id="W3", title="Off-snapshot work", year=2020, provenance=PROV),
        ]
    )
    sidecar.create_run(Run(run_id="run-c", field_name=FIELD, phase=RunPhase.COARSE))
    sidecar.create_run(
        Run(
            run_id="run-z",
            field_name=FIELD,
            phase=RunPhase.ZOOM,
            parent_run_id="run-c",
            whitespace_id="ws1",
        )
    )
    sidecar.add_run_works("run-z", ["W1", "W2"])
    sidecar.put_whitespace(
        WhitespaceCandidate(
            whitespace_id="ws1",
            run_id="run-c",
            kind="bridge",
            description="agent memory <-> consolidation bridge",
            sparsity_score=0.9,
            status="confirmed",
        )
    )
    sidecar.put_whitespace(
        WhitespaceCandidate(
            whitespace_id="ws2",
            run_id="run-c",
            kind="thin_cell",
            description="forgetting curves for tool-use traces",
            sparsity_score=0.7,
            status="not_confirmed",
            not_confirmed_reason="dense at depth",
        )
    )
    sidecar.put_gap(
        _gap(
            "g1",
            [
                EvidenceItem(kind="work", work_id="W1", quote="episodic buffers"),
                EvidenceItem(kind="work", work_id="W2"),
                WEB_EVIDENCE,
            ],
            composite=0.7,
        )
    )
    sidecar.put_gap(
        _gap("g2", [EvidenceItem(kind="work", work_id="W2")], composite=0.3)
    )
    yield sidecar, graph
    sidecar.close()


class FakeLlm:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    async def opus_json(self, system: str, user: str, max_tokens: int = 8192) -> dict:
        self.calls.append((system, user))
        return self.payload


def _conforming_payload(seed: str) -> tuple[dict, list[str]]:
    picked = pick_tuple(seed, CATALOG)
    payload = {
        "ideas": [
            {
                "text": "Invert consolidation: schedule forgetting as a training signal.",
                "operators": [picked.operators[0]],
                "organon_position": "cell (1,2)",
                "nearest_work_id": "W1",
            },
            {
                "text": "Idea with an operator outside the tuple.",
                "operators": ["not-a-real-operator"],
                "organon_position": "cell (2,2)",
                "nearest_work_id": "W1",
            },
            {
                "text": "Idea citing a work outside the provided set.",
                "operators": [picked.operators[1]],
                "organon_position": "cell (3,1)",
                "nearest_work_id": "W999",
            },
        ]
    }
    return payload, picked.operators


class TestExpandGap:
    async def test_conforming_ideas_kept_others_dropped(self, stores) -> None:
        sidecar, graph = stores
        gap = sidecar.list_gaps("run-z")[0]
        payload, operators = _conforming_payload(f"run-z:{gap.gap_id}:0")
        llm = FakeLlm(payload)
        expansion = await expand_gap(gap, "run-z", 0, graph, llm, CATALOG)
        assert expansion.gap_id == gap.gap_id
        assert expansion.attempt == 0
        assert len(expansion.ideas) == 1
        idea = expansion.ideas[0]
        assert idea.operators == [operators[0]]
        assert idea.nearest_work_id == "W1"

    async def test_prompt_carries_gap_statement_and_work_context(self, stores) -> None:
        sidecar, graph = stores
        gap = sidecar.list_gaps("run-z")[0]
        payload, _ = _conforming_payload(f"run-z:{gap.gap_id}:0")
        llm = FakeLlm(payload)
        await expand_gap(gap, "run-z", 0, graph, llm, CATALOG)
        _, user = llm.calls[0]
        assert gap.statement in user
        assert "Episodic memory for agents" in user
        assert "https://example.org/agent-memory-survey" in user
        assert "IDEONOMY METHOD TUPLE" in user

    async def test_deterministic_tuple_for_same_seed_inputs(self, stores) -> None:
        sidecar, graph = stores
        gap = sidecar.list_gaps("run-z")[0]
        payload, _ = _conforming_payload(f"run-z:{gap.gap_id}:7")
        first = await expand_gap(gap, "run-z", 7, graph, FakeLlm(payload), CATALOG)
        second = await expand_gap(gap, "run-z", 7, graph, FakeLlm(payload), CATALOG)
        assert first.tuple == second.tuple
        assert first.tuple.seed == f"run-z:{gap.gap_id}:7"

    async def test_all_ideas_nonconforming_raises(self, stores) -> None:
        sidecar, graph = stores
        gap = sidecar.list_gaps("run-z")[0]
        llm = FakeLlm(
            {
                "ideas": [
                    {
                        "text": "bad",
                        "operators": ["nope"],
                        "organon_position": "x",
                        "nearest_work_id": "W1",
                    }
                ]
            }
        )
        with pytest.raises(ValueError, match="non-conforming"):
            await expand_gap(gap, "run-z", 0, graph, llm, CATALOG)

    async def test_gap_without_work_evidence_raises(self, stores) -> None:
        _, graph = stores
        gap = _gap("g-web-only", [WEB_EVIDENCE], composite=0.5)
        with pytest.raises(ValueError, match="no work evidence"):
            await expand_gap(gap, "run-z", 0, graph, FakeLlm({"ideas": []}), CATALOG)


async def _persist_expansion(sidecar: Sidecar, graph: GraphStore) -> None:
    gap = sidecar.list_gaps("run-z")[0]
    payload, _ = _conforming_payload(f"run-z:{gap.gap_id}:0")
    expansion = await expand_gap(gap, "run-z", 0, graph, FakeLlm(payload), CATALOG)
    sidecar.put_expansion(expansion)


class TestAssembleReport:
    async def test_report_shape(self, stores) -> None:
        sidecar, graph = stores
        await _persist_expansion(sidecar, graph)
        report = assemble_report("run-z", sidecar, graph, WEIGHTS)
        assert report["field"] == FIELD
        assert report["run"]["run_id"] == "run-z"
        assert report["run"]["snapshot_size"] == 2
        assert report["weights"] == WEIGHTS
        assert {g["gap_id"] for g in report["gaps"]} == {"g1", "g2"}
        if report["ranking_source"] == "composite_score":
            assert [g["gap_id"] for g in report["gaps"]] == ["g1", "g2"]
        assert [g["rank"] for g in report["gaps"]] == [1, 2]
        g1 = next(g for g in report["gaps"] if g["gap_id"] == "g1")
        work_ev = next(e for e in g1["evidence"] if e.get("work_id") == "W1")
        assert work_ev["title"] == "Episodic memory for agents"
        assert work_ev["year"] == 2024
        assert len(g1["expansions"]) == 1
        assert report["examined_not_confirmed"] == [
            {
                "whitespace_id": "ws2",
                "kind": "thin_cell",
                "description": "forgetting curves for tool-use traces",
                "reason": "dense at depth",
            }
        ]

    async def test_report_is_json_able(self, stores) -> None:
        import json

        sidecar, graph = stores
        await _persist_expansion(sidecar, graph)
        report = assemble_report("run-z", sidecar, graph, WEIGHTS)
        json.dumps(report)

    def test_coarse_run_aggregates_child_zoom_gaps(self, stores) -> None:
        sidecar, graph = stores
        report = assemble_report("run-c", sidecar, graph, WEIGHTS)
        assert {g["gap_id"] for g in report["gaps"]} == {"g1", "g2"}

    def test_unknown_run_raises(self, stores) -> None:
        sidecar, graph = stores
        with pytest.raises(ValueError, match="unknown run"):
            assemble_report("run-nope", sidecar, graph, WEIGHTS)


class TestToMarkdown:
    async def test_citations_labels_and_sections(self, stores) -> None:
        sidecar, graph = stores
        await _persist_expansion(sidecar, graph)
        report = assemble_report("run-z", sidecar, graph, WEIGHTS)
        md = to_markdown(report)
        assert f"# Gap Report — {FIELD}" in md
        assert "[W1] Episodic memory for agents (2024), doi:10.1234/w1" in md
        assert "[W2] Consolidation in human memory (2021)" in md
        assert "https://example.org/agent-memory-survey (retrieved 2026-08-01)" in md
        assert "SPECULATIVE — ideonomy expansion (tuple:" in md
        assert "## Examined, not confirmed" in md
        assert "forgetting curves for tool-use traces — reason: dense at depth" in md
        picked = pick_tuple("run-z:g1:0", CATALOG)
        assert f"`[{picked.operators[0]}]`" in md
        assert "nearest work: [W1]" in md
        assert "sparsity 0.80" in md
        g1 = next(g for g in report["gaps"] if g["gap_id"] == "g1")
        assert f"composite {g1['composite_score']:.2f}" in md


class TestLintReport:
    async def _good_report(self, sidecar, graph) -> dict:
        await _persist_expansion(sidecar, graph)
        return assemble_report("run-z", sidecar, graph, WEIGHTS)

    async def test_good_report_is_clean(self, stores) -> None:
        sidecar, graph = stores
        report = await self._good_report(sidecar, graph)
        assert lint_report(report, sidecar, graph) == []

    async def test_evidence_work_missing_from_snapshot(self, stores) -> None:
        sidecar, graph = stores
        report = await self._good_report(sidecar, graph)
        bad = copy.deepcopy(report)
        bad["gaps"][0]["evidence"].append(
            {"kind": "work", "work_id": "W3", "title": "Off-snapshot work"}
        )
        violations = lint_report(bad, sidecar, graph)
        assert violations == [
            "gap g1: evidence work W3 not in Run Snapshot of zoom run run-z"
        ]

    async def test_evidence_work_missing_from_graph(self, stores) -> None:
        sidecar, graph = stores
        report = await self._good_report(sidecar, graph)
        bad = copy.deepcopy(report)
        bad["gaps"][0]["evidence"][0]["work_id"] = "W404"
        violations = lint_report(bad, sidecar, graph)
        assert any("W404 not in graph" in v for v in violations)
        assert any("W404 not in Run Snapshot" in v for v in violations)

    async def test_web_item_without_retrieved_at(self, stores) -> None:
        sidecar, graph = stores
        report = await self._good_report(sidecar, graph)
        bad = copy.deepcopy(report)
        for ev in bad["gaps"][0]["evidence"]:
            if ev["kind"] == "web":
                ev["retrieved_at"] = None
        violations = lint_report(bad, sidecar, graph)
        assert any("web evidence without retrieved_at" in v for v in violations)

    async def test_idea_citing_unknown_work(self, stores) -> None:
        sidecar, graph = stores
        report = await self._good_report(sidecar, graph)
        bad = copy.deepcopy(report)
        bad["gaps"][0]["expansions"][0]["ideas"][0]["nearest_work_id"] = "W404"
        violations = lint_report(bad, sidecar, graph)
        assert violations == [
            "gap g1 expansion attempt 0: idea cites unknown work 'W404'"
        ]

    async def test_gap_without_evidence_flagged(self, stores) -> None:
        sidecar, graph = stores
        report = await self._good_report(sidecar, graph)
        bad = copy.deepcopy(report)
        bad["gaps"][1]["evidence"] = []
        violations = lint_report(bad, sidecar, graph)
        assert any("no evidence" in v for v in violations)
        assert any("no citation marker" in v for v in violations)

    async def test_unverified_marker_flagged(self, stores) -> None:
        sidecar, graph = stores
        report = await self._good_report(sidecar, graph)
        bad = copy.deepcopy(report)
        bad["gaps"][0]["statement"] += " Agents forget everything. [unverified]"
        violations = lint_report(bad, sidecar, graph)
        assert violations == ["gap g1: statement contains '[unverified]'"]
