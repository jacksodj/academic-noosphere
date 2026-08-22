"""GraphStore: the ladybug-backed literature graph.

The graph records what the literature is (Works, Authors, Topics, Sources,
Institutions and their edges); Run Snapshots live in the sidecar. Every node
and relationship carries provenance columns (source_api, source_id,
retrieved_at) per the grounding rule.

Graph algorithms do NOT run in ladybug (the ALGO extension cannot download in
this environment) — export edges via ``citation_edges`` / ``work_topic_rows``
and use ``noosphere.analysis.algos`` (igraph) instead.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import ladybug

from noosphere.models import (
    Author,
    Authorship,
    Citation,
    Institution,
    Provenance,
    Source,
    Topic,
    Work,
    WorkTopic,
)

_PROV_COLS = "source_api STRING, source_id STRING, retrieved_at STRING"

_SCHEMA_DDL: tuple[str, ...] = (
    "CREATE NODE TABLE IF NOT EXISTS Work("
    "openalex_id STRING, doi STRING, title STRING, year INT64, "
    "abstract STRING, cited_by_count INT64, embedding DOUBLE[], "
    f"{_PROV_COLS}, PRIMARY KEY(openalex_id))",
    "CREATE NODE TABLE IF NOT EXISTS Author("
    f"openalex_id STRING, display_name STRING, orcid STRING, {_PROV_COLS}, "
    "PRIMARY KEY(openalex_id))",
    "CREATE NODE TABLE IF NOT EXISTS Topic("
    f"openalex_id STRING, display_name STRING, level STRING, {_PROV_COLS}, "
    "PRIMARY KEY(openalex_id))",
    "CREATE NODE TABLE IF NOT EXISTS Source("
    f"openalex_id STRING, display_name STRING, type STRING, {_PROV_COLS}, "
    "PRIMARY KEY(openalex_id))",
    "CREATE NODE TABLE IF NOT EXISTS Institution("
    "openalex_id STRING, display_name STRING, ror STRING, "
    f"country_code STRING, {_PROV_COLS}, PRIMARY KEY(openalex_id))",
    f"CREATE REL TABLE IF NOT EXISTS AUTHORED(FROM Author TO Work, position INT64, {_PROV_COLS})",
    f"CREATE REL TABLE IF NOT EXISTS CITES(FROM Work TO Work, {_PROV_COLS})",
    f"CREATE REL TABLE IF NOT EXISTS ABOUT(FROM Work TO Topic, score DOUBLE, {_PROV_COLS})",
    f"CREATE REL TABLE IF NOT EXISTS PUBLISHED_IN(FROM Work TO Source, {_PROV_COLS})",
    f"CREATE REL TABLE IF NOT EXISTS AFFILIATED_WITH(FROM Author TO Institution, {_PROV_COLS})",
)


def _prov_params(p: Provenance) -> dict[str, Any]:
    return {
        "source_api": p.source_api,
        "source_id": p.source_id,
        "retrieved_at": p.retrieved_at.isoformat(),
    }


class GraphStore:
    def __init__(self, db_path: Path) -> None:
        self._db = ladybug.Database(str(db_path))
        self._con = ladybug.Connection(self._db)

    def close(self) -> None:
        """Flush and close the database (checkpoints the WAL)."""
        self._db.close()

    def _execute(self, cypher: str, params: dict[str, Any] | None = None) -> Any:
        if params is None:
            return self._con.execute(cypher)
        return self._con.execute(cypher, params)

    def _rows(self, cypher: str, params: dict[str, Any] | None = None) -> list[list]:
        result = self._execute(cypher, params)
        rows: list[list] = []
        while result.has_next():
            rows.append(result.get_next())
        return rows

    # -- schema ---------------------------------------------------------------

    def init_schema(self) -> None:
        for ddl in _SCHEMA_DDL:
            self._execute(ddl)
        try:
            self._execute(
                "CALL CREATE_VECTOR_INDEX('Work', 'work_embedding_idx', 'embedding')"
            )
        except Exception:
            pass  # vector/HNSW extension unavailable in this environment

    # -- node upserts ---------------------------------------------------------

    def _upsert_node(self, table: str, set_clause: str, params: dict[str, Any]) -> None:
        self._execute(
            f"MERGE (n:{table} {{openalex_id: $openalex_id}}) "
            f"ON CREATE SET {set_clause} ON MATCH SET {set_clause}",
            params,
        )

    def upsert_works(self, works: list[Work]) -> None:
        set_clause = (
            "n.doi=$doi, n.title=$title, n.year=$year, n.abstract=$abstract, "
            "n.cited_by_count=$cited_by_count, n.embedding=$embedding, "
            "n.source_api=$source_api, n.source_id=$source_id, n.retrieved_at=$retrieved_at"
        )
        for w in works:
            self._upsert_node(
                "Work",
                set_clause,
                {
                    "openalex_id": w.openalex_id,
                    "doi": w.doi,
                    "title": w.title,
                    "year": w.year,
                    "abstract": w.abstract,
                    "cited_by_count": w.cited_by_count,
                    "embedding": w.embedding,
                    **_prov_params(w.provenance),
                },
            )

    def upsert_authors(self, authors: list[Author]) -> None:
        set_clause = (
            "n.display_name=$display_name, n.orcid=$orcid, "
            "n.source_api=$source_api, n.source_id=$source_id, n.retrieved_at=$retrieved_at"
        )
        for a in authors:
            self._upsert_node(
                "Author",
                set_clause,
                {
                    "openalex_id": a.openalex_id,
                    "display_name": a.display_name,
                    "orcid": a.orcid,
                    **_prov_params(a.provenance),
                },
            )

    def upsert_topics(self, topics: list[Topic]) -> None:
        set_clause = (
            "n.display_name=$display_name, n.level=$level, "
            "n.source_api=$source_api, n.source_id=$source_id, n.retrieved_at=$retrieved_at"
        )
        for t in topics:
            self._upsert_node(
                "Topic",
                set_clause,
                {
                    "openalex_id": t.openalex_id,
                    "display_name": t.display_name,
                    "level": t.level,
                    **_prov_params(t.provenance),
                },
            )

    def upsert_sources(self, sources: list[Source]) -> None:
        set_clause = (
            "n.display_name=$display_name, n.type=$type, "
            "n.source_api=$source_api, n.source_id=$source_id, n.retrieved_at=$retrieved_at"
        )
        for s in sources:
            self._upsert_node(
                "Source",
                set_clause,
                {
                    "openalex_id": s.openalex_id,
                    "display_name": s.display_name,
                    "type": s.type,
                    **_prov_params(s.provenance),
                },
            )

    def upsert_institutions(self, insts: list[Institution]) -> None:
        set_clause = (
            "n.display_name=$display_name, n.ror=$ror, n.country_code=$country_code, "
            "n.source_api=$source_api, n.source_id=$source_id, n.retrieved_at=$retrieved_at"
        )
        for i in insts:
            self._upsert_node(
                "Institution",
                set_clause,
                {
                    "openalex_id": i.openalex_id,
                    "display_name": i.display_name,
                    "ror": i.ror,
                    "country_code": i.country_code,
                    **_prov_params(i.provenance),
                },
            )

    # -- edge upserts ---------------------------------------------------------

    def add_authorships(self, a: list[Authorship]) -> None:
        for edge in a:
            self._execute(
                "MATCH (a:Author {openalex_id: $author_id}), (w:Work {openalex_id: $work_id}) "
                "MERGE (a)-[r:AUTHORED]->(w) "
                "ON CREATE SET r.position=$position, r.source_api=$source_api, "
                "r.source_id=$source_id, r.retrieved_at=$retrieved_at "
                "ON MATCH SET r.position=$position, r.source_api=$source_api, "
                "r.source_id=$source_id, r.retrieved_at=$retrieved_at",
                {
                    "author_id": edge.author_id,
                    "work_id": edge.work_id,
                    "position": edge.position,
                    **_prov_params(edge.provenance),
                },
            )

    def add_citations(self, c: list[Citation]) -> None:
        for edge in c:
            self._execute(
                "MATCH (a:Work {openalex_id: $citing_id}), (b:Work {openalex_id: $cited_id}) "
                "MERGE (a)-[r:CITES]->(b) "
                "ON CREATE SET r.source_api=$source_api, r.source_id=$source_id, "
                "r.retrieved_at=$retrieved_at",
                {
                    "citing_id": edge.citing_id,
                    "cited_id": edge.cited_id,
                    **_prov_params(edge.provenance),
                },
            )

    def add_work_topics(self, wt: list[WorkTopic]) -> None:
        for edge in wt:
            self._execute(
                "MATCH (w:Work {openalex_id: $work_id}), (t:Topic {openalex_id: $topic_id}) "
                "MERGE (w)-[r:ABOUT]->(t) "
                "ON CREATE SET r.score=$score, r.source_api=$source_api, "
                "r.source_id=$source_id, r.retrieved_at=$retrieved_at "
                "ON MATCH SET r.score=$score, r.source_api=$source_api, "
                "r.source_id=$source_id, r.retrieved_at=$retrieved_at",
                {
                    "work_id": edge.work_id,
                    "topic_id": edge.topic_id,
                    "score": edge.score,
                    **_prov_params(edge.provenance),
                },
            )

    # -- reads ----------------------------------------------------------------

    def work_ids(self) -> set[str]:
        return {row[0] for row in self._rows("MATCH (w:Work) RETURN w.openalex_id")}

    def get_work(self, openalex_id: str) -> Work | None:
        rows = self._rows(
            "MATCH (w:Work {openalex_id: $id}) "
            "RETURN w.openalex_id, w.doi, w.title, w.year, w.abstract, "
            "w.cited_by_count, w.embedding, w.source_api, w.source_id, w.retrieved_at",
            {"id": openalex_id},
        )
        if not rows:
            return None
        (oid, doi, title, year, abstract, cited_by, embedding, s_api, s_id, ret_at) = rows[0]
        return Work(
            openalex_id=oid,
            doi=doi,
            title=title,
            year=year,
            abstract=abstract,
            cited_by_count=cited_by,
            embedding=embedding,
            provenance=Provenance(
                source_api=s_api,
                source_id=s_id,
                retrieved_at=datetime.fromisoformat(ret_at),
            ),
        )

    def citation_edges(self, within: set[str] | None = None) -> list[tuple[str, str]]:
        if within is None:
            rows = self._rows(
                "MATCH (a:Work)-[:CITES]->(b:Work) RETURN a.openalex_id, b.openalex_id"
            )
        else:
            rows = self._rows(
                "MATCH (a:Work)-[:CITES]->(b:Work) "
                "WHERE a.openalex_id IN $ids AND b.openalex_id IN $ids "
                "RETURN a.openalex_id, b.openalex_id",
                {"ids": sorted(within)},
            )
        return [(row[0], row[1]) for row in rows]

    def work_topic_rows(self) -> list[tuple[str, str, float]]:
        rows = self._rows(
            "MATCH (w:Work)-[r:ABOUT]->(t:Topic) "
            "RETURN w.openalex_id, t.openalex_id, r.score"
        )
        return [(row[0], row[1], row[2]) for row in rows]

    def query(self, cypher: str) -> list[list]:
        return self._rows(cypher)
