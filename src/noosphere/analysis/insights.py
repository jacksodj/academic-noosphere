"""Corpus insights over a Run Snapshot: most-cited works and recently-active
research areas. Pure reads over the graph — every number traces to stored
works/topics (grounding rule); no LLM involved.

"Recent" is publication-year based (OpenAlex gives no month), so an
"last 18 months" style question maps to years >= current_year - 1; the
cutoff year is returned so the UI can label the window honestly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def corpus_insights(
    run_id: str,
    sidecar: Any,
    graph: Any,
    *,
    top_works: int = 15,
    top_topics: int = 15,
    ref_year: int | None = None,
) -> dict:
    if ref_year is None:
        ref_year = datetime.now(timezone.utc).year
    recent_cutoff = ref_year - 1  # year granularity ≈ "last 18 months"

    snapshot = sidecar.get_run_works(run_id)
    snapshot_set = set(snapshot)

    works: dict[str, Any] = {}
    year_histogram: dict[int, int] = {}
    for wid in snapshot:
        w = graph.get_work(wid)
        if w is None:
            continue
        works[wid] = w
        if w.year is not None:
            year_histogram[w.year] = year_histogram.get(w.year, 0) + 1

    most_cited = sorted(
        works.values(), key=lambda w: (-w.cited_by_count, w.openalex_id)
    )[:top_works]

    topic_names = {
        row[0]: row[1]
        for row in graph.query("MATCH (t:Topic) RETURN t.openalex_id, t.display_name")
    }
    topic_total: dict[str, int] = {}
    topic_recent: dict[str, int] = {}
    for wid, topic_id, _score in graph.work_topic_rows():
        if wid not in snapshot_set:
            continue
        w = works.get(wid)
        if w is None:
            continue
        topic_total[topic_id] = topic_total.get(topic_id, 0) + 1
        if w.year is not None and w.year >= recent_cutoff:
            topic_recent[topic_id] = topic_recent.get(topic_id, 0) + 1

    active = sorted(
        (
            {
                "topic_id": tid,
                "name": topic_names.get(tid, tid),
                "recent_count": n,
                "total_count": topic_total.get(tid, n),
                "recent_share": round(n / topic_total.get(tid, n), 3),
            }
            for tid, n in topic_recent.items()
        ),
        key=lambda t: (-t["recent_count"], t["topic_id"]),
    )[:top_topics]

    return {
        "run_id": run_id,
        "snapshot_size": len(snapshot),
        "resolved_works": len(works),
        "recent_cutoff_year": recent_cutoff,
        "top_cited": [
            {
                "work_id": w.openalex_id,
                "title": w.title,
                "year": w.year,
                "doi": w.doi,
                "cited_by_count": w.cited_by_count,
            }
            for w in most_cited
        ],
        "active_topics": active,
        "year_histogram": {str(y): n for y, n in sorted(year_histogram.items())},
    }
