"""Two-phase Survey orchestrator (#11/#12).

`run_coarse` builds the coarse core corpus (seed-and-expand, relevance =
embedding similarity + topic overlap only — citation counts are NEVER an
ingest filter), persists it to the graph, and records the Run Snapshot.
`run_zoom` reuses the same machinery over a Whitespace Candidate's bounded
region (evidence works + description-derived queries, depth 1, no cap).

Both are checkpoint-idempotent job handlers: a checkpoint is saved after each
stage (seeds -> expand -> relevance -> persist) and a resume skips completed
stages, rehydrating raw records via `works_batch` (served by the sidecar's
immutable response cache in production).

Edge notes (v1): PUBLISHED_IN edges are written through the `graph.query`
escape hatch; AFFILIATED_WITH is skipped because `parse_work` returns
institutions without the author<->institution pairing.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from noosphere.config import Settings
from noosphere.graph import GraphStore
from noosphere.models import Citation, Provenance, Run, RunStatus, WhitespaceCandidate
from noosphere.pipeline.embed import Embedder
from noosphere.pipeline.queue import Checkpoint
from noosphere.sidecar import Sidecar
from noosphere.sources.openalex import OpenAlexClient, reconstruct_abstract, short_id
from noosphere.sources.websearch import WebSearchClient, extract_doi

DISCOVERY_RESOLVE_PER_PAGE = 2
SEED_SEARCH_PER_PAGE = 25

SeedLoader = Callable[[], Awaitable[list[dict]]]


def embed_text(raw: dict) -> str:
    title = raw.get("display_name") or raw.get("title") or ""
    abstract = reconstruct_abstract(raw.get("abstract_inverted_index"))
    return f"{title}. {abstract}" if abstract else title


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    mean = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in mean)) or 1.0
    return [x / norm for x in mean]


def _topic_ids(raw: dict) -> set[str]:
    return {short_id(t["id"]) for t in raw.get("topics") or [] if t.get("id")}


def _cypher_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class SurveyService:
    def __init__(
        self,
        sidecar: Sidecar,
        graph: GraphStore,
        openalex: OpenAlexClient,
        embedder: Embedder,
        settings: Settings,
        websearch: WebSearchClient | None = None,
    ) -> None:
        self._sidecar = sidecar
        self._graph = graph
        self._openalex = openalex
        self._embedder = embedder
        self._settings = settings
        self._websearch = websearch

    # -- public entry points ---------------------------------------------------

    async def run_coarse(
        self, run: Run, seed_queries: list[str], checkpoint: Checkpoint
    ) -> None:
        await self._execute(
            run,
            checkpoint,
            seed_loader=lambda: self._coarse_seeds(seed_queries),
            cap=self._settings.coarse_corpus_target,
        )

    async def run_zoom(
        self, run: Run, candidate: WhitespaceCandidate, checkpoint: Checkpoint
    ) -> None:
        candidate.status = "zooming"
        self._sidecar.put_whitespace(candidate)
        await self._execute(
            run,
            checkpoint,
            seed_loader=lambda: self._zoom_seeds(candidate),
            cap=None,
        )

    # -- staged pipeline -------------------------------------------------------

    async def _execute(
        self, run: Run, checkpoint: Checkpoint, seed_loader: SeedLoader, cap: int | None
    ) -> None:
        state: dict[str, Any] = checkpoint.get() or {}
        done: list[str] = list(state.get("done", []))
        if not done:
            self._sidecar.update_run(
                run.run_id,
                status=RunStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
        else:
            self._sidecar.update_run(run.run_id, status=RunStatus.RUNNING)

        works: dict[str, dict] = {}
        try:
            seed_ids = await self._stage_seeds(state, done, checkpoint, seed_loader, works)
            candidate_ids = await self._stage_expand(state, done, checkpoint, seed_ids, works)
            kept_ids, vectors = await self._stage_relevance(
                state, done, checkpoint, seed_ids, candidate_ids, works, cap
            )
            await self._stage_persist(state, done, checkpoint, run, kept_ids, works, vectors)
        except Exception:
            self._sidecar.update_run(run.run_id, status=RunStatus.FAILED)
            raise
        self._sidecar.update_run(
            run.run_id,
            status=RunStatus.COMPLETED,
            finished_at=datetime.now(timezone.utc),
        )

    async def _stage_seeds(
        self,
        state: dict[str, Any],
        done: list[str],
        checkpoint: Checkpoint,
        seed_loader: SeedLoader,
        works: dict[str, dict],
    ) -> list[str]:
        if "seeds" in done:
            return list(state["seed_ids"])
        for raw in await seed_loader():
            works[short_id(raw["id"])] = raw
        seed_ids = sorted(works)
        state["seed_ids"] = seed_ids
        done.append("seeds")
        state["done"] = done
        checkpoint.save(state)
        return seed_ids

    async def _stage_expand(
        self,
        state: dict[str, Any],
        done: list[str],
        checkpoint: Checkpoint,
        seed_ids: list[str],
        works: dict[str, dict],
    ) -> list[str]:
        if "expand" in done:
            return list(state["candidate_ids"])
        await self._hydrate(works, seed_ids)
        neighbors: set[str] = set()
        for sid in seed_ids:
            referenced, citing = await self._openalex.referenced_and_citing(sid)
            neighbors.update(referenced)
            neighbors.update(citing)
        missing = sorted(n for n in neighbors if n not in works)
        if missing:
            for raw in await self._openalex.works_batch(missing):
                works[short_id(raw["id"])] = raw
        candidate_ids = sorted(works)
        state["candidate_ids"] = candidate_ids
        done.append("expand")
        state["done"] = done
        checkpoint.save(state)
        return candidate_ids

    async def _stage_relevance(
        self,
        state: dict[str, Any],
        done: list[str],
        checkpoint: Checkpoint,
        seed_ids: list[str],
        candidate_ids: list[str],
        works: dict[str, dict],
        cap: int | None,
    ) -> tuple[list[str], dict[str, list[float]] | None]:
        if "relevance" in done:
            return list(state["kept_ids"]), None
        await self._hydrate(works, candidate_ids)
        present = [wid for wid in candidate_ids if wid in works]
        vectors = dict(
            zip(present, self._embedder.embed([embed_text(works[w]) for w in present]))
        )
        field_centroid = centroid([vectors[s] for s in seed_ids if s in vectors])
        seed_topics: set[str] = set()
        for sid in seed_ids:
            if sid in works:
                seed_topics |= _topic_ids(works[sid])

        similarity = {
            wid: cosine(vectors[wid], field_centroid) if field_centroid else 0.0
            for wid in present
        }
        kept = [
            wid
            for wid in present
            if similarity[wid] >= self._settings.relevance_threshold
            or (_topic_ids(works[wid]) & seed_topics)
        ]
        kept.sort(key=lambda wid: similarity[wid], reverse=True)
        if cap is not None:
            kept = kept[:cap]
        state["kept_ids"] = kept
        done.append("relevance")
        state["done"] = done
        checkpoint.save(state)
        return kept, vectors

    async def _stage_persist(
        self,
        state: dict[str, Any],
        done: list[str],
        checkpoint: Checkpoint,
        run: Run,
        kept_ids: list[str],
        works: dict[str, dict],
        vectors: dict[str, list[float]] | None,
    ) -> None:
        if "persist" in done:
            return
        await self._hydrate(works, kept_ids)
        kept = [wid for wid in kept_ids if wid in works]
        if vectors is None:
            vectors = dict(
                zip(kept, self._embedder.embed([embed_text(works[w]) for w in kept]))
            )
        self._persist(run, works, kept, vectors)
        done.append("persist")
        state["done"] = done
        checkpoint.save(state)

    async def _hydrate(self, works: dict[str, dict], ids: list[str]) -> None:
        missing = [i for i in ids if i not in works]
        if not missing:
            return
        for raw in await self._openalex.works_batch(missing):
            works[short_id(raw["id"])] = raw

    # -- seed loaders ----------------------------------------------------------

    async def _coarse_seeds(self, seed_queries: list[str]) -> list[dict]:
        raws: list[dict] = []
        for query in seed_queries:
            raws.extend(
                await self._openalex.works_search(query, per_page=SEED_SEARCH_PER_PAGE)
            )
        if self._settings.web_search_enabled and self._websearch is not None:
            for query in seed_queries:
                raws.extend(await self._discover(query))
        return raws

    async def _discover(self, query: str) -> list[dict]:
        """Web Search Discovery: refs -> resolved OpenAlex records.

        Identifiers only — the DiscoveredRefs themselves (titles/urls/dates,
        no snippets requested) are dropped after Resolution; only resolved
        scholarly-API records move forward.
        """
        assert self._websearch is not None
        refs = await self._websearch.search(query)
        raws: list[dict] = []
        for ref in refs:
            doi = extract_doi(ref.url) or extract_doi(ref.title)
            if doi:
                results = await self._openalex.works_search(
                    "", per_page=DISCOVERY_RESOLVE_PER_PAGE, filters={"doi": doi}
                )
            elif ref.title:
                results = await self._openalex.works_search(
                    ref.title, per_page=DISCOVERY_RESOLVE_PER_PAGE
                )
            else:
                continue
            if results:
                raws.append(results[0])
        return raws

    async def _zoom_seeds(self, candidate: WhitespaceCandidate) -> list[dict]:
        evidence_ids = [
            e.work_id for e in candidate.evidence if e.kind == "work" and e.work_id
        ]
        raws: list[dict] = []
        if evidence_ids:
            raws.extend(await self._openalex.works_batch(evidence_ids))
        if candidate.description:
            raws.extend(
                await self._openalex.works_search(
                    candidate.description, per_page=SEED_SEARCH_PER_PAGE
                )
            )
        return raws

    # -- graph + snapshot persistence -----------------------------------------

    def _persist(
        self,
        run: Run,
        works: dict[str, dict],
        kept: list[str],
        vectors: dict[str, list[float]],
    ) -> None:
        kept_set = set(kept)
        all_works = []
        authors: dict[str, Any] = {}
        topics: dict[str, Any] = {}
        sources: dict[str, Any] = {}
        institutions: dict[str, Any] = {}
        authorships: list[Any] = []
        work_topics: list[Any] = []
        citations: list[Citation] = []
        published_in: list[tuple[str, str, Provenance]] = []

        for wid in kept:
            raw = works[wid]
            (
                work,
                w_authors,
                w_authorships,
                w_work_topics,
                w_topics,
                source,
                w_institutions,
            ) = OpenAlexClient.parse_work(raw)
            work.embedding = vectors.get(wid)
            all_works.append(work)
            for a in w_authors:
                authors[a.openalex_id] = a
            authorships.extend(w_authorships)
            work_topics.extend(w_work_topics)
            for t in w_topics:
                topics[t.openalex_id] = t
            if source is not None:
                sources[source.openalex_id] = source
                published_in.append((wid, source.openalex_id, work.provenance))
            for inst in w_institutions:
                institutions[inst.openalex_id] = inst
            for ref in raw.get("referenced_works") or []:
                rid = short_id(ref)
                if rid in kept_set:
                    citations.append(
                        Citation(citing_id=wid, cited_id=rid, provenance=work.provenance)
                    )

        self._graph.init_schema()
        self._graph.upsert_works(all_works)
        self._graph.upsert_authors(list(authors.values()))
        self._graph.upsert_topics(list(topics.values()))
        self._graph.upsert_sources(list(sources.values()))
        self._graph.upsert_institutions(list(institutions.values()))
        self._graph.add_authorships(authorships)
        self._graph.add_work_topics(work_topics)
        self._graph.add_citations(citations)
        self._add_published_in(published_in)
        self._sidecar.add_run_works(run.run_id, kept)

    def _add_published_in(self, edges: list[tuple[str, str, Provenance]]) -> None:
        for work_id, source_id, prov in edges:
            literals = tuple(
                _cypher_literal(v)
                for v in (
                    work_id,
                    source_id,
                    prov.source_api,
                    prov.source_id,
                    prov.retrieved_at.isoformat(),
                )
            )
            self._graph.query(
                "MATCH (w:Work {openalex_id: '%s'}), (s:Source {openalex_id: '%s'}) "
                "MERGE (w)-[r:PUBLISHED_IN]->(s) "
                "ON CREATE SET r.source_api='%s', r.source_id='%s', r.retrieved_at='%s'"
                % literals
            )
