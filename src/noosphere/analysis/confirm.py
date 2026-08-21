"""Zoom confirmation: three checks that turn a Whitespace Candidate into a Gap.

1. **Sparsity persists** — local citation density in the zoom snapshot
   (unique undirected edges / possible pairs) must stay low relative to the
   coarse sparsity estimate.
2. **Narrative demand** — ``mine_narrative`` over the zoom snapshot; the v1
   heuristic is that any claims found among region works counts as demand.
3. **Temporal profile** — bucketed from the ``Work.year`` distribution:
   never-started (fewer than 3 dated works), went-quiet (little activity in
   the last 3 years), or emerging (recent activity). Always classifiable, so
   it shapes the Gap's kinds and recency score rather than gating.

Passing candidates get an Opus-written grounded statement
(``gap_statement_prompt``), a persisted ``Gap``, and status ``"confirmed"``;
failing ones are persisted ``"not_confirmed"`` with a reason.
"""

import json
from datetime import datetime, timezone

from noosphere.analysis.narrative import mine_narrative
from noosphere.analysis.ranking import composite_score
from noosphere.analysis.whitespace import inverse_log_citedness
from noosphere.config import Settings
from noosphere.graph import GraphStore
from noosphere.llm.bedrock import LlmClient
from noosphere.llm.prompts import gap_statement_prompt
from noosphere.models import EvidenceItem, Gap, GapKind, WhitespaceCandidate, Work
from noosphere.sidecar import Sidecar

_MIN_ZOOM_SPARSITY = 0.5
_MIN_SPARSITY_PERSISTENCE = 0.5  # zoom sparsity must reach this fraction of coarse
_NARRATIVE_SATURATION = 4  # claims at which narrative_demand saturates to 1.0
_RECENT_YEARS = 3
_MAX_EVIDENCE = 12

SCORE_KEYS = ("sparsity", "narrative_demand", "recency", "low_citedness")


def _local_sparsity(edges: list[tuple[str, str]], n_works: int) -> tuple[float, float]:
    """(density, sparsity) over unique undirected pairs in the zoom snapshot."""
    undirected = {(min(a, b), max(a, b)) for a, b in edges if a != b}
    possible = n_works * (n_works - 1) / 2
    density = len(undirected) / possible if possible else 0.0
    return density, 1.0 - min(1.0, density)


def temporal_profile(years: list[int], *, ref_year: int | None = None) -> tuple[str, float]:
    """Classify a year distribution; returns (profile, recency in [0, 1]).

    recency = fraction of dated works from the last ``_RECENT_YEARS`` years.
    """
    if ref_year is None:
        ref_year = datetime.now(timezone.utc).year
    if not years:
        return "never_started", 0.0
    recent = sum(1 for y in years if y >= ref_year - (_RECENT_YEARS - 1))
    recency = min(1.0, recent / len(years))
    if len(years) < 3:
        return "never_started", recency
    if recency <= 0.2:
        return "went_quiet", recency
    return "emerging", recency


async def confirm_candidate(
    candidate: WhitespaceCandidate,
    zoom_run_id: str,
    graph: GraphStore,
    sidecar: Sidecar,
    llm: LlmClient,
) -> Gap | None:
    def refute(reason: str) -> None:
        candidate.status = "not_confirmed"
        candidate.not_confirmed_reason = reason
        sidecar.put_whitespace(candidate)

    zoom_ids = sidecar.get_run_works(zoom_run_id)
    if not zoom_ids:
        refute(f"zoom snapshot {zoom_run_id} is empty")
        return None

    works: dict[str, Work] = {}
    for wid in zoom_ids:
        w = graph.get_work(wid)
        if w is not None:
            works[wid] = w

    # (i) sparsity persists at zoom depth
    edges = graph.citation_edges(within=set(zoom_ids))
    density, zoom_sparsity = _local_sparsity(edges, len(zoom_ids))
    if (
        zoom_sparsity < _MIN_ZOOM_SPARSITY
        or zoom_sparsity < _MIN_SPARSITY_PERSISTENCE * candidate.sparsity_score
    ):
        refute(
            "sparsity did not persist at zoom depth: local citation density "
            f"{density:.3f} (zoom sparsity {zoom_sparsity:.2f} vs coarse "
            f"estimate {candidate.sparsity_score:.2f})"
        )
        return None

    # (ii) narrative demand among region works
    claims = await mine_narrative(zoom_ids, graph, llm)
    if not claims:
        refute("no narrative demand: zero stated claims mined from the zoom snapshot")
        return None
    narrative_demand = min(1.0, len(claims) / _NARRATIVE_SATURATION)

    # (iii) temporal profile
    years = [w.year for w in works.values() if w.year is not None]
    profile, recency = temporal_profile(years)

    kinds = [GapKind.STRUCTURAL, GapKind.NARRATIVE]
    if profile in ("never_started", "went_quiet"):
        kinds.append(GapKind.TEMPORAL)

    scores = {
        "sparsity": zoom_sparsity,
        "narrative_demand": narrative_demand,
        "recency": recency,
        "low_citedness": inverse_log_citedness(
            [w.cited_by_count for w in works.values()]
        ),
    }

    evidence: list[EvidenceItem] = list(candidate.evidence)
    seen = {(e.kind, e.work_id, e.url, e.quote) for e in evidence}
    for item in claims:
        key = (item.kind, item.work_id, item.url, item.quote)
        if key not in seen and len(evidence) < _MAX_EVIDENCE:
            seen.add(key)
            evidence.append(item)

    system, user = gap_statement_prompt(
        candidate.model_dump_json(),
        json.dumps([e.model_dump(mode="json") for e in evidence]),
    )
    statement = (await llm.opus_json(system, user))["statement"]

    gap = Gap(
        gap_id=f"gap-{zoom_run_id}-{candidate.whitespace_id}",
        whitespace_id=candidate.whitespace_id,
        zoom_run_id=zoom_run_id,
        kinds=kinds,
        statement=statement,
        evidence=evidence,
        scores=scores,
        composite_score=composite_score(scores, Settings().ranking_weights),
    )
    sidecar.put_gap(gap)
    candidate.status = "confirmed"
    candidate.not_confirmed_reason = None
    sidecar.put_whitespace(candidate)
    return gap
