"""Grounding linter for assembled Gap Reports.

Every report is linted against the Run Snapshot (the reproducibility artifact)
and the graph before it leaves the app. Empty result = grounded.
"""

from __future__ import annotations

from typing import Any

from noosphere.reports.gaps import to_markdown

UNVERIFIED_MARKER = "[unverified]"


def _snapshot(sidecar: Any, cache: dict[str, set[str]], run_id: str | None) -> set[str]:
    if not run_id:
        return set()
    if run_id not in cache:
        cache[run_id] = set(sidecar.get_run_works(run_id))
    return cache[run_id]


def lint_report(report: dict, sidecar: Any, graph: Any) -> list[str]:
    """Return grounding violations; an empty list means the report is grounded."""
    violations: list[str] = []
    graph_ids: set[str] = graph.work_ids()
    snapshots: dict[str, set[str]] = {}

    for gap in report.get("gaps", []):
        gid = gap.get("gap_id", "<unknown>")
        zoom_run_id = gap.get("zoom_run_id")
        snapshot = _snapshot(sidecar, snapshots, zoom_run_id)

        evidence = gap.get("evidence", [])
        if not evidence:
            violations.append(f"gap {gid}: no evidence — ungrounded gap statement")
        for ev in evidence:
            kind = ev.get("kind")
            if kind == "work":
                work_id = ev.get("work_id")
                if not work_id:
                    violations.append(f"gap {gid}: work evidence without work_id")
                    continue
                if work_id not in snapshot:
                    violations.append(
                        f"gap {gid}: evidence work {work_id} not in Run Snapshot "
                        f"of zoom run {zoom_run_id}"
                    )
                if work_id not in graph_ids:
                    violations.append(
                        f"gap {gid}: evidence work {work_id} not in graph"
                    )
            elif kind == "web":
                if not ev.get("url"):
                    violations.append(f"gap {gid}: web evidence without url")
                if not ev.get("retrieved_at"):
                    violations.append(
                        f"gap {gid}: web evidence without retrieved_at "
                        f"(url: {ev.get('url')})"
                    )
            else:
                violations.append(f"gap {gid}: evidence with unknown kind {kind!r}")

        statement = gap.get("statement", "")
        if UNVERIFIED_MARKER in statement:
            violations.append(
                f"gap {gid}: statement contains {UNVERIFIED_MARKER!r}"
            )

        for exp in gap.get("expansions", []):
            attempt = exp.get("attempt")
            for idea in exp.get("ideas", []):
                nearest = idea.get("nearest_work_id")
                if not nearest or nearest not in graph_ids:
                    violations.append(
                        f"gap {gid} expansion attempt {attempt}: idea cites "
                        f"unknown work {nearest!r}"
                    )

    violations.extend(_lint_markdown(report))
    return violations


def _lint_markdown(report: dict) -> list[str]:
    try:
        md = to_markdown(report)
    except Exception as exc:  # defensive: a broken render is a grounding failure
        return [f"markdown: to_markdown failed: {exc}"]
    violations: list[str] = []
    for gap in report.get("gaps", []):
        gid = gap.get("gap_id", "<unknown>")
        markers: list[str] = []
        for ev in gap.get("evidence", []):
            if ev.get("kind") == "work" and ev.get("work_id"):
                markers.append(f"[{ev['work_id']}]")
            elif ev.get("kind") == "web" and ev.get("url"):
                markers.append(ev["url"])
        if not markers or not any(marker in md for marker in markers):
            violations.append(
                f"gap {gid}: rendered markdown has no citation marker for it"
            )
    return violations
