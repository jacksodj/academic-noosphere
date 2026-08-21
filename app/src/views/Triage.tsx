import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { EvidenceChip, ScoreBar } from "../components";
import { getReport, getWhitespace, listRuns, zoomWhitespace } from "../endpoints";
import type { Run, WhitespaceCandidate, WorkRef } from "../types";

const KIND_LABEL: Record<WhitespaceCandidate["kind"], string> = {
  bridge: "bridge",
  thin_cell: "thin cell",
};

/**
 * Whitespace Triage (view 2 of 5, ticket #14) — the human pivot between the
 * coarse pass and bounded zoom passes: review each Whitespace Candidate and
 * start a zoom pass to confirm or refute it.
 */
export default function Triage() {
  const [params, setParams] = useSearchParams();
  const runParam = params.get("run");

  const [runs, setRuns] = useState<Run[] | null>(null);
  const [candidates, setCandidates] = useState<WhitespaceCandidate[] | null>(null);
  const [works, setWorks] = useState<Record<string, WorkRef>>({});
  const [error, setError] = useState<string | null>(null);
  const [zooming, setZooming] = useState<Set<string>>(new Set());

  const coarseRuns = useMemo(() => (runs ?? []).filter((r) => r.phase === "coarse"), [runs]);
  const runId = runParam ?? coarseRuns[0]?.run_id ?? null;

  useEffect(() => {
    listRuns()
      .then((rs) => {
        setRuns(rs);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!runId) return;
    setCandidates(null);
    getWhitespace(runId)
      .then((cs) => {
        setCandidates(cs);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [runId]);

  // Best-effort tooltip enrichment: pull work titles from the report of any
  // completed child zoom run (the report's `works` citation table).
  useEffect(() => {
    if (!runId || !runs) return;
    const zoomRuns = runs.filter(
      (r) => r.phase === "zoom" && r.parent_run_id === runId && r.status === "completed",
    );
    let cancelled = false;
    Promise.allSettled(zoomRuns.map((r) => getReport(r.run_id))).then((results) => {
      if (cancelled) return;
      const merged: Record<string, WorkRef> = {};
      for (const res of results) {
        if (res.status === "fulfilled") Object.assign(merged, res.value.works);
      }
      setWorks(merged);
    });
    return () => {
      cancelled = true;
    };
  }, [runId, runs]);

  const startZoom = useCallback(
    async (candidate: WhitespaceCandidate) => {
      if (!runId) return;
      setZooming((prev) => new Set(prev).add(candidate.whitespace_id));
      try {
        const { candidate: updated } = await zoomWhitespace(candidate.whitespace_id, runId);
        setCandidates((cs) =>
          (cs ?? []).map((c) => (c.whitespace_id === updated.whitespace_id ? updated : c)),
        );
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setZooming((prev) => {
          const next = new Set(prev);
          next.delete(candidate.whitespace_id);
          return next;
        });
      }
    },
    [runId],
  );

  return (
    <section>
      <div className="view-head">
        <h1>Whitespace Triage</h1>
        {coarseRuns.length > 0 && (
          <label className="run-select">
            <span className="muted">Coarse run</span>
            <select
              value={runId ?? ""}
              onChange={(e) => setParams({ run: e.target.value })}
            >
              {coarseRuns.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id} — {r.field_name} ({r.status})
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
          <p>Start a Survey from the Dashboard; its coarse pass surfaces Whitespace Candidates here.</p>
        </div>
      )}

      {runId && candidates === null && !error && <p className="muted">Loading candidates…</p>}

      {candidates !== null && candidates.length === 0 && (
        <div className="card empty-state">
          <h2>Nothing to triage</h2>
          <p>This coarse run surfaced no Whitespace Candidates.</p>
        </div>
      )}

      {candidates !== null && candidates.length > 0 && (
        <div className="table-wrap">
          <table className="triage-table">
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Kind</th>
                <th>Description</th>
                <th>Signals</th>
                <th>Evidence</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr key={c.whitespace_id} className={`ws-row ws-${c.status}`}>
                  <td className="mono">{c.whitespace_id}</td>
                  <td>
                    <span className={`badge kind-${c.kind}`}>{KIND_LABEL[c.kind]}</span>
                    {c.kind === "bridge" && c.community_a !== null && c.community_b !== null && (
                      <div className="muted mono small">
                        C{c.community_a} ↔ C{c.community_b}
                      </div>
                    )}
                    {c.kind === "thin_cell" && c.topic_id && (
                      <div className="muted mono small">{c.topic_id}</div>
                    )}
                  </td>
                  <td className="wrap-cell">
                    {c.description}
                    {c.status === "not_confirmed" && c.not_confirmed_reason && (
                      <div className="not-confirmed-reason">{c.not_confirmed_reason}</div>
                    )}
                  </td>
                  <td className="signals-cell">
                    <ScoreBar label="sparsity" value={c.sparsity_score} />
                    <ScoreBar label="low-cited" value={c.low_citedness_signal} />
                  </td>
                  <td className="wrap-cell">
                    <div className="chip-row">
                      {c.evidence.length === 0 && <span className="muted">—</span>}
                      {c.evidence.map((item, i) => (
                        <EvidenceChip key={i} item={item} works={works} />
                      ))}
                    </div>
                  </td>
                  <td>
                    <span className={`badge ws-status-${c.status}`}>
                      {c.status.replace("_", " ")}
                    </span>
                  </td>
                  <td>
                    {c.status === "candidate" && (
                      <button
                        className="primary"
                        disabled={zooming.has(c.whitespace_id)}
                        onClick={() => void startZoom(c)}
                      >
                        {zooming.has(c.whitespace_id) ? "Starting…" : "Zoom"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
