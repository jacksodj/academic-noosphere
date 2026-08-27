/**
 * Sources explorer: a filterable, citation-ranked listing of the works in a
 * Run Snapshot. Insights deep-links here (active area → topic filter, year
 * bar → year filter); every row clicks through to its DOI / OpenAlex page.
 */

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { runLabel, workUrl, pickRun } from "../components";
import { listRuns, listWorks } from "../endpoints";
import type { Run, WorksPage } from "../types";

export default function Sources() {
  const [params, setParams] = useSearchParams();
  const runParam = params.get("run");
  const topicId = params.get("topic_id");
  const topicName = params.get("topic_name");
  const yearFrom = params.get("year_from");
  const yearTo = params.get("year_to");
  const q = params.get("q") ?? "";

  const [runs, setRuns] = useState<Run[] | null>(null);
  const [page, setPage] = useState<WorksPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draftQ, setDraftQ] = useState(q);

  const coarseRuns = useMemo(() => (runs ?? []).filter((r) => r.phase === "coarse"), [runs]);
  const runId = pickRun("coarse", runParam, coarseRuns);

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!runId) return;
    setPage(null);
    listWorks(runId, {
      topic_id: topicId ?? undefined,
      year_from: yearFrom ?? undefined,
      year_to: yearTo ?? undefined,
      q: q || undefined,
    })
      .then(setPage)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [runId, topicId, yearFrom, yearTo, q]);

  function setParam(patch: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    if (runId) next.set("run", runId);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    setParams(next);
  }

  const filters: { label: string; clear: Record<string, string | null> }[] = [];
  if (topicId)
    filters.push({
      label: `topic: ${topicName ?? topicId}`,
      clear: { topic_id: null, topic_name: null },
    });
  if (yearFrom || yearTo)
    filters.push({
      label:
        yearFrom === yearTo && yearFrom
          ? `year: ${yearFrom}`
          : `years: ${yearFrom ?? "…"}–${yearTo ?? "…"}`,
      clear: { year_from: null, year_to: null },
    });
  if (q) filters.push({ label: `title contains “${q}”`, clear: { q: null } });

  return (
    <section>
      <div className="view-head">
        <h1>Sources</h1>
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

      <div className="sources-controls">
        <input
          value={draftQ}
          placeholder="filter by title…"
          onChange={(e) => setDraftQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") setParam({ q: draftQ.trim() || null });
          }}
        />
        <button onClick={() => setParam({ q: draftQ.trim() || null })}>Filter</button>
        {filters.map((f) => (
          <button key={f.label} className="chip filter-chip" onClick={() => setParam(f.clear)}>
            {f.label} ✕
          </button>
        ))}
      </div>

      {error && <p className="error">{error}</p>}
      {runId && page === null && !error && <p className="muted">Loading sources…</p>}

      {page && (
        <>
          <p className="muted">
            {page.total.toLocaleString()} works
            {page.total > page.works.length && ` (showing top ${page.works.length} by citations)`}
          </p>
          <div className="card">
            <ol className="cited-list">
              {page.works.map((w) => (
                <li key={w.work_id}>
                  <div className="cited-row">
                    <a href={workUrl(w.work_id, w.doi)} target="_blank" rel="noreferrer">
                      {w.title?.trim() || `(untitled — ${w.work_id})`}
                    </a>
                    <span className="muted small">
                      {w.year ?? "?"} · {w.cited_by_count.toLocaleString()} citations ·{" "}
                      <span className="mono">{w.work_id}</span>
                    </span>
                  </div>
                </li>
              ))}
            </ol>
            {page.works.length === 0 && (
              <p className="muted">No works match these filters.</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}
