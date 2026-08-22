import Graph from "graphology";
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
  const [report, setReport] = useState<GapReport | null>(null);
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
    setReport(null);
    setReportChecked(false);
    setSelection(null);
    getWhitespace(runId)
      .then(setCandidates)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    // Community map data rides in the report JSON of a completed child zoom run.
    const zoomRuns = runs.filter(
      (r) => r.phase === "zoom" && r.parent_run_id === runId && r.status === "completed",
    );
    Promise.allSettled(zoomRuns.map((r) => getReport(r.run_id))).then((results) => {
      const found = results.find(
        (res): res is PromiseFulfilledResult<GapReport> =>
          res.status === "fulfilled" && res.value.communities.length > 0,
      );
      setReport(found?.value ?? null);
      setReportChecked(true);
    });
  }, [runId, runs]);

  // Derive the community list: report communities, else stubs for community
  // ids referenced by bridge candidates (coarse run not yet zoomed).
  const communities = useMemo<ReportCommunity[]>(() => {
    if (report && report.communities.length > 0) return report.communities;
    const ids = new Set<number>();
    for (const c of candidates ?? []) {
      if (c.community_a !== null) ids.add(c.community_a);
      if (c.community_b !== null) ids.add(c.community_b);
    }
    return [...ids].sort((a, b) => a - b).map((id) => ({
      id,
      label: `Community ${id}`,
      size: 50,
      top_topics: [],
      works: [],
    }));
  }, [report, candidates]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !candidates || !reportChecked || communities.length === 0) return;

    const theme = readTheme();
    const graph = new Graph({ multi: true });

    // Community super-nodes on a circle; node size ~ sqrt(work count).
    const n = communities.length;
    const R = 100;
    const pos = new Map<number, { x: number; y: number }>();
    communities.forEach((c, i) => {
      const angle = (2 * Math.PI * i) / n - Math.PI / 2;
      const x = R * Math.cos(angle);
      const y = R * Math.sin(angle);
      pos.set(c.id, { x, y });
      graph.addNode(`c${c.id}`, {
        x,
        y,
        size: 6 + Math.sqrt(c.size) / 2.5,
        label: `${c.label ?? `Community ${c.id}`} (${c.size})`,
        color: theme.accent,
        kind: "community",
      });
    });

    // Inter-community edges, thickness ~ density weight.
    for (const e of report?.community_edges ?? []) {
      if (pos.has(e.source) && pos.has(e.target)) {
        graph.addEdge(`c${e.source}`, `c${e.target}`, {
          size: 0.5 + e.weight * 5,
          color: theme.border,
        });
      }
    }

    // Whitespace candidates: bridges as a highlighted mid-point node with
    // thin connector edges (dashed-equivalent); thin cells on an outer ring.
    let thinIdx = 0;
    const thinCells = candidates.filter((c) => c.kind === "thin_cell");
    const wsLabel = (c: WhitespaceCandidate): string => {
      const parsed = parseCandidate(c);
      const title =
        parsed.title.length > 32 ? `${parsed.title.slice(0, 32)}…` : parsed.title;
      return c.status === "candidate" ? title : `${title} (${c.status.replace("_", " ")})`;
    };
    // Node size scales with how surprising the hole is (expected works).
    const wsSize = (c: WhitespaceCandidate): number => {
      const expected = parseCandidate(c).expected;
      return expected ? Math.min(14, 4 + Math.sqrt(expected)) : 5;
    };
    for (const c of candidates) {
      const wsColor = c.status === "confirmed" ? theme.warn : theme.muted;
      if (c.kind === "bridge" && c.community_a !== null && c.community_b !== null) {
        const a = pos.get(c.community_a);
        const b = pos.get(c.community_b);
        if (!a || !b) continue;
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2;
        graph.addNode(c.whitespace_id, {
          x: mx * 1.15,
          y: my * 1.15,
          size: wsSize(c),
          label: wsLabel(c),
          color: wsColor,
          kind: "whitespace",
        });
        graph.addEdge(`c${c.community_a}`, c.whitespace_id, { size: 1, color: wsColor });
        graph.addEdge(c.whitespace_id, `c${c.community_b}`, { size: 1, color: wsColor });
      } else if (c.kind === "thin_cell") {
        const angle = (2 * Math.PI * thinIdx) / Math.max(1, thinCells.length) - Math.PI / 4;
        thinIdx += 1;
        graph.addNode(c.whitespace_id, {
          x: 1.45 * R * Math.cos(angle),
          y: 1.45 * R * Math.sin(angle),
          size: wsSize(c),
          label: wsLabel(c),
          color: wsColor,
          kind: "whitespace",
        });
      }
    }

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
        const id = Number(node.slice(1));
        const community = communities.find((c) => c.id === id);
        if (community) setSelection({ type: "community", community });
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
  }, [candidates, report, reportChecked, communities]);

  const works = report?.works ?? {};

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
              <span>edge thickness ~ inter-community density</span>
            </div>
            {!report && (
              <p className="muted small">
                No completed zoom report for this run yet — showing community stubs referenced by
                bridge candidates only.
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
