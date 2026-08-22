/**
 * Run detail: header + stage progress + realtime activity feed.
 *
 * History comes from GET /api/runs/{id}/activity (persisted in the sidecar);
 * live lines append via SSE "activity" events, progress via "progress" events.
 * The feed auto-follows the tail unless the user scrolls up.
 */

import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getRunActivity,
  getRunProgress,
  listRuns,
  retryRun,
  subscribeEvents,
} from "../endpoints";
import type { Run, RunActivity, RunProgress } from "../types";

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour12: false });
}

function StageChips({ progress }: { progress: RunProgress }) {
  return (
    <div className="stage-chips">
      {progress.stages.map((stage) => {
        const state = progress.done.includes(stage)
          ? "done"
          : stage === progress.current
            ? "current"
            : "todo";
        return (
          <span key={stage} className={`stage-chip stage-${state}`}>
            {state === "done" ? "✓ " : ""}
            {stage}
          </span>
        );
      })}
    </div>
  );
}

export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [activities, setActivities] = useState<RunActivity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const feedRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);

  function refreshRun() {
    listRuns()
      .then((runs) => setRun(runs.find((r) => r.run_id === runId) ?? null))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }

  useEffect(() => {
    if (!runId) return;
    refreshRun();
    getRunActivity(runId)
      .then((res) => setActivities(res.activities))
      .catch(() => setActivities([]));
    getRunProgress(runId)
      .then((res) => setProgress(res.progress))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once per run
  }, [runId]);

  useEffect(
    () =>
      subscribeEvents((event) => {
        if (event.run_id !== runId) return;
        if (event.type === "activity" && typeof event.message === "string") {
          setActivities((prev) => [...prev, event as unknown as RunActivity]);
        } else if (event.type === "progress" && event.progress) {
          setProgress(event.progress as RunProgress);
        } else {
          refreshRun(); // lifecycle event (completed, requeued, …)
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stable per run
    [runId],
  );

  // Auto-follow the tail unless the user scrolled up.
  useEffect(() => {
    const el = feedRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  }, [activities]);

  async function retry() {
    if (!runId) return;
    setRetrying(true);
    setError(null);
    try {
      await retryRun(runId);
      refreshRun();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetrying(false);
    }
  }

  if (!runId) return null;

  return (
    <section>
      <div className="view-head">
        <h1 className="mono run-title" title={runId}>
          {run?.field_name ?? runId}
        </h1>
        <Link to="/" className="muted">
          ← Dashboard
        </Link>
      </div>

      {error && <p className="error">{error}</p>}

      {run && (
        <p className="run-meta">
          <span className={`badge status-${run.status}`}>{run.status}</span>{" "}
          <span className="muted">
            {run.phase} run <span className="mono">{runId.slice(0, 8)}</span>
            {run.started_at ? ` · started ${new Date(run.started_at).toLocaleString()}` : ""}
          </span>{" "}
          {run.status === "failed" && (
            <button className="subtle retry-btn" disabled={retrying} onClick={() => void retry()}>
              {retrying ? "Retrying…" : "Retry"}
            </button>
          )}
        </p>
      )}

      {progress && <StageChips progress={progress} />}
      {progress?.error && <p className="error">{progress.error}</p>}

      <div
        className="activity-feed card"
        ref={feedRef}
        onScroll={() => {
          const el = feedRef.current;
          if (el) followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {activities.length === 0 && <p className="muted">No activity recorded yet.</p>}
        {activities.map((a) => (
          <div key={a.seq} className="activity-line">
            <span className="mono muted activity-ts">{fmtTime(a.ts)}</span>
            <span>{a.message}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
