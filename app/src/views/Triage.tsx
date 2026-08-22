import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { EvidenceChip, ScoreBar } from "../components";
import { getReport, getWhitespace, listRuns, zoomWhitespace } from "../endpoints";
import type { Run, WhitespaceCandidate, WorkRef } from "../types";
import { candidateStat, parseCandidate, surpriseScore } from "../whitespace";

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
        <>
          <p className="muted">
            {candidates.length} candidates, most surprising first (largest gap between expected
            and observed works).
          </p>
          <div className="ws-cards">
            {[...candidates]
              .sort((a, b) => surpriseScore(b) - surpriseScore(a))
              .map((c) => {
                const p = parseCandidate(c);
                const stat = candidateStat(p);
                return (
                  <div key={c.whitespace_id} className={`card ws-card ws-${c.status}`}>
                    <div className="ws-card-head">
                      <div className="ws-card-title">
                        <h2 title={c.description}>{p.title}</h2>
                        <span className="muted small">
                          <span className={`badge kind-${c.kind}`}>{KIND_LABEL[c.kind]}</span>{" "}
                          {p.community && `community ${p.community} · `}
                          {c.topic_id && (
                            <span className="mono">{c.topic_id} · </span>
                          )}
                          <span className="mono">{p.shortId}</span>
                        </span>
                      </div>
                      <div className="ws-card-actions">
                        <span className={`badge ws-status-${c.status}`}>
                          {c.status.replace("_", " ")}
                        </span>
                        {c.status === "candidate" && (
                          <button
                            className="primary"
                            disabled={zooming.has(c.whitespace_id)}
                            onClick={() => void startZoom(c)}
                          >
                            {zooming.has(c.whitespace_id) ? "Starting…" : "Zoom"}
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="ws-card-body">
                      {stat ? (
                        <span className="ws-stat">{stat}</span>
                      ) : (
                        <span className="ws-stat-desc">{c.description}</span>
                      )}
                      <div className="signals-cell">
                        <ScoreBar label="sparsity" value={c.sparsity_score} />
                        <ScoreBar label="low-cited" value={c.low_citedness_signal} />
                      </div>
                    </div>
                    {c.evidence.length > 0 && (
                      <div className="chip-row">
                        {c.evidence.map((item, i) => (
                          <EvidenceChip key={i} item={item} works={works} />
                        ))}
                      </div>
                    )}
                    {c.status === "not_confirmed" && c.not_confirmed_reason && (
                      <div className="not-confirmed-reason">{c.not_confirmed_reason}</div>
                    )}
                  </div>
                );
              })}
          </div>
        </>
      )}
    </section>
  );
}
