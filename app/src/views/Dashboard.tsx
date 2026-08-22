import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiConfig } from "../api";
import { stageProgressLabel } from "../components";
import { createSurvey, getRunProgress, listRuns, retryRun, subscribeEvents } from "../endpoints";
import type { Run, RunProgress, StageProgress } from "../types";

function fmt(iso: string | null): string {
  return iso
    ? new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })
    : "—";
}

/** UUIDs blow up the table; show the first block, full id on hover. */
function shortId(id: string): string {
  return id.length > 12 ? id.slice(0, 8) : id;
}

function progressLabel(p: RunProgress): string {
  if (!p.current) return "finishing";
  const step = p.stages.indexOf(p.current) + 1;
  const counts =
    p.counts.kept > 0
      ? `${p.counts.kept.toLocaleString()} kept`
      : p.counts.candidates > 0
        ? `${p.counts.candidates.toLocaleString()} works`
        : p.counts.seeds > 0
          ? `${p.counts.seeds.toLocaleString()} seeds`
          : "";
  return `${p.current} ${step}/${p.stages.length}${counts ? ` · ${counts}` : ""}`;
}

export default function Dashboard() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [fieldName, setFieldName] = useState("");
  const [seedQueries, setSeedQueries] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(() => {
    listRuns()
      .then((rs) => {
        setRuns(rs);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Live progress: SSE events update the stage line and refresh the run list
  // on state changes; an initial fetch fills progress for already-active runs.
  const [progress, setProgress] = useState<Record<string, RunProgress>>({});
  const [stageTicks, setStageTicks] = useState<Record<string, StageProgress>>({});

  useEffect(
    () =>
      subscribeEvents((event) => {
        const runId = typeof event.run_id === "string" ? event.run_id : null;
        if (!runId) return;
        if (event.type === "progress" && event.progress) {
          setProgress((prev) => ({ ...prev, [runId]: event.progress as RunProgress }));
          // a checkpoint save means the sub-stage tick is stale
          setStageTicks((prev) => {
            const { [runId]: _stale, ...rest } = prev;
            return rest;
          });
        } else if (event.type === "stage_progress") {
          setStageTicks((prev) => ({ ...prev, [runId]: event as unknown as StageProgress }));
        } else {
          refresh(); // coarse_completed, run_requeued, … — statuses changed
        }
      }),
    [refresh],
  );

  // Poll fallback: SSE can drop silently (sleep/wake, reconnect gaps) — keep
  // the list honest while anything is active.
  useEffect(() => {
    const anyActive = (runs ?? []).some((r) => r.status === "running" || r.status === "pending");
    if (!anyActive) return;
    const timer = setInterval(refresh, 30000);
    return () => clearInterval(timer);
  }, [runs, refresh]);

  useEffect(() => {
    for (const r of runs ?? []) {
      if ((r.status === "running" || r.status === "pending") && !(r.run_id in progress)) {
        getRunProgress(r.run_id)
          .then((res) => setProgress((prev) => ({ ...prev, [r.run_id]: res.progress })))
          .catch(() => undefined);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fill once per runs change
  }, [runs]);

  const [retrying, setRetrying] = useState<string | null>(null);

  async function retry(runId: string) {
    setRetrying(runId);
    setError(null);
    try {
      await retryRun(runId);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetrying(null);
    }
  }

  async function submitSurvey(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createSurvey({
        field_name: fieldName.trim(),
        seed_queries: seedQueries
          .split("\n")
          .map((q) => q.trim())
          .filter(Boolean),
      });
      setShowForm(false);
      setFieldName("");
      setSeedQueries("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section>
      <div className="view-head">
        <h1>Dashboard</h1>
        <button className="primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "New Survey"}
        </button>
      </div>

      {apiConfig.mock && <p className="badge mock-badge">mock data (VITE_MOCK=1)</p>}
      {error && <p className="error">{error}</p>}

      {showForm && (
        <form className="card form" onSubmit={submitSurvey}>
          <label>
            Field name
            <input
              value={fieldName}
              onChange={(e) => setFieldName(e.target.value)}
              placeholder="e.g. memory for AI agents"
              required
            />
          </label>
          <label>
            Seed queries (one per line)
            <textarea
              value={seedQueries}
              onChange={(e) => setSeedQueries(e.target.value)}
              rows={4}
              placeholder={"agent memory architectures\nepisodic memory consolidation"}
            />
          </label>
          <button className="primary" type="submit" disabled={submitting || !fieldName.trim()}>
            {submitting ? "Starting…" : "Start Survey"}
          </button>
        </form>
      )}

      {runs === null && !error && <p className="muted">Loading runs…</p>}

      {runs !== null && runs.length === 0 && (
        <div className="card empty-state">
          <h2>No Surveys yet</h2>
          <p>
            Start your first Survey to build a core corpus and surface Whitespace
            Candidates for a Field.
          </p>
        </div>
      )}

      {runs !== null && runs.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Field</th>
                <th>Phase</th>
                <th>Status</th>
                <th>Started</th>
                <th>Finished</th>
                <th>Spend</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run_id}>
                  <td className="mono" title={r.run_id}>
                    <Link to={`/runs/${r.run_id}`}>{shortId(r.run_id)}</Link>
                  </td>
                  <td className="cell-clip" title={r.field_name}>
                    <Link to={`/runs/${r.run_id}`} className="plain-link">
                      {r.field_name}
                    </Link>
                  </td>
                  <td>
                    {r.phase}
                    {r.whitespace_id && <span className="muted"> → {r.whitespace_id}</span>}
                  </td>
                  <td>
                    <span className={`badge status-${r.status}`}>{r.status}</span>
                    {r.status === "running" && (stageTicks[r.run_id] || progress[r.run_id]) && (
                      <span className="muted progress-label">
                        {stageTicks[r.run_id]
                          ? stageProgressLabel(stageTicks[r.run_id])
                          : progressLabel(progress[r.run_id])}
                      </span>
                    )}
                    {r.status === "failed" && (
                      <button
                        className="subtle retry-btn"
                        disabled={retrying === r.run_id}
                        onClick={() => void retry(r.run_id)}
                        title="Requeue this run; it resumes from its last checkpoint"
                      >
                        {retrying === r.run_id ? "Retrying…" : "Retry"}
                      </button>
                    )}
                  </td>
                  <td>{fmt(r.started_at)}</td>
                  <td>{fmt(r.finished_at)}</td>
                  {/* TODO(wave 2): live per-run spend from the SpendMeter via SSE */}
                  <td className="muted">$—</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
