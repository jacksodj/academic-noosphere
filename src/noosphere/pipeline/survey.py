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

import asyncio
import math
import time
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
EMBED_BATCH = 512  # abstracts per off-loop embed call (progress + ETA cadence)
PERSIST_BATCH = 250  # work nodes per graph upsert (HNSW insert is the slow part)
CITATION_BATCH = 2000  # citation edges per graph write (40k edges ≈ 17min total)

SeedLoader = Callable[[], Awaitable[list[dict]]]


def _fmt_duration(seconds: int) -> str:
    """Human ETA: 45s, 3m 20s, 1h 12m."""
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


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
        on_activity: Callable[[str, str], None] | None = None,
        on_stage_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._sidecar = sidecar
        self._graph = graph
        self._openalex = openalex
        self._embedder = embedder
        self._settings = settings
        self._websearch = websearch
        self._on_activity = on_activity
        self._on_stage_progress = on_stage_progress
        self._active_run_id: str | None = None

    def _note(self, message: str) -> None:
        """Emit one human-readable activity line for the active run."""
        if self._on_activity is not None and self._active_run_id is not None:
            self._on_activity(self._active_run_id, message)

    def _stage_progress(self, payload: dict[str, Any]) -> None:
        """Emit transient sub-stage progress (SSE only, never persisted)."""
        if self._on_stage_progress is not None and self._active_run_id is not None:
            self._on_stage_progress(self._active_run_id, payload)

    async def _embed_all(self, texts: list[str]) -> list[list[float]]:
        """Embed in batches off the event loop, reporting count + ETA.

        The rate estimate uses the elapsed wall clock of this call, so the
        projection self-corrects as batches complete.
        """
        if len(texts) <= EMBED_BATCH:
            return await asyncio.to_thread(self._embedder.embed, texts)
        out: list[list[float]] = []
        started = time.monotonic()
        for i in range(0, len(texts), EMBED_BATCH):
            out.extend(
                await asyncio.to_thread(self._embedder.embed, texts[i : i + EMBED_BATCH])
            )
            completed = min(i + EMBED_BATCH, len(texts))
            elapsed = time.monotonic() - started
            rate = completed / elapsed if elapsed > 0 else 0.0
            eta_s = round((len(texts) - completed) / rate) if rate > 0 else None
            self._note(
                f"Embedding: {completed}/{len(texts)} abstracts"
                + (f" (~{_fmt_duration(eta_s)} remaining)" if eta_s else "")
            )
            self._stage_progress(
                {"stage": "relevance", "step": "embed",
                 "done": completed, "total": len(texts), "eta_s": eta_s}
            )
        return out

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
        self._active_run_id = run.run_id
        state: dict[str, Any] = checkpoint.get() or {}
        done: list[str] = list(state.get("done", []))
        if done:
            self._note(f"Resuming from checkpoint — stages done: {', '.join(done)}")
        else:
            self._note(f"Survey started ({run.phase} pass)")
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
        except Exception as exc:
            self._note(f"Run failed: {type(exc).__name__}: {exc}")
            self._sidecar.update_run(run.run_id, status=RunStatus.FAILED)
            raise
        self._note("Survey completed")
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
        self._note(f"Seed stage complete: {len(seed_ids)} unique works")
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
        self._note(f"Citation expansion: walking references/citations of {len(seed_ids)} seeds")
        neighbors: set[str] = set()
        for i, sid in enumerate(seed_ids, 1):
            referenced, citing = await self._openalex.referenced_and_citing(sid)
            neighbors.update(referenced)
            neighbors.update(citing)
            if i % 25 == 0 or i == len(seed_ids):
                self._note(
                    f"Citation expansion: {i}/{len(seed_ids)} seeds → "
                    f"{len(neighbors)} neighbors so far"
                )
        missing = sorted(n for n in neighbors if n not in works)
        if missing:
            self._note(f"Fetching metadata for {len(missing)} expansion works")
            for raw in await self._openalex.works_batch(missing):
                works[short_id(raw["id"])] = raw
        candidate_ids = sorted(works)
        self._note(f"Expand stage complete: {len(candidate_ids)} candidate works")
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
        self._note(f"Embedding {len(present)} abstracts (this is the long stage)")
        embedded = await self._embed_all([embed_text(works[w]) for w in present])
        vectors = dict(zip(present, embedded))
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
        self._note(
            f"Relevance filter kept {len(kept)} of {len(present)} works "
            f"(threshold {self._settings.relevance_threshold})"
        )
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
            embedded = await self._embed_all([embed_text(works[w]) for w in kept])
            vectors = dict(zip(kept, embedded))
        self._note(f"Persisting {len(kept)} works to graph + sidecar")
        await self._persist(run, works, kept, vectors)
        done.append("persist")
        state["done"] = done
        checkpoint.save(state)

    async def _hydrate(self, works: dict[str, dict], ids: list[str]) -> None:
        missing = [i for i in ids if i not in works]
        if not missing:
            return
        if len(missing) > 100:
            self._note(f"Hydrating metadata for {len(missing)} works from OpenAlex")
        # Chunked here (not just inside works_batch) so long hydrations report
        # progress into the activity feed instead of going silent for minutes.
        slice_size = 500
        for start in range(0, len(missing), slice_size):
            for raw in await self._openalex.works_batch(missing[start : start + slice_size]):
                works[short_id(raw["id"])] = raw
            fetched = min(start + slice_size, len(missing))
            if len(missing) > slice_size:
                self._note(f"Hydrating metadata: {fetched}/{len(missing)} works")

    # -- seed loaders ----------------------------------------------------------

    async def _coarse_seeds(self, seed_queries: list[str]) -> list[dict]:
        raws: list[dict] = []
        for query in seed_queries:
            found = await self._openalex.works_search(query, per_page=SEED_SEARCH_PER_PAGE)
            self._note(f"OpenAlex search {query!r} → {len(found)} works")
            raws.extend(found)
        if self._settings.web_search_enabled and self._websearch is not None:
            for query in seed_queries:
                resolved = await self._discover(query)
                self._note(f"Web Search discovery {query!r} → {len(resolved)} resolved works")
                raws.extend(resolved)
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

    async def _persist(
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

        self._note(
            f"Persist: parsed {len(all_works)} works "
            f"({len(authors)} authors, {len(topics)} topics, {len(citations)} citation edges)"
        )
        # All graph mutations run via to_thread: a single HNSW upsert chunk
        # blocks for tens of seconds, and on the event loop that freezes every
        # API request (observed as "the app stopped refreshing"). The worker
        # is the only graph writer; concurrent readers (report endpoint) go
        # through Ladybug's own scheduler.
        await asyncio.to_thread(self._graph.init_schema)
        # Work nodes carry the embeddings — HNSW insertion makes this the slow
        # phase (tens of minutes for a full corpus). Chunked for progress
        # reporting, plus count + ETA like the embed stage.
        started = time.monotonic()
        for i in range(0, len(all_works), PERSIST_BATCH):
            await asyncio.to_thread(
                self._graph.upsert_works, all_works[i : i + PERSIST_BATCH]
            )
            completed = min(i + PERSIST_BATCH, len(all_works))
            elapsed = time.monotonic() - started
            rate = completed / elapsed if elapsed > 0 else 0.0
            eta_s = round((len(all_works) - completed) / rate) if rate > 0 else None
            if len(all_works) > PERSIST_BATCH:
                self._note(
                    f"Persist: {completed}/{len(all_works)} work nodes written"
                    + (f" (~{_fmt_duration(eta_s)} remaining)" if eta_s else "")
                )
                self._stage_progress(
                    {"stage": "persist", "step": "work nodes",
                     "done": completed, "total": len(all_works), "eta_s": eta_s}
                )
        self._note("Persist: work nodes written")
        await asyncio.to_thread(self._graph.upsert_authors, list(authors.values()))
        await asyncio.to_thread(self._graph.upsert_topics, list(topics.values()))
        await asyncio.to_thread(self._graph.upsert_sources, list(sources.values()))
        await asyncio.to_thread(self._graph.upsert_institutions, list(institutions.values()))
        self._note("Persist: author/topic/source/institution nodes written")
        await asyncio.to_thread(self._graph.add_authorships, authorships)
        await asyncio.to_thread(self._graph.add_work_topics, work_topics)
        # Citation edges dominate persist wall-clock (observed 17min for 40k
        # edges) — chunked with awaits + ETA like the work nodes above.
        started = time.monotonic()
        for i in range(0, len(citations), CITATION_BATCH):
            await asyncio.to_thread(
                self._graph.add_citations, citations[i : i + CITATION_BATCH]
            )
            completed = min(i + CITATION_BATCH, len(citations))
            elapsed = time.monotonic() - started
            rate = completed / elapsed if elapsed > 0 else 0.0
            eta_s = round((len(citations) - completed) / rate) if rate > 0 else None
            if len(citations) > CITATION_BATCH:
                self._note(
                    f"Persist: {completed}/{len(citations)} citation edges written"
                    + (f" (~{_fmt_duration(eta_s)} remaining)" if eta_s else "")
                )
                self._stage_progress(
                    {"stage": "persist", "step": "citation edges",
                     "done": completed, "total": len(citations), "eta_s": eta_s}
                )
        self._note(f"Persist: {len(citations)} citation edges written")
        await asyncio.to_thread(self._add_published_in, published_in)
        self._sidecar.add_run_works(run.run_id, kept)
        self._note("Persist: run snapshot recorded")

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
