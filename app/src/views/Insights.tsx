/**
 * Corpus Insights (extra lens over a Run Snapshot): most-cited works and
 * recently-active research areas. Every number is a pure graph read; the
 * "recent" window is publication-year based (OpenAlex has no month), and the
 * cutoff year is shown so the label stays honest.
 */

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { runLabel, workUrl } from "../components";
import { Link } from "react-router-dom";
import { getInsights, listRuns } from "../endpoints";
import type { CorpusInsights, Run } from "../types";

export default function Insights() {
  const [params, setParams] = useSearchParams();
  const runParam = params.get("run");

  const [runs, setRuns] = useState<Run[] | null>(null);
  const [insights, setInsights] = useState<CorpusInsights | null>(null);
  const [error, setError] = useState<string | null>(null);

  const coarseRuns = useMemo(() => (runs ?? []).filter((r) => r.phase === "coarse"), [runs]);
  const runId = runParam ?? coarseRuns[0]?.run_id ?? null;

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!runId) return;
    setInsights(null);
    getInsights(runId)
      .then(setInsights)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [runId]);

  const maxCited = insights?.top_cited[0]?.cited_by_count ?? 1;
  const maxRecent = insights?.active_topics[0]?.recent_count ?? 1;
  const histYears = Object.entries(insights?.year_histogram ?? {}).slice(-30);
  const maxHist = Math.max(1, ...histYears.map(([, n]) => n));

  return (
    <section>
      <div className="view-head">
        <h1>Insights</h1>
        {coarseRuns.length > 0 && (
          <label className="run-select">
            <span className="muted">Coarse run</span>
            <select value={runId ?? ""} onChange={(e) => setParams({ run: e.target.value })}>
              {coarseRuns.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {runLabel(r)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {error && <p className="error">{error}</p>}
      {runId && insights === null && !error && <p className="muted">Computing insights…</p>}

      {insights && (
        <>
          <p className="muted">
            {insights.snapshot_size.toLocaleString()} works in this Run Snapshot ·{" "}
            {insights.resolved_works.toLocaleString()} resolved in the graph
          </p>

          <div className="card insights-card">
            <h2>Most-cited works</h2>
            <ol className="cited-list">
              {insights.top_cited.map((w) => (
                <li key={w.work_id}>
                  <div className="cited-row">
                    <a href={workUrl(w.work_id, w.doi)} target="_blank" rel="noreferrer">
                      {w.title?.trim() || `(untitled — ${w.work_id})`}
                    </a>
                    <span className="muted small">
                      {w.year ?? "?"} · <span className="mono">{w.work_id}</span>
                    </span>
                  </div>
                  <div className="insight-bar">
                    <span
                      className="insight-bar-fill"
                      style={{ width: `${(100 * w.cited_by_count) / maxCited}%` }}
                    />
                    <span className="insight-bar-value mono">
                      {w.cited_by_count.toLocaleString()} citations
                    </span>
                  </div>
                </li>
              ))}
            </ol>
          </div>

          <div className="card insights-card">
            <h2>
              Most active research areas{" "}
              <span className="muted">
                (published {insights.recent_cutoff_year}–present)
              </span>
            </h2>
            <ol className="cited-list">
              {insights.active_topics.map((t) => (
                <li key={t.topic_id}>
                  <div className="cited-row">
                    <Link
                      className="plain-link"
                      to={`/sources?run=${runId}&topic_id=${t.topic_id}&topic_name=${encodeURIComponent(t.name)}&year_from=${insights.recent_cutoff_year}`}
                      title="Browse these works in Sources"
                    >
                      {t.name} →
                    </Link>
                    <span className="muted small">
                      {Math.round(t.recent_share * 100)}% of its {t.total_count} works are
                      recent
                    </span>
                  </div>
                  <div className="insight-bar">
                    <span
                      className="insight-bar-fill accent2"
                      style={{ width: `${(100 * t.recent_count) / maxRecent}%` }}
                    />
                    <span className="insight-bar-value mono">
                      {t.recent_count} recent works
                    </span>
                  </div>
                </li>
              ))}
            </ol>
          </div>

          <div className="card insights-card">
            <h2>Publication years</h2>
            <div className="year-hist">
              {histYears.map(([year, n]) => (
                <Link
                  key={year}
                  className="year-col"
                  to={`/sources?run=${runId}&year_from=${year}&year_to=${year}`}
                  title={`${year}: ${n} works — browse in Sources`}
                >
                  <span
                    className="year-col-fill"
                    style={{ height: `${Math.max(2, (100 * n) / maxHist)}%` }}
                  />
                  <span className="year-col-label">{year.slice(2)}</span>
                </Link>
              ))}
            </div>
          </div>
        </>
      )}
    </section>
  );
}
