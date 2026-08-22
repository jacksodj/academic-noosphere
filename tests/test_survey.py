"""SurveyService tests (all fakes / in-memory; no network) plus unit tests for
the Web Search parsing helpers.

The sidecar and graph are the real implementations in tmp dirs; OpenAlex and
Web Search are canned fakes; embeddings come from the deterministic
StubEmbedder (or a table-driven embedder where exact similarities matter).
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noosphere.config import Settings
from noosphere.graph import GraphStore
from noosphere.models import EvidenceItem, Run, RunPhase, RunStatus, WhitespaceCandidate
from noosphere.pipeline.embed import StubEmbedder
from noosphere.pipeline.queue import Checkpoint
from noosphere.pipeline.survey import SurveyService, centroid, cosine, embed_text
from noosphere.sidecar import Sidecar
from noosphere.sources.openalex import short_id
from noosphere.sources.websearch import (
    DiscoveredRef,
    WebSearchClient,
    extract_doi,
    parse_result_items,
    parse_tool_content,
    pick_websearch_tool,
    refs_from_items,
)

_HAS_MCP = importlib.util.find_spec("mcp") is not None


# -- fixtures and fakes --------------------------------------------------------


def raw_work(
    wid: str,
    title: str,
    *,
    abstract: str | None = None,
    referenced: tuple[str, ...] = (),
    topics: tuple[str, ...] = (),
    cited_by: int = 0,
    doi: str | None = None,
    authors: tuple[str, ...] = (),
    source: str | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": f"https://openalex.org/{wid}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "display_name": title,
        "publication_year": 2024,
        "cited_by_count": cited_by,
        "abstract_inverted_index": (
            {w: [i] for i, w in enumerate(abstract.split())} if abstract else None
        ),
        "referenced_works": [f"https://openalex.org/{r}" for r in referenced],
        "topics": [
            {"id": f"https://openalex.org/{t}", "display_name": t, "score": 0.9}
            for t in topics
        ],
        "authorships": [
            {
                "author": {"id": f"https://openalex.org/{a}", "display_name": a},
                "institutions": [],
            }
            for a in authors
        ],
    }
    if source:
        raw["primary_location"] = {
            "source": {
                "id": f"https://openalex.org/{source}",
                "display_name": source,
                "type": "journal",
            }
        }
    return raw


class FakeOpenAlex:
    """Canned OpenAlex client; records calls for resume assertions."""

    def __init__(self) -> None:
        self.search_results: dict[str, list[dict]] = {}
        self.by_doi: dict[str, list[dict]] = {}
        self.works: dict[str, dict] = {}
        self.links: dict[str, tuple[list[str], list[str]]] = {}
        self.search_calls: list[tuple[str, int, dict | None]] = []
        self.batch_calls: list[list[str]] = []

    def add(self, raw: dict) -> dict:
        self.works[short_id(raw["id"])] = raw
        return raw

    async def works_search(
        self, query: str, per_page: int = 25, filters: dict | None = None
    ) -> list[dict]:
        self.search_calls.append((query, per_page, filters))
        if filters and "doi" in filters:
            return self.by_doi.get(filters["doi"], [])
        return self.search_results.get(query, [])

    async def works_batch(self, openalex_ids: list[str]) -> list[dict]:
        self.batch_calls.append(list(openalex_ids))
        return [self.works[i] for i in openalex_ids if i in self.works]

    async def referenced_and_citing(self, openalex_id: str) -> tuple[list[str], list[str]]:
        return self.links.get(short_id(openalex_id), ([], []))


class FakeWebSearch:
    """Returns canned refs; mirrors the real client's snippet-dropping."""

    def __init__(self, refs: list[DiscoveredRef]) -> None:
        self.refs = refs
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        max_results: int = 10,
        include_domains: list[str] | None = None,
        want_snippets: bool = False,
    ) -> list[DiscoveredRef]:
        self.calls.append({"query": query, "want_snippets": want_snippets})
        if want_snippets:
            return list(self.refs)
        return [replace(r, snippet=None) for r in self.refs]


class TableEmbedder:
    """Maps exact texts to preset vectors, for similarity-order tests."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self.table = table

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self.table[t]) for t in texts]


@pytest.fixture
def sidecar(tmp_path: Path) -> Sidecar:
    s = Sidecar(tmp_path / "sidecar.duckdb")
    yield s
    s.close()


@pytest.fixture
def graph(tmp_path: Path) -> GraphStore:
    return GraphStore(tmp_path / "graph")


def make_checkpoint(sidecar: Sidecar, job_id: str = "job-1") -> Checkpoint:
    sidecar.job_put(job_id, "survey", {}, "running", None)
    return Checkpoint(sidecar, job_id)


def make_run(sidecar: Sidecar, run_id: str = "run-1", phase: RunPhase = RunPhase.COARSE, **kw) -> Run:
    run = Run(run_id=run_id, field_name="memory for AI agents", phase=phase, **kw)
    sidecar.create_run(run)
    return run


def make_service(
    sidecar: Sidecar,
    graph: GraphStore,
    openalex: FakeOpenAlex,
    settings: Settings,
    embedder=None,
    websearch=None,
    on_activity=None,
) -> SurveyService:
    return SurveyService(
        sidecar=sidecar,
        graph=graph,
        openalex=openalex,
        embedder=embedder or StubEmbedder(),
        settings=settings,
        websearch=websearch,
        on_activity=on_activity,
    )


# -- coarse pass ---------------------------------------------------------------


async def test_coarse_happy_path(sidecar: Sidecar, graph: GraphStore) -> None:
    fake = FakeOpenAlex()
    s1 = fake.add(
        raw_work(
            "W1",
            "agent memory survey",
            abstract="episodic memory architectures for agents",
            referenced=("W3",),
            topics=("T1",),
            cited_by=100,
            authors=("A1",),
            source="S9",
        )
    )
    fake.add(raw_work("W2", "consolidation in agents", topics=("T1",)))
    fake.add(raw_work("W3", "hippocampal replay", topics=("T1",)))
    fake.add(raw_work("W4", "quantum chromodynamics", topics=("T9",), referenced=("W1",)))
    fake.search_results = {"q1": [s1], "q2": [fake.works["W2"]]}
    fake.links = {"W1": (["W3"], ["W4"])}

    settings = Settings(web_search_enabled=False)
    service = make_service(sidecar, graph, fake, settings)
    run = make_run(sidecar)
    checkpoint = make_checkpoint(sidecar)

    await service.run_coarse(run, ["q1", "q2"], checkpoint)

    assert sidecar.get_run_works("run-1") == ["W1", "W2", "W3"]
    assert graph.work_ids() == {"W1", "W2", "W3"}
    assert graph.citation_edges() == [("W1", "W3")]

    w1 = graph.get_work("W1")
    assert w1 is not None
    assert w1.embedding is not None and len(w1.embedding) == 768
    assert w1.cited_by_count == 100

    assert ("W1", "T1", 0.9) in graph.work_topic_rows()
    assert graph.query(
        "MATCH (w:Work)-[:PUBLISHED_IN]->(s:Source) RETURN w.openalex_id, s.openalex_id"
    ) == [["W1", "S9"]]
    assert graph.query(
        "MATCH (a:Author)-[:AUTHORED]->(w:Work) RETURN a.openalex_id, w.openalex_id"
    ) == [["A1", "W1"]]

    stored = sidecar.get_run("run-1")
    assert stored is not None
    assert stored.status is RunStatus.COMPLETED
    assert stored.started_at is not None and stored.finished_at is not None

    state = checkpoint.get()
    assert state is not None
    assert state["done"] == ["seeds", "expand", "relevance", "persist"]


async def test_resume_skips_seeds_stage(sidecar: Sidecar, graph: GraphStore) -> None:
    fake = FakeOpenAlex()
    fake.add(raw_work("W1", "agent memory survey", topics=("T1",)))

    settings = Settings(web_search_enabled=False)
    service = make_service(sidecar, graph, fake, settings)
    run = make_run(sidecar)
    checkpoint = make_checkpoint(sidecar)
    checkpoint.save({"done": ["seeds"], "seed_ids": ["W1"]})

    await service.run_coarse(run, ["q1"], checkpoint)

    assert fake.search_calls == []  # SEEDS was skipped; no search re-issued
    assert ["W1"] in fake.batch_calls  # rehydrated via works_batch instead
    assert sidecar.get_run_works("run-1") == ["W1"]
    assert sidecar.get_run("run-1").status is RunStatus.COMPLETED


async def test_relevance_keeps_zero_citation_work(
    sidecar: Sidecar, graph: GraphStore
) -> None:
    fake = FakeOpenAlex()
    seed = fake.add(
        raw_work(
            "W1",
            "agent memory survey",
            abstract="episodic memory architectures",
            topics=("T1",),
            cited_by=5000,
        )
    )
    # Same title+abstract => identical stub embedding => similarity 1.0 to the
    # centroid; zero citations and no topic overlap — must still be kept.
    fake.add(
        raw_work(
            "W5",
            "agent memory survey",
            abstract="episodic memory architectures",
            referenced=("W1",),
            cited_by=0,
        )
    )
    fake.search_results = {"q1": [seed]}
    fake.links = {"W1": ([], ["W5"])}

    settings = Settings(web_search_enabled=False)
    service = make_service(sidecar, graph, fake, settings)
    run = make_run(sidecar)

    await service.run_coarse(run, ["q1"], make_checkpoint(sidecar))

    assert "W5" in sidecar.get_run_works("run-1")
    w5 = graph.get_work("W5")
    assert w5 is not None and w5.cited_by_count == 0
    assert ("W5", "W1") in graph.citation_edges()


async def test_cap_enforced_by_similarity_order(
    sidecar: Sidecar, graph: GraphStore
) -> None:
    fake = FakeOpenAlex()
    seed = fake.add(raw_work("W1", "seed text"))
    fake.add(raw_work("W2", "close text"))
    fake.add(raw_work("W3", "further text"))
    fake.search_results = {"q1": [seed]}
    fake.links = {"W1": ([], ["W2", "W3"])}

    embedder = TableEmbedder(
        {
            "seed text": [1.0, 0.0, 0.0],
            "close text": [0.9, 0.436, 0.0],  # cos ~0.90
            "further text": [0.8, 0.6, 0.0],  # cos 0.80 — above threshold, capped out
        }
    )
    settings = Settings(web_search_enabled=False, coarse_corpus_target=2)
    service = make_service(sidecar, graph, fake, settings, embedder=embedder)
    run = make_run(sidecar)

    await service.run_coarse(run, ["q1"], make_checkpoint(sidecar))

    assert sidecar.get_run_works("run-1") == ["W1", "W2"]
    assert graph.work_ids() == {"W1", "W2"}


async def test_websearch_discovery_is_identifiers_only(
    sidecar: Sidecar, graph: GraphStore
) -> None:
    marker = "SNIPPET-MARKER-MUST-NOT-PERSIST"
    web_title = "WEB RESULT TITLE NEVER PERSISTED"
    websearch = FakeWebSearch(
        [
            DiscoveredRef(
                title=web_title,
                url="https://doi.org/10.1234/xyz",
                published_date_raw="2024-05-01",
                snippet=marker,
            )
        ]
    )

    fake = FakeOpenAlex()
    seed = fake.add(raw_work("W1", "agent memory survey", topics=("T1",)))
    resolved = fake.add(raw_work("W9", "resolved from scholarly api", topics=("T1",)))
    fake.search_results = {"q1": [seed]}
    fake.by_doi = {"10.1234/xyz": [resolved]}

    settings = Settings(web_search_enabled=True)
    service = make_service(sidecar, graph, fake, settings, websearch=websearch)
    run = make_run(sidecar)
    checkpoint = make_checkpoint(sidecar)

    await service.run_coarse(run, ["q1"], checkpoint)

    # Discovery ran without snippets and the DOI resolved through OpenAlex.
    assert websearch.calls == [{"query": "q1", "want_snippets": False}]
    assert "W9" in sidecar.get_run_works("run-1")
    assert graph.work_ids() == {"W1", "W9"}

    # Nothing snippet-like or web-result-like reaches sidecar or graph.
    job_dump = json.dumps(sidecar.job_get("job-1"))
    assert marker not in job_dump and web_title not in job_dump
    graph_text = json.dumps(graph.query("MATCH (w:Work) RETURN w.title, w.abstract"))
    assert marker not in graph_text and web_title not in graph_text
    w9 = graph.get_work("W9")
    assert w9 is not None and w9.title == "resolved from scholarly api"


async def test_failure_marks_run_failed(sidecar: Sidecar, graph: GraphStore) -> None:
    class ExplodingOpenAlex(FakeOpenAlex):
        async def works_search(self, query, per_page=25, filters=None):
            raise ConnectionError("openalex down")

    service = make_service(sidecar, graph, ExplodingOpenAlex(), Settings(web_search_enabled=False))
    run = make_run(sidecar)

    with pytest.raises(ConnectionError):
        await service.run_coarse(run, ["q1"], make_checkpoint(sidecar))

    assert sidecar.get_run("run-1").status is RunStatus.FAILED


# -- zoom pass -----------------------------------------------------------------


async def test_zoom_seeds_from_evidence_and_description_no_cap(
    sidecar: Sidecar, graph: GraphStore
) -> None:
    fake = FakeOpenAlex()
    fake.add(raw_work("W1", "memory planning bridge", topics=("T1",), referenced=("W6",)))
    fake.add(raw_work("W2", "planning with memory", topics=("T1",)))
    fake.add(raw_work("W6", "shared substrate", topics=("T1",)))
    description = "bridge between memory and planning"
    fake.search_results = {description: [fake.works["W2"]]}
    fake.links = {"W1": (["W6"], [])}

    coarse_run = make_run(sidecar, run_id="run-coarse")
    candidate = WhitespaceCandidate(
        whitespace_id="ws-1",
        run_id="run-coarse",
        kind="bridge",
        description=description,
        sparsity_score=0.8,
        evidence=[EvidenceItem(kind="work", work_id="W1")],
    )
    sidecar.put_whitespace(candidate)

    # coarse_corpus_target=1 would shrink a coarse pass — zoom must ignore it.
    settings = Settings(web_search_enabled=False, coarse_corpus_target=1)
    service = make_service(sidecar, graph, fake, settings)
    zoom_run = make_run(
        sidecar,
        run_id="run-zoom",
        phase=RunPhase.ZOOM,
        parent_run_id="run-coarse",
        whitespace_id="ws-1",
    )

    await service.run_zoom(zoom_run, candidate, make_checkpoint(sidecar))

    assert sidecar.get_run_works("run-zoom") == ["W1", "W2", "W6"]
    assert graph.work_ids() == {"W1", "W2", "W6"}
    [stored] = sidecar.list_whitespace("run-coarse")
    assert stored.status == "zooming"
    assert sidecar.get_run("run-zoom").status is RunStatus.COMPLETED


# -- embedding/similarity helpers ---------------------------------------------


def test_embed_text_joins_title_and_abstract() -> None:
    raw = raw_work("W1", "a title", abstract="some abstract words")
    assert embed_text(raw) == "a title. some abstract words"
    assert embed_text(raw_work("W2", "only title")) == "only title"


def test_cosine_and_centroid() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert centroid([]) == []
    assert centroid([[2.0, 0.0], [0.0, 2.0]]) == pytest.approx([2 ** -0.5, 2 ** -0.5])


# -- websearch parsing helpers -------------------------------------------------


def test_pick_websearch_tool_matches_gateway_prefixed_name() -> None:
    assert (
        pick_websearch_tool(["other-tool", "web-search-tool___WebSearch"])
        == "web-search-tool___WebSearch"
    )
    assert pick_websearch_tool(["WEBSEARCH"]) == "WEBSEARCH"
    assert pick_websearch_tool(["calculator"]) is None
    assert pick_websearch_tool([]) is None


def test_parse_result_items_happy_and_malformed() -> None:
    payload = json.dumps(
        {
            "results": [
                {"title": "T", "url": "https://x", "text": "snippet", "publishedDate": "2024-01-01"},
                "not-a-dict",
            ]
        }
    )
    items = parse_result_items(payload)
    assert items == [
        {"title": "T", "url": "https://x", "text": "snippet", "publishedDate": "2024-01-01"}
    ]
    assert parse_result_items("not json") == []
    assert parse_result_items(json.dumps(["list"])) == []
    assert parse_result_items(json.dumps({"results": "nope"})) == []


def test_refs_drop_snippets_by_default() -> None:
    items = [
        {"title": "T", "url": "https://x", "text": "SNIPPET", "publishedDate": "circa 2020"},
        {"title": "", "url": ""},  # no identifiers -> skipped
        {"title": "No date"},
    ]
    refs = refs_from_items(items)
    assert refs == [
        DiscoveredRef(title="T", url="https://x", published_date_raw="circa 2020"),
        DiscoveredRef(title="No date", url="", published_date_raw=None),
    ]
    assert all(r.snippet is None for r in refs)

    with_snippets = refs_from_items(items, want_snippets=True)
    assert with_snippets[0].snippet == "SNIPPET"


def test_parse_tool_content_reads_first_text_block() -> None:
    blocks = [
        SimpleNamespace(type="image", data=b""),
        SimpleNamespace(type="text", text="not json"),
        SimpleNamespace(
            type="text",
            text=json.dumps({"results": [{"title": "T", "url": "https://x"}]}),
        ),
    ]
    refs = parse_tool_content(blocks)
    assert [r.title for r in refs] == ["T"]
    assert parse_tool_content([]) == []


def test_extract_doi() -> None:
    assert extract_doi("https://doi.org/10.1234/abc.def") == "10.1234/abc.def"
    assert extract_doi("see 10.5555/xyz-1, cited often") == "10.5555/xyz-1"
    assert extract_doi("https://doi.org/10.1234/abc?utm=1") == "10.1234/abc"
    assert extract_doi("no identifiers here") is None
    assert extract_doi(None) is None
    assert extract_doi("") is None


@pytest.mark.skipif(_HAS_MCP, reason="mcp installed; call would attempt network")
async def test_websearch_client_missing_extra_raises_clear_error() -> None:
    client = WebSearchClient("https://gw.example/mcp", "us-east-1")
    with pytest.raises(RuntimeError, match="websearch"):
        await client.search("query")


async def test_activities_emitted_through_pipeline(
    sidecar: Sidecar, graph: GraphStore
) -> None:
    fake = FakeOpenAlex()
    s1 = fake.add(raw_work("W1", "agent memory survey", topics=("T1",)))
    fake.add(raw_work("W2", "consolidation in agents", topics=("T1",)))
    fake.search_results = {"q1": [s1]}
    fake.links = {"W1": ([], ["W2"])}

    lines: list[tuple[str, str]] = []
    service = make_service(
        sidecar, graph, fake, Settings(), on_activity=lambda rid, msg: lines.append((rid, msg))
    )
    run = make_run(sidecar)
    await service.run_coarse(run, ["q1"], make_checkpoint(sidecar))

    assert lines, "no activities emitted"
    assert all(rid == run.run_id for rid, _ in lines)
    text = "\n".join(msg for _, msg in lines)
    assert "Survey started" in text
    assert "OpenAlex search 'q1'" in text
    assert "Survey completed" in text
    # stages announce themselves in order
    stages = [m for _, m in lines if "stage complete" in m or "Relevance filter" in m]
    assert len(stages) >= 3
