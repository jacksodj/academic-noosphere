import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Sigma from "sigma";
import { getReport, getWhitespace, listRuns } from "../endpoints";
import type { GapReport, ReportCommunity, Run, WhitespaceCandidate } from "../types";
import { workUrl } from "../components";
import { candidateStat, parseCandidate } from "../whitespace";

interface ThemeColors {
  text: string;
  muted: string;
  border: string;
  accent: string;
  warn: string;
  bgRaised: string;
}

function readTheme(): ThemeColors {
  const s = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => s.getPropertyValue(name).trim() || fallback;
  return {
    text: v("--text", "#1c1c1a"),
    muted: v("--text-muted", "#6f6f6a"),
    border: v("--border", "#c9c9c2"),
    accent: v("--accent", "#4657c8"),
    warn: v("--warn", "#9a6a00"),
    bgRaised: v("--bg-raised", "#ffffff"),
  };
}

type Selection =
  | { type: "community"; community: ReportCommunity }
  | { type: "whitespace"; candidate: WhitespaceCandidate }
  | null;

/**
 * Graph Explorer (view 4 of 5, ticket #14) — community-map default lens.
 * Built entirely from /api/runs/{id}/whitespace + the child zoom run's report
 * JSON (`communities` + `community_edges` + `works`); no dedicated graph
 * endpoint in v1, and the work-level full graph stays out of the render —
 * drill-in is a member-work list.
 */
export default function Explorer() {
  const [params, setParams] = useSearchParams();
  const runParam = params.get("run");

  const [runs, setRuns] = useState<Run[] | null>(null);
  const [candidates, setCandidates] = useState<WhitespaceCandidate[] | null>(null);
  const [reports, setReports] = useState<GapReport[]>([]);
  const [reportChecked, setReportChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);

  const coarseRuns = useMemo(() => (runs ?? []).filter((r) => r.phase === "coarse"), [runs]);
  const runId = runParam ?? coarseRuns[0]?.run_id ?? null;

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!runId || !runs) return;
    setCandidates(null);
    setReports([]);
    setReportChecked(false);
    setSelection(null);
    getWhitespace(runId)
      .then(setCandidates)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    // Community map data rides in the report JSON of a completed child zoom run.
    const zoomRuns = runs.filter(
      (r) => r.phase === "zoom" && r.parent_run_id === runId && r.status === "completed",
    );
    // Default lens shows everything we know: communities from EVERY completed
    // zoom report of this coarse run, merged (ids namespaced per report).
    Promise.allSettled(zoomRuns.map((r) => getReport(r.run_id))).then((results) => {
      setReports(
        results
          .filter(
            (res): res is PromiseFulfilledResult<GapReport> =>
              res.status === "fulfilled" && res.value.communities.length > 0,
          )
          .map((res) => res.value),
      );
      setReportChecked(true);
    });
  }, [runId, runs]);

  // Merge community lists across all reports; keys are namespaced per report
  // so ids can't collide, and duplicate labels get a disambiguating suffix.
  const merged = useMemo(() => {
    const entries: { key: string; community: ReportCommunity; reportIdx: number }[] = [];
    reports.forEach((rep, ri) => {
      for (const c of rep.communities) {
        entries.push({ key: `c${ri}:${c.id}`, community: c, reportIdx: ri });
      }
    });
    if (entries.length === 0) {
      const ids = new Set<number>();
      for (const c of candidates ?? []) {
        if (c.community_a !== null) ids.add(c.community_a);
        if (c.community_b !== null) ids.add(c.community_b);
      }
      for (const id of [...ids].sort((a, b) => a - b)) {
        entries.push({
          key: `stub:${id}`,
          community: { id, label: `Community ${id}`, size: 50, top_topics: [], works: [] },
          reportIdx: -1,
        });
      }
    }
    entries.sort((a, b) => b.community.size - a.community.size);
    const labelCounts = new Map<string, number>();
    for (const e of entries) {
      const l = e.community.label ?? `Community ${e.community.id}`;
      labelCounts.set(l, (labelCounts.get(l) ?? 0) + 1);
    }
    const works: Record<string, import("../types").WorkRef> = {};
    for (const rep of reports) Object.assign(works, rep.works);
    return { entries, labelCounts, works };
  }, [reports, candidates]);
  const communities = useMemo(
    () => merged.entries.map((e) => e.community),
    [merged],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !candidates || !reportChecked || communities.length === 0) return;

    const theme = readTheme();
    const graph = new Graph({ multi: true });

    // Community super-nodes, seeded on a deterministic circle (size-sorted);
    // node size ~ sqrt(works). ForceAtlas2 below pulls citation-dense
    // communities together so clusters are spatial, not just labeled.
    const n = merged.entries.length;
    const R = 100;
    const pos = new Map<string, { x: number; y: number }>();
    // work id -> owning community key (from the report's per-community member
    // lists) — used to anchor whitespace nodes by their evidence works.
    const workCommunity = new Map<string, string>();
    merged.entries.forEach((entry, i) => {
      const c = entry.community;
      const angle = (2 * Math.PI * i) / n - Math.PI / 2;
      const x = R * Math.cos(angle);
      const y = R * Math.sin(angle);
      pos.set(entry.key, { x, y });
      for (const wid of c.works) {
        if (!workCommunity.has(wid)) workCommunity.set(wid, entry.key);
      }
      const base = c.label ?? `Community ${c.id}`;
      const dupe = (merged.labelCounts.get(base) ?? 0) > 1;
      graph.addNode(entry.key, {
        x,
        y,
        size: 6 + Math.sqrt(c.size) / 2.5,
        label: `${base}${dupe ? ` ·C${c.id}` : ""} (${c.size})`,
        color: theme.accent,
        kind: "community",
      });
    });

    // Inter-community edges (within each report), thickness ~ density weight;
    // `weight` drives the force layout's attraction.
    reports.forEach((rep, ri) => {
      for (const e of rep.community_edges) {
        const a = `c${ri}:${e.source}`;
        const b = `c${ri}:${e.target}`;
        if (pos.has(a) && pos.has(b)) {
          graph.addEdge(a, b, {
            size: 0.5 + e.weight * 5,
            color: theme.border,
            weight: 1 + e.weight * 10,
          });
        }
      }
    });

    // Whitespace nodes: seeded on an outer ring, then anchored by GHOST
    // edges to the communities that hold their evidence works — the force
    // layout pulls each candidate toward the region its missing citations
    // belong to (bridges settle between their two sides).
    const wsLabel = (c: WhitespaceCandidate): string => {
      const parsed = parseCandidate(c);
      const title =
        parsed.title.length > 32 ? `${parsed.title.slice(0, 32)}…` : parsed.title;
      return c.status === "candidate" ? title : `${title} (${c.status.replace("_", " ")})`;
    };
    const wsSize = (c: WhitespaceCandidate): number => {
      const expected = parseCandidate(c).expected;
      return expected ? Math.min(14, 4 + Math.sqrt(expected)) : 5;
    };
    const ghostColor = `${theme.warn}55`; // translucent: expected-but-missing citations
    let wsIdx = 0;
    for (const c of candidates) {
      const wsColor = c.status === "confirmed" ? theme.warn : theme.muted;
      const angle = (2 * Math.PI * wsIdx) / Math.max(1, candidates.length);
      wsIdx += 1;
      graph.addNode(c.whitespace_id, {
        x: 1.6 * R * Math.cos(angle),
        y: 1.6 * R * Math.sin(angle),
        size: wsSize(c),
        label: wsLabel(c),
        color: wsColor,
        kind: "whitespace",
      });
      const anchors = new Set<string>();
      for (const ev of c.evidence) {
        if (ev.work_id) {
          const key = workCommunity.get(ev.work_id);
          if (key) anchors.add(key);
        }
      }
      for (const key of [...anchors].slice(0, 4)) {
        graph.addEdge(c.whitespace_id, key, {
          size: 1,
          color: ghostColor,
          weight: 2,
        });
      }
    }

    // Force-directed layout: deterministic given the seeded positions and a
    // fixed iteration count. Citation-dense communities cluster spatially;
    // ghost edges place whitespace where its absent citations would run.
    forceAtlas2.assign(graph, {
      iterations: 400,
      settings: {
        ...forceAtlas2.inferSettings(graph),
        edgeWeightInfluence: 1,
        gravity: 0.8,
        scalingRatio: 12,
        slowDown: 5,
      },
    });

    const renderer = new Sigma(graph, container, {
      labelColor: { color: theme.text },
      labelSize: 12,
      labelDensity: 3,
      labelRenderedSizeThreshold: 3,
      defaultEdgeType: "line",
      renderEdgeLabels: false,
      minCameraRatio: 0.3,
      maxCameraRatio: 3,
    });

    renderer.on("clickNode", ({ node }) => {
      const kind = graph.getNodeAttribute(node, "kind") as string;
      if (kind === "community") {
        const entry = merged.entries.find((e) => e.key === node);
        if (entry) setSelection({ type: "community", community: entry.community });
      } else {
        const candidate = candidates.find((c) => c.whitespace_id === node);
        if (candidate) setSelection({ type: "whitespace", candidate });
      }
    });
    renderer.on("clickStage", () => setSelection(null));

    // Re-render with fresh palette when the OS theme flips.
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onTheme = () => {
      const t = readTheme();
      graph.forEachNode((node, attrs) => {
        if (attrs.kind === "community") graph.setNodeAttribute(node, "color", t.accent);
      });
      renderer.setSetting("labelColor", { color: t.text });
      renderer.refresh();
    };
    mq.addEventListener("change", onTheme);

    return () => {
      mq.removeEventListener("change", onTheme);
      renderer.kill();
    };
  }, [candidates, reports, reportChecked, merged]);

  const works = merged.works;

  return (
    <section>
      <div className="view-head">
        <h1>Graph Explorer</h1>
        {coarseRuns.length > 0 && (
          <label className="run-select">
            <span className="muted">Coarse run</span>
            <select value={runId ?? ""} onChange={(e) => setParams({ run: e.target.value })}>
              {coarseRuns.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id} — {r.field_name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {runs !== null && coarseRuns.length === 0 && (
        <div className="card empty-state">
          <h2>No coarse runs yet</h2>
          <p>The community map renders once a Survey's coarse pass has completed.</p>
        </div>
      )}

      {runId && (!candidates || !reportChecked) && !error && (
        <p className="muted">Loading community map…</p>
      )}

      {candidates && reportChecked && communities.length === 0 && (
        <div className="card empty-state">
          <h2>No community data</h2>
          <p>
            This run has no report communities and no bridge candidates to place; run a zoom pass
            to populate the map.
          </p>
        </div>
      )}

      {candidates && reportChecked && communities.length > 0 && (
        <div className="explorer-layout">
          <div className="graph-panel">
            <div ref={containerRef} className="sigma-container" />
            <div className="graph-legend muted small">
              <span>
                <span className="legend-dot" style={{ background: "var(--accent)" }} /> community
                (size ~ works)
              </span>
              <span>
                <span className="legend-dot" style={{ background: "var(--warn)" }} /> whitespace
                (confirmed)
              </span>
              <span>
                <span className="legend-dot" style={{ background: "var(--text-muted)" }} />{" "}
                whitespace (other)
              </span>
              <span>solid edge ~ citation density · faint edge = expected-but-missing citations (whitespace anchor)</span>
            </div>
            {reports.length === 0 && (
              <p className="muted small">
                No completed zoom report for this run yet — showing community stubs referenced by
                bridge candidates only.
              </p>
            )}
            {reports.length > 1 && (
              <p className="muted small">
                Showing merged communities from {reports.length} zoom regions.
              </p>
            )}
          </div>

          <aside className="drillin card">
            {selection === null && (
              <p className="muted">Click a community or whitespace node to drill in.</p>
            )}
            {selection?.type === "community" && (
              <>
                <h2>{selection.community.label ?? `Community ${selection.community.id}`}</h2>
                <p className="muted small">
                  {selection.community.size} works
                  {selection.community.top_topics.length > 0 &&
                    ` · ${selection.community.top_topics.join(" · ")}`}
                </p>
                {selection.community.works.length === 0 ? (
                  <p className="muted">No member works listed in the report payload.</p>
                ) : (
                  <ul className="drillin-works">
                    {selection.community.works.map((wid) => {
                      const w = works[wid];
                      return (
                        <li key={wid}>
                          <span className="mono small">[{wid}]</span>{" "}
                          <a
                            href={workUrl(wid, w?.doi)}
                            target="_blank"
                            rel="noreferrer"
                            className="work-link"
                            title={w?.doi ? `doi:${w.doi}` : `openalex.org/${wid}`}
                          >
                            {w?.title ?? <span className="muted">(unresolved — open on OpenAlex)</span>}
                          </a>
                          {w?.year && <span className="muted"> · {w.year}</span>}
                          {w?.doi && (
                            <div className="mono small muted">doi:{w.doi}</div>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </>
            )}
            {selection?.type === "whitespace" && (
              <>
                <h2>{parseCandidate(selection.candidate).title}</h2>
                <p className="muted small">
                  {selection.candidate.kind} · {selection.candidate.status.replace("_", " ")} ·
                  sparsity {selection.candidate.sparsity_score.toFixed(2)} · low-cited{" "}
                  {selection.candidate.low_citedness_signal.toFixed(2)} ·{" "}
                  <span className="mono">{parseCandidate(selection.candidate).shortId}</span>
                </p>
                {candidateStat(parseCandidate(selection.candidate)) && (
                  <p className="ws-stat">{candidateStat(parseCandidate(selection.candidate))}</p>
                )}
                <p className="muted small">{selection.candidate.description}</p>
                {selection.candidate.not_confirmed_reason && (
                  <p className="not-confirmed-reason">
                    {selection.candidate.not_confirmed_reason}
                  </p>
                )}
              </>
            )}
          </aside>
        </div>
      )}
    </section>
  );
}
