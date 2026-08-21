"""Domain models — the shared vocabulary in code. Terms match CONTEXT.md exactly.

Provenance rule (#9): everything persisted carries source_api / source_id /
retrieved_at. Author identity is only ever an OpenAlex author ID.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

SourceApi = Literal["openalex", "s2", "crossref", "arxiv", "pubmed", "websearch"]


class Provenance(BaseModel):
    source_api: SourceApi
    source_id: str
    retrieved_at: datetime


class Work(BaseModel):
    openalex_id: str  # canonical key, e.g. "W2741809807"
    doi: str | None = None
    title: str
    year: int | None = None
    abstract: str | None = None
    cited_by_count: int = 0  # analysis feature (gap signal), never an ingest filter
    embedding: list[float] | None = None  # 768-dim SPECTER2, populated at ingest
    provenance: Provenance


class Author(BaseModel):
    openalex_id: str
    display_name: str
    orcid: str | None = None
    provenance: Provenance


class Topic(BaseModel):
    openalex_id: str
    display_name: str
    level: Literal["domain", "field", "subfield", "topic"]
    provenance: Provenance


class Source(BaseModel):
    """Journal / conference / repository a Work is published in."""
    openalex_id: str
    display_name: str
    type: str | None = None
    provenance: Provenance


class Institution(BaseModel):
    openalex_id: str
    display_name: str
    ror: str | None = None
    country_code: str | None = None
    provenance: Provenance


class Authorship(BaseModel):
    author_id: str
    work_id: str
    position: int  # 0-based author order
    provenance: Provenance


class Citation(BaseModel):
    citing_id: str
    cited_id: str
    provenance: Provenance


class WorkTopic(BaseModel):
    work_id: str
    topic_id: str
    score: float
    provenance: Provenance


class RunPhase(StrEnum):
    COARSE = "coarse"
    ZOOM = "zoom"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class Run(BaseModel):
    """One Survey phase execution. Run Snapshots (run_works) live in the sidecar."""
    run_id: str
    field_name: str
    phase: RunPhase
    parent_run_id: str | None = None  # zoom runs point at their coarse run
    whitespace_id: str | None = None  # zoom runs point at their candidate
    query_manifest_hash: str | None = None
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None


class GapKind(StrEnum):
    STRUCTURAL = "structural"
    NARRATIVE = "narrative"
    TEMPORAL = "temporal"


class EvidenceItem(BaseModel):
    """A Grounded Claim's citation: DOI/OpenAlex ID, or URL + retrieval date."""
    kind: Literal["work", "web"]
    work_id: str | None = None
    url: str | None = None
    retrieved_at: datetime | None = None
    quote: str | None = None


class WhitespaceCandidate(BaseModel):
    whitespace_id: str
    run_id: str  # the coarse run that surfaced it
    kind: Literal["bridge", "thin_cell"]
    description: str
    community_a: int | None = None
    community_b: int | None = None
    topic_id: str | None = None
    sparsity_score: float
    low_citedness_signal: float = 0.0
    evidence: list[EvidenceItem] = Field(default_factory=list)
    status: Literal["candidate", "zooming", "confirmed", "not_confirmed"] = "candidate"
    not_confirmed_reason: str | None = None


class Gap(BaseModel):
    gap_id: str
    whitespace_id: str
    zoom_run_id: str
    kinds: list[GapKind]
    statement: str  # Opus-written, every claim cited
    evidence: list[EvidenceItem]
    scores: dict[str, float]  # component scores: sparsity, narrative_demand, recency, low_citedness
    composite_score: float


class IdeonomyTuple(BaseModel):
    operators: list[str]
    organon: str
    dimension_prompts: list[str]
    seed: str  # "{run_id}:{gap_id}:{attempt}" — reproducible


class IdeonomyIdea(BaseModel):
    text: str
    operators: list[str]  # which operators produced it
    organon_position: str
    nearest_work_id: str  # citation to nearest existing work — required


class IdeonomyExpansion(BaseModel):
    gap_id: str
    attempt: int
    tuple: IdeonomyTuple
    ideas: list[IdeonomyIdea]
