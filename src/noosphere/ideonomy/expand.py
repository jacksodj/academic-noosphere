"""Ideonomy Expansion (#15): apply a seeded method tuple to a confirmed Gap.

The Opus prompt lives here. Input is the gap statement + its evidence + the
nearest works (titles / abstract heads fetched from the graph for the gap's
work evidence) + the picked tuple's method bodies. Output is strict JSON;
ideas that cite operators outside the tuple or works outside the provided set
are dropped. The caller persists the returned ``IdeonomyExpansion``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noosphere.models import EvidenceItem, Gap, IdeonomyExpansion, IdeonomyIdea
from noosphere.ideonomy.picker import pick_tuple, tuple_bodies

if TYPE_CHECKING:
    from noosphere.graph import GraphStore

_ABSTRACT_HEAD_CHARS = 400

_SYSTEM = (
    "You are the Ideonomy Expansion engine of Academic Noosphere. You apply a "
    "randomized ideonomic method tuple (operators x organon x dimension-"
    "prompts) to a confirmed literature gap to generate SPECULATIVE research "
    "ideas. Every idea must be produced by operators from the given tuple and "
    "must cite its nearest existing work from the provided list. Respond with "
    "strict JSON only - no prose, no markdown fences."
)


def _evidence_line(ev: EvidenceItem) -> str:
    if ev.kind == "work":
        line = f"- work {ev.work_id}"
    else:
        retrieved = ev.retrieved_at.date().isoformat() if ev.retrieved_at else "?"
        line = f"- web {ev.url} (retrieved {retrieved})"
    if ev.quote:
        line += f' - "{ev.quote}"'
    return line


def _nearest_works_block(graph: "GraphStore", work_ids: list[str]) -> str:
    lines: list[str] = []
    for wid in work_ids:
        work = graph.get_work(wid)
        if work is None:
            continue
        head = ""
        if work.abstract:
            head = work.abstract[:_ABSTRACT_HEAD_CHARS]
            if len(work.abstract) > _ABSTRACT_HEAD_CHARS:
                head += "..."
        year = work.year if work.year is not None else "?"
        lines.append(f"[{work.openalex_id}] {work.title} ({year})")
        if head:
            lines.append(f"  abstract head: {head}")
    return "\n".join(lines)


def _build_user_prompt(
    gap: Gap, nearest_works: str, bodies: str, operators: list[str], work_ids: list[str]
) -> str:
    evidence = "\n".join(_evidence_line(ev) for ev in gap.evidence)
    schema = json.dumps(
        {
            "ideas": [
                {
                    "text": "one concrete speculative research idea",
                    "operators": ["<subset of the allowed operators>"],
                    "organon_position": "where the idea sits in the organon",
                    "nearest_work_id": "<one of the allowed work ids>",
                }
            ]
        },
        indent=2,
    )
    return (
        "## Gap statement\n"
        f"{gap.statement}\n\n"
        "## Gap evidence\n"
        f"{evidence}\n\n"
        "## Nearest works (the only citable works)\n"
        f"{nearest_works}\n\n"
        "## Ideonomy method tuple\n"
        f"{bodies}\n"
        "## Task\n"
        "Apply the tuple's operators, structured by the organon and probed "
        "along the dimension-prompts, to generate 3-8 speculative ideas that "
        "would address this gap.\n\n"
        "Return strict JSON matching exactly this shape:\n"
        f"{schema}\n\n"
        "Hard constraints:\n"
        f"- \"operators\" must be a non-empty subset of: {json.dumps(operators)}\n"
        f"- \"nearest_work_id\" must be one of: {json.dumps(work_ids)}\n"
        "- \"organon_position\" names the idea's position in the organon "
        "structure.\n"
        "- No keys beyond those shown; no text outside the JSON object."
    )


def _conforming_idea(
    raw: Any, allowed_ops: set[str], allowed_work_ids: set[str]
) -> IdeonomyIdea | None:
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    operators = raw.get("operators")
    organon_position = raw.get("organon_position")
    nearest_work_id = raw.get("nearest_work_id")
    if not (isinstance(text, str) and text.strip()):
        return None
    if not (
        isinstance(operators, list)
        and operators
        and all(isinstance(op, str) for op in operators)
        and set(operators) <= allowed_ops
    ):
        return None
    if not isinstance(organon_position, str) or not organon_position.strip():
        return None
    if nearest_work_id not in allowed_work_ids:
        return None
    return IdeonomyIdea(
        text=text,
        operators=operators,
        organon_position=organon_position,
        nearest_work_id=nearest_work_id,
    )


async def expand_gap(
    gap: Gap,
    run_id: str,
    attempt: int,
    graph: "GraphStore",
    llm: Any,
    catalog_dir: Path,
) -> IdeonomyExpansion:
    """Generate one Ideonomy Expansion for ``gap``. Reproducible per
    ``{run_id}:{gap_id}:{attempt}``; Re-roll = attempt N+1. Caller persists."""
    seed = f"{run_id}:{gap.gap_id}:{attempt}"
    picked = pick_tuple(seed, catalog_dir)
    bodies = tuple_bodies(picked, catalog_dir)

    work_ids = [
        ev.work_id for ev in gap.evidence if ev.kind == "work" and ev.work_id
    ]
    work_ids = [wid for wid in work_ids if graph.get_work(wid) is not None]
    if not work_ids:
        raise ValueError(
            f"gap {gap.gap_id}: no work evidence resolvable in the graph - "
            "cannot ground nearest-work citations"
        )

    user = _build_user_prompt(
        gap, _nearest_works_block(graph, work_ids), bodies, picked.operators, work_ids
    )
    response = await llm.opus_json(_SYSTEM, user)

    if not isinstance(response, dict) or not isinstance(response.get("ideas"), list):
        raise ValueError(
            f"gap {gap.gap_id}: expansion response is not {{'ideas': [...]}}"
        )
    allowed_ops = set(picked.operators)
    allowed_work_ids = set(work_ids)
    ideas = [
        idea
        for raw in response["ideas"]
        if (idea := _conforming_idea(raw, allowed_ops, allowed_work_ids)) is not None
    ]
    if not ideas:
        raise ValueError(
            f"gap {gap.gap_id}: all ideas dropped as non-conforming "
            f"(seed {seed})"
        )
    return IdeonomyExpansion(gap_id=gap.gap_id, attempt=attempt, tuple=picked, ideas=ideas)
