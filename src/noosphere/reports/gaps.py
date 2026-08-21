"""Gap Report assembly and Markdown rendering.

``assemble_report`` produces a JSON-able dict (field, run info, ranked gaps
with component scores, examined-not-confirmed candidates, Ideonomy Expansions
per gap). ``to_markdown`` renders it under the grounding rule: every gap
section lists its evidence as citations, and expansions are labeled
SPECULATIVE.

Ranking defers to ``noosphere.analysis.ranking`` (built in parallel) when it
exposes ``rank`` or ``composite``; otherwise gaps are sorted locally on their
persisted ``composite_score``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from noosphere.models import Gap, Run, RunPhase


def _ranked_gaps(gaps: list[Gap], weights: dict) -> tuple[list[Gap], str]:
    try:
        from noosphere.analysis import ranking  # built by a parallel agent
    except ImportError:
        ranking = None
    if ranking is not None:
        rank_fn = getattr(ranking, "rank_gaps", None) or getattr(ranking, "rank", None)
        if callable(rank_fn):
            try:
                return list(rank_fn(gaps, weights)), f"analysis.ranking.{rank_fn.__name__}"
            except Exception:
                pass
        composite_fn = getattr(ranking, "composite_score", None) or getattr(
            ranking, "composite", None
        )
        if callable(composite_fn):
            for key in (
                lambda g: composite_fn(g.scores, weights),
                lambda g: composite_fn(g, weights),
            ):
                try:
                    return (
                        sorted(gaps, key=key, reverse=True),
                        "analysis.ranking.composite",
                    )
                except Exception:
                    continue
    return sorted(gaps, key=lambda g: g.composite_score, reverse=True), "composite_score"


def _enriched_evidence(gap: Gap, graph: Any) -> list[dict]:
    items: list[dict] = []
    for ev in gap.evidence:
        item = ev.model_dump(mode="json")
        if ev.kind == "work" and ev.work_id:
            work = graph.get_work(ev.work_id)
            if work is not None:
                item["title"] = work.title
                item["year"] = work.year
                item["doi"] = work.doi
        items.append(item)
    return items


def _collect_gaps(run: Run, sidecar: Any) -> list[Gap]:
    if run.phase == RunPhase.ZOOM:
        return sidecar.list_gaps(run.run_id)
    zoom_ids = [
        r.run_id
        for r in sidecar.list_runs(run.field_name)
        if r.parent_run_id == run.run_id
    ]
    return [g for zid in zoom_ids for g in sidecar.list_gaps(zid)]


MAX_COMMUNITY_WORKS = 30  # per-community drill-in cap for the report's works index


def _communities_block(run_id: str, sidecar: Any, graph: Any) -> tuple[dict, list, list]:
    """Community map for the Explorer lens: (works_index, communities, edges).

    Best-effort — a report without communities is still a valid report.
    """
    try:
        from noosphere.analysis.algos import (
            inter_community_edge_density,
            louvain_communities,
        )
        ids = set(sidecar.get_run_works(run_id))
        if not ids:
            return {}, [], []
        edges = graph.citation_edges(within=ids)
        communities = louvain_communities(edges, ids)
        topic_names = {row[0]: row[1] for row in graph.query(
            "MATCH (t:Topic) RETURN t.openalex_id, t.display_name")}
        by_comm: dict[int, list[str]] = {}
        for wid, cid in communities.items():
            by_comm.setdefault(cid, []).append(wid)
        topic_rows = [r for r in graph.work_topic_rows() if r[0] in ids]
        comm_topics: dict[int, dict[str, float]] = {}
        for wid, tid, score in topic_rows:
            cid = communities.get(wid)
            if cid is not None:
                comm_topics.setdefault(cid, {})
                comm_topics[cid][tid] = comm_topics[cid].get(tid, 0.0) + score
        comm_list = []
        works_index: dict[str, dict] = {}
        for cid, members in sorted(by_comm.items()):
            top = sorted(comm_topics.get(cid, {}).items(), key=lambda kv: -kv[1])[:3]
            top_names = [topic_names.get(tid, tid) for tid, _ in top]
            shown = sorted(members)[:MAX_COMMUNITY_WORKS]
            for wid in shown:
                works_index.setdefault(wid, {})
            comm_list.append({
                "id": cid,
                "label": top_names[0] if top_names else f"Community {cid}",
                "size": len(members),
                "top_topics": top_names,
                "works": shown,
            })
        density = inter_community_edge_density(edges, communities)
        max_d = max(density.values(), default=0.0) or 1.0
        comm_edges = [
            {"source": a, "target": b, "weight": round(d / max_d, 4)}
            for (a, b), d in sorted(density.items())
            if d > 0
        ]
        return works_index, comm_list, comm_edges
    except Exception:
        return {}, [], []


def _fill_works_index(index: dict[str, dict], gap_dicts: list[dict], graph: Any) -> dict:
    """Resolve every cited work id (evidence, ideas, community members) to
    {work_id, title, year, doi} for the SPA's citation-chip hover cards."""
    for body in gap_dicts:
        for ev in body.get("evidence", []):
            if ev.get("work_id"):
                index.setdefault(ev["work_id"], {})
        for exp in body.get("expansions", []):
            for idea in exp.get("ideas", []):
                if idea.get("nearest_work_id"):
                    index.setdefault(idea["nearest_work_id"], {})
    resolved: dict[str, dict] = {}
    for wid in index:
        work = graph.get_work(wid)
        if work is not None:
            resolved[wid] = {"work_id": wid, "title": work.title,
                             "year": work.year, "doi": work.doi}
        else:
            resolved[wid] = {"work_id": wid, "title": None, "year": None, "doi": None}
    return resolved


def assemble_report(run_id: str, sidecar: Any, graph: Any, weights: dict) -> dict:
    """Assemble the Gap Report for a run (zoom run: its gaps; coarse run: gaps
    from all its zoom runs). JSON-able throughout."""
    run = sidecar.get_run(run_id)
    if run is None:
        raise ValueError(f"unknown run: {run_id}")
    coarse_run_id = run.run_id if run.phase == RunPhase.COARSE else run.parent_run_id

    ranked, ranking_source = _ranked_gaps(_collect_gaps(run, sidecar), weights)
    gap_dicts: list[dict] = []
    for position, gap in enumerate(ranked, start=1):
        body = gap.model_dump(mode="json")
        body["rank"] = position
        body["evidence"] = _enriched_evidence(gap, graph)
        body["expansions"] = [
            e.model_dump(mode="json") for e in sidecar.list_expansions(gap.gap_id)
        ]
        gap_dicts.append(body)

    candidates = sidecar.list_whitespace(coarse_run_id) if coarse_run_id else []
    examined = [
        {
            "whitespace_id": w.whitespace_id,
            "kind": w.kind,
            "description": w.description,
            "reason": w.not_confirmed_reason,
        }
        for w in candidates
        if w.status == "not_confirmed"
    ]

    works_index, communities, community_edges = _communities_block(run_id, sidecar, graph)

    return {
        "field": run.field_name,
        "works": _fill_works_index(works_index, gap_dicts, graph),
        "communities": communities,
        "community_edges": community_edges,
        "run": {
            "run_id": run.run_id,
            "phase": run.phase.value,
            "status": run.status.value,
            "parent_run_id": run.parent_run_id,
            "whitespace_id": run.whitespace_id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "snapshot_size": len(sidecar.get_run_works(run_id)),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weights": dict(weights),
        "ranking_source": ranking_source,
        "gaps": gap_dicts,
        "examined_not_confirmed": examined,
    }


# -- Markdown rendering -------------------------------------------------------


def _citation(ev: dict) -> str | None:
    if ev.get("kind") == "work" and ev.get("work_id"):
        title = ev.get("title") or "(title unavailable)"
        year = ev.get("year") if ev.get("year") is not None else "?"
        line = f"[{ev['work_id']}] {title} ({year})"
        if ev.get("doi"):
            line += f", doi:{ev['doi']}"
        return line
    if ev.get("kind") == "web" and ev.get("url"):
        retrieved = (ev.get("retrieved_at") or "?")[:10]
        return f"{ev['url']} (retrieved {retrieved})"
    return None


def _scores_line(gap: dict) -> str:
    parts = [f"{name} {value:.2f}" for name, value in sorted(gap.get("scores", {}).items())]
    parts.append(f"composite {gap.get('composite_score', 0.0):.2f}")
    return " · ".join(parts)


def _expansion_md(exp: dict) -> list[str]:
    t = exp.get("tuple", {})
    tuple_desc = (
        f"{' + '.join(t.get('operators', []))} × {t.get('organon', '?')} × "
        f"{' / '.join(t.get('dimension_prompts', []))}"
    )
    lines = [
        f"#### SPECULATIVE — ideonomy expansion (tuple: {tuple_desc})",
        "",
        f"_Attempt {exp.get('attempt')}, seed `{t.get('seed', '?')}`. Ideas below "
        "are speculative, not Grounded Claims; each cites its nearest existing "
        "work._",
        "",
    ]
    for idea in exp.get("ideas", []):
        badges = " ".join(f"`[{op}]`" for op in idea.get("operators", []))
        lines.append(
            f"- {idea.get('text', '')} {badges} — organon position: "
            f"{idea.get('organon_position', '?')} — nearest work: "
            f"[{idea.get('nearest_work_id', '?')}]"
        )
    lines.append("")
    return lines


def to_markdown(report: dict) -> str:
    run = report.get("run", {})
    lines: list[str] = [
        f"# Gap Report — {report.get('field', '?')}",
        "",
        f"Run `{run.get('run_id', '?')}` ({run.get('phase', '?')}) · "
        f"status: {run.get('status', '?')} · "
        f"Run Snapshot: {run.get('snapshot_size', 0)} works",
        "",
        f"Generated: {report.get('generated_at', '?')} · "
        f"ranking: {report.get('ranking_source', '?')}",
        "",
        "## Gaps",
        "",
    ]
    gaps = report.get("gaps", [])
    if not gaps:
        lines += ["_No confirmed gaps for this run._", ""]
    for gap in gaps:
        kinds = ", ".join(gap.get("kinds", []))
        lines += [
            f"### {gap.get('rank', '?')}. {gap.get('gap_id', '?')} ({kinds})",
            "",
            gap.get("statement", ""),
            "",
            "**Evidence**",
            "",
        ]
        for ev in gap.get("evidence", []):
            citation = _citation(ev)
            if citation is None:
                continue
            line = f"- {citation}"
            if ev.get("quote"):
                line += f' — "{ev["quote"]}"'
            lines.append(line)
        lines += ["", f"**Component scores** — {_scores_line(gap)}", ""]
        for exp in gap.get("expansions", []):
            lines += _expansion_md(exp)

    lines += ["## Examined, not confirmed", ""]
    examined = report.get("examined_not_confirmed", [])
    if not examined:
        lines.append("_All whitespace candidates that were zoomed are confirmed._")
    for item in examined:
        reason = item.get("reason") or "no reason recorded"
        lines.append(
            f"- `{item.get('whitespace_id', '?')}` ({item.get('kind', '?')}): "
            f"{item.get('description', '')} — reason: {reason}"
        )
    lines.append("")
    return "\n".join(lines)
