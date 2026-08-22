import { useCallback, useEffect, useState } from "react";
import { apiConfig } from "../api";
import { createSurvey, listRuns, retryRun } from "../endpoints";
import type { Run } from "../types";

function fmt(iso: string | null): string {
  return iso
    ? new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })
    : "—";
}

/** UUIDs blow up the table; show the first block, full id on hover. */
function shortId(id: string): string {
  return id.length > 12 ? id.slice(0, 8) : id;
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
                    {shortId(r.run_id)}
                  </td>
                  <td className="cell-clip" title={r.field_name}>
                    {r.field_name}
                  </td>
                  <td>
                    {r.phase}
                    {r.whitespace_id && <span className="muted"> → {r.whitespace_id}</span>}
                  </td>
                  <td>
                    <span className={`badge status-${r.status}`}>{r.status}</span>
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
