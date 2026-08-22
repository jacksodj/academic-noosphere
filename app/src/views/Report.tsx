import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CitedText, EvidenceChip, WorkChip } from "../components";
import {
  expandGap,
  getExpansions,
  getReport,
  getReportMarkdown,
  getWhitespace,
  listRuns,
  subscribeEvents,
} from "../endpoints";
import { parseCandidate } from "../whitespace";
import type { Gap, GapKind, GapReport, IdeonomyExpansion, Run, WorkRef } from "../types";

const GAP_KINDS: GapKind[] = ["structural", "narrative", "temporal"];
const COMPONENT_ORDER = ["sparsity", "narrative_demand", "recency", "low_citedness"];
const COMPONENT_LABEL: Record<string, string> = {
  sparsity: "sparsity",
  narrative_demand: "narrative demand",
  recency: "recency",
  low_citedness: "low citedness",
};

function ScoreChips({ gap }: { gap: Gap }) {
  const keys = [
    ...COMPONENT_ORDER.filter((k) => k in gap.scores),
    ...Object.keys(gap.scores).filter((k) => !COMPONENT_ORDER.includes(k)),
  ];
  return (
    <div className="chip-row score-chips">
      <span className="chip chip-composite" title="composite score">
        composite <strong>{gap.composite_score.toFixed(2)}</strong>
      </span>
      {keys.map((k) => (
        <span key={k} className="chip chip-score" title={`${COMPONENT_LABEL[k] ?? k} component`}>
          {COMPONENT_LABEL[k] ?? k} {gap.scores[k].toFixed(2)}
        </span>
      ))}
    </div>
  );
}

/**
 * Per-gap Ideonomy Expansion panel — collapsed by default; content is
 * generated speculation, segregated and unmistakably labeled (grounding rule).
 */
function IdeonomyPanel({ gap, works }: { gap: Gap; works: Record<string, WorkRef> }) {
  const [open, setOpen] = useState(false);
  const [expansions, setExpansions] = useState<IdeonomyExpansion[] | null>(null);
  // Expansion generation is async (job queue + Opus, ~1min): `busy` covers the
  // whole wait, resolved by SSE expansion_ready/failed with a poll fallback.
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function refreshExpansions(): Promise<IdeonomyExpansion[] | null> {
    return getExpansions(gap.gap_id)
      .then((list) => {
        setExpansions(list);
        return list;
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      });
  }

  useEffect(() => {
    if (!open || expansions !== null) return;
    void refreshExpansions();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on open
  }, [open, expansions, gap.gap_id]);

  useEffect(() => {
    if (!busy) return;
    const expected = (expansions?.length ?? 0) + 1;
    const unsubscribe = subscribeEvents((event) => {
      if (event.gap_id !== gap.gap_id) return;
      if (event.type === "expansion_ready") {
        void refreshExpansions().then(() => setBusy(false));
      } else if (event.type === "expansion_failed") {
        setError(String(event.error ?? "expansion failed"));
        setBusy(false);
      }
    });
    // Poll fallback in case the SSE stream is down.
    const timer = setInterval(() => {
      void refreshExpansions().then((list) => {
        if (list && list.length >= expected) setBusy(false);
      });
    }, 15000);
    return () => {
      unsubscribe();
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-arm per wait
  }, [busy, gap.gap_id]);

  async function runExpand() {
    setBusy(true);
    setError(null);
    try {
      await expandGap(gap.gap_id); // 202 ack; result arrives via SSE/poll above
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const hasExpansions = (expansions?.length ?? 0) > 0;

  return (
    <div className={`ideonomy ${open ? "ideonomy-open" : ""}`}>
      <button className="ideonomy-toggle" onClick={() => setOpen((v) => !v)}>
        <span className="spec-label">SPECULATIVE</span> Ideonomy Expansion
        <span className="muted"> {open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="ideonomy-body">
          <p className="spec-banner">
            Generated speculation — segregated from grounded findings. Each idea cites its nearest
            existing work; nothing below is a Grounded Claim.
          </p>
          {error && <p className="error">{error}</p>}
          {expansions === null && !error && <p className="muted">Loading expansions…</p>}
          {expansions !== null && !hasExpansions && (
            <p className="muted">No expansions yet for this gap.</p>
          )}
          {expansions?.map((exp) => (
            <div key={exp.attempt} className="expansion">
              <div className="tuple-legend">
                <span className="muted">attempt {exp.attempt}</span>
                <span className="chip chip-tuple" title="ideonomic operators">
                  operators: {exp.tuple.operators.join(" · ")}
                </span>
                <span className="chip chip-tuple" title="organon">
                  {exp.tuple.organon}
                </span>
                <span className="chip chip-tuple" title="dimension prompts">
                  dims: {exp.tuple.dimension_prompts.join(" · ")}
                </span>
                <span className="chip chip-tuple mono" title="reproducibility seed">
                  seed {exp.tuple.seed}
                </span>
              </div>
              <ul className="idea-list">
                {exp.ideas.map((idea, i) => (
                  <li key={i} className="idea">
                    <p>{idea.text}</p>
                    <div className="chip-row">
                      {idea.operators.map((op) => (
                        <span key={op} className="badge op-badge">
                          {op}
                        </span>
                      ))}
                      <span className="badge organon-badge">{idea.organon_position}</span>
                      <span className="muted small">nearest work</span>
                      <WorkChip workId={idea.nearest_work_id} works={works} />
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {expansions !== null && (
            <button className="primary" disabled={busy} onClick={() => void runExpand()}>
              {busy
                ? "Expanding (Opus)…"
                : hasExpansions
                  ? `Re-roll (attempt ${(expansions.at(-1)?.attempt ?? expansions.length) + 1}, Opus)`
                  : "Expand (Opus)"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Gap Report reader (view 3 of 5, ticket #14). */
export default function Report() {
  const [params, setParams] = useSearchParams();
  const runParam = params.get("run");

  const [runs, setRuns] = useState<Run[] | null>(null);
  const [report, setReport] = useState<GapReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kindFilter, setKindFilter] = useState<Set<GapKind>>(new Set(GAP_KINDS));
  const [showUnconfirmed, setShowUnconfirmed] = useState(false);
  const [exporting, setExporting] = useState(false);

  const zoomRuns = useMemo(
    () => (runs ?? []).filter((r) => r.phase === "zoom" && r.status === "completed"),
    [runs],
  );
  const runId = runParam ?? zoomRuns[0]?.run_id ?? null;

  // Human labels for the zoom-run picker: candidate topic titles, resolved
  // from the parent coarse runs' whitespace lists.
  const [wsTitles, setWsTitles] = useState<Record<string, string>>({});

  useEffect(() => {
    const parents = [...new Set(zoomRuns.map((r) => r.parent_run_id).filter(Boolean))];
    Promise.allSettled(parents.map((p) => getWhitespace(p as string))).then((results) => {
      const titles: Record<string, string> = {};
      for (const res of results) {
        if (res.status !== "fulfilled") continue;
        for (const c of res.value) titles[c.whitespace_id] = parseCandidate(c).title;
      }
      setWsTitles(titles);
    });
  }, [zoomRuns]);

  function zoomLabel(r: Run): string {
    const title = r.whitespace_id ? wsTitles[r.whitespace_id] : undefined;
    const shortWs = r.whitespace_id?.split("-").pop() ?? r.run_id.slice(0, 8);
    const date = r.finished_at
      ? new Date(r.finished_at).toLocaleDateString(undefined, { month: "numeric", day: "numeric" })
      : "";
    return title
      ? `${title} · ${shortWs}${date ? ` · ${date}` : ""}`
      : `${shortWs} · ${r.run_id.slice(0, 8)}`;
  }

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!runId) return;
    setReport(null);
    getReport(runId)
      .then((r) => {
        setReport(r);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [runId]);

  function toggleKind(kind: GapKind) {
    setKindFilter((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }

  async function exportMarkdown() {
    if (!runId) return;
    setExporting(true);
    try {
      const md = await getReportMarkdown(runId);
      const blob = new Blob([md], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `gap-report-${runId}.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }

  // Grade on a curve: the kind filter must never leave the reader with
  // nothing. Kind-less gaps always show, and if the selected kinds would
  // hide every gap, fall back to showing all of them with a notice.
  const { gaps, filterBypassed } = useMemo(() => {
    if (!report) return { gaps: [], filterBypassed: false };
    const matching = report.gaps.filter(
      (g) => g.kinds.length === 0 || g.kinds.some((k) => kindFilter.has(k)),
    );
    const bypass = report.gaps.length > 0 && matching.length === 0;
    const visible = bypass ? report.gaps : matching;
    return {
      gaps: [...visible].sort((a, b) => b.composite_score - a.composite_score),
      filterBypassed: bypass,
    };
  }, [report, kindFilter]);

  return (
    <section>
      <div className="view-head">
        <h1>Gap Report</h1>
        <div className="view-head-actions">
          {zoomRuns.length > 0 && (
            <label className="run-select">
              <span className="muted">Zoom run</span>
              <select value={runId ?? ""} onChange={(e) => setParams({ run: e.target.value })}>
                {zoomRuns.map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {zoomLabel(r)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {report && (
            <button onClick={() => void exportMarkdown()} disabled={exporting}>
              {exporting ? "Exporting…" : "Export Markdown"}
            </button>
          )}
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {runs !== null && zoomRuns.length === 0 && (
        <div className="card empty-state">
          <h2>No Gap Report yet</h2>
          <p>
            Confirmed gaps with grounded evidence render here after a Survey completes a zoom pass.
          </p>
        </div>
      )}

      {runId && report === null && !error && <p className="muted">Loading report…</p>}

      {report && (
        <>
          <p className="report-meta muted">
            {report.field_name} · generated {new Date(report.generated_at).toLocaleString()} ·{" "}
            {report.gaps.length} confirmed gap{report.gaps.length === 1 ? "" : "s"}
          </p>

          <div className="filter-row">
            <span className="muted small">Evidence kinds:</span>
            {GAP_KINDS.map((k) => (
              <button
                key={k}
                className={`filter-toggle ${kindFilter.has(k) ? "on" : ""}`}
                onClick={() => toggleKind(k)}
              >
                {k}
              </button>
            ))}
          </div>

          {filterBypassed && (
            <p className="muted">
              No gaps carry the selected evidence kinds — showing all {gaps.length} so there is
              always something to reason over.
            </p>
          )}

          {gaps.length === 0 && (
            <div className="card empty-state">
              <h2>No confirmed gaps in this zoom pass</h2>
              <p>
                The zoom examined its candidate and did not confirm a gap — see “Examined, not
                confirmed” below for the reason, or pick another candidate in Triage.
              </p>
            </div>
          )}

          {gaps.map((gap, i) => (
            <article key={gap.gap_id} className="card gap-card">
              <div className="gap-head">
                <span className="gap-rank">#{i + 1}</span>
                <div className="chip-row">
                  {gap.kinds.map((k) => (
                    <span key={k} className={`badge gapkind-${k}`}>
                      {k}
                    </span>
                  ))}
                  <span className="muted mono small">{gap.gap_id}</span>
                </div>
              </div>
              <p className="gap-statement">
                <CitedText text={gap.statement} evidence={gap.evidence} works={report.works} />
              </p>
              <ScoreChips gap={gap} />
              <div className="chip-row evidence-row">
                <span className="muted small">Evidence:</span>
                {gap.evidence.map((item, j) => (
                  <EvidenceChip key={j} item={item} works={report.works} />
                ))}
              </div>
              {gap.evidence.some((e) => e.quote) && (
                <ul className="quote-list">
                  {gap.evidence
                    .filter((e) => e.quote)
                    .map((e, j) => (
                      <li key={j} className="quote">
                        “{e.quote}”{" "}
                        {e.work_id && <WorkChip workId={e.work_id} works={report.works} />}
                      </li>
                    ))}
                </ul>
              )}
              <IdeonomyPanel gap={gap} works={report.works} />
            </article>
          ))}

          {report.examined_not_confirmed.length > 0 && (
            <div className="card unconfirmed">
              <button className="ideonomy-toggle" onClick={() => setShowUnconfirmed((v) => !v)}>
                Examined, not confirmed ({report.examined_not_confirmed.length})
                <span className="muted"> {showUnconfirmed ? "▾" : "▸"}</span>
              </button>
              {showUnconfirmed && (
                <ul className="unconfirmed-list">
                  {report.examined_not_confirmed.map((c) => (
                    <li key={c.whitespace_id}>
                      <span className="mono">{c.whitespace_id}</span> · {c.description}
                      {c.not_confirmed_reason && (
                        <div className="not-confirmed-reason">{c.not_confirmed_reason}</div>
                      )}
                      <div className="chip-row">
                        {(c.evidence ?? []).map((item, j) => (
                          <EvidenceChip key={j} item={item} works={report.works} />
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
