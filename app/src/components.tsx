/** Small shared UI pieces for the Triage and Report views. */

import type { EvidenceItem, WorkRef } from "./types";

/** Labeled 0..1 signal bar (sparsity, low-citedness, component scores). */
export function ScoreBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="score-bar" title={`${label}: ${value.toFixed(2)}`}>
      <span className="score-bar-label">{label}</span>
      <span className="score-bar-track">
        <span className="score-bar-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="score-bar-value mono">{value.toFixed(2)}</span>
    </div>
  );
}

function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toISOString().slice(0, 10) : "";
}

/**
 * Source link for a work: the DOI resolver when a DOI is known, else the
 * work's OpenAlex landing page (always exists and links onward to the paper).
 */
export function workUrl(workId: string, doi?: string | null): string {
  if (doi) {
    const bare = doi.replace(/^https?:\/\/(dx\.)?doi\.org\//, "");
    return `https://doi.org/${bare}`;
  }
  return `https://openalex.org/${workId}`;
}

/**
 * Citation chip for a work id: renders `[W…]` with a hover card carrying
 * title/year/doi when the report's `works` table resolves it. Clicking opens
 * the source (DOI, else OpenAlex) in the system browser.
 */
export function WorkChip({ workId, works }: { workId: string; works?: Record<string, WorkRef> }) {
  const ref = works?.[workId];
  return (
    <a
      className="chip chip-work"
      href={workUrl(workId, ref?.doi)}
      target="_blank"
      rel="noreferrer"
    >
      <span className="mono">[{workId}]</span>
      <span className="chip-card">
        {ref?.title && <span className="chip-card-title">{ref.title}</span>}
        <span className="chip-card-meta">
          {ref?.year && <span>{ref.year}</span>}
          {ref?.doi ? (
            <span className="mono">doi:{ref.doi}</span>
          ) : (
            <span className="mono">openalex.org/{workId}</span>
          )}
          <span className="chip-open">open source ↗</span>
        </span>
      </span>
    </a>
  );
}

/** Citation chip for a Web Search finding: domain + retrieval date (identifier only). */
export function WebChip({ url, retrievedAt }: { url: string; retrievedAt: string | null }) {
  return (
    <a className="chip chip-web" href={url} target="_blank" rel="noreferrer" title={url}>
      {domainOf(url)}
      {retrievedAt && <span className="chip-date">retrieved {fmtDate(retrievedAt)}</span>}
    </a>
  );
}

/** Render one EvidenceItem as the appropriate chip. */
export function EvidenceChip({
  item,
  works,
}: {
  item: EvidenceItem;
  works?: Record<string, WorkRef>;
}) {
  if (item.kind === "work" && item.work_id) {
    return <WorkChip workId={item.work_id} works={works} />;
  }
  if (item.url) {
    return <WebChip url={item.url} retrievedAt={item.retrieved_at} />;
  }
  return null;
}

/** Human duration for ETAs: 45s, 3m 20s, 1h 12m. */
export function fmtDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (minutes < 60) return `${minutes}m ${String(secs).padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
}

/** "embedding 4,096/21,293 · ~8m 30s left" from a stage_progress tick. */
export function stageProgressLabel(p: {
  step: string;
  done: number;
  total: number;
  eta_s: number | null;
}): string {
  const eta = p.eta_s != null && p.eta_s > 0 ? ` · ~${fmtDuration(p.eta_s)} left` : "";
  return `${p.step} ${p.done.toLocaleString()}/${p.total.toLocaleString()}${eta}`;
}

/**
 * Route-level error boundary: a render crash shows an inline error card
 * instead of white-screening the whole app.
 */
import { Component, type ReactNode } from "react";

export class ViewErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card view-error">
          <h2>This view hit a rendering error</h2>
          <p className="mono small">{this.state.error.message}</p>
          <button onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      );
    }
    return this.props.children;
  }
}

/**
 * Statement text with the LLM's inline `[n]` citations rendered as live links.
 * Indices are 0-based positions into the gap's evidence array (the exact list
 * the synthesis prompt numbered); unresolvable markers stay plain text.
 */
export function CitedText({
  text,
  evidence,
  works,
}: {
  text: string;
  evidence: EvidenceItem[];
  works?: Record<string, WorkRef>;
}) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const m = /^\[(\d+)\]$/.exec(part);
        if (!m) return <span key={i}>{part}</span>;
        const ev = evidence[Number(m[1])];
        const href =
          ev?.kind === "work" && ev.work_id
            ? workUrl(ev.work_id, works?.[ev.work_id]?.doi)
            : ev?.url ?? null;
        if (!href) return <span key={i}>{part}</span>;
        const title =
          ev.work_id != null
            ? (works?.[ev.work_id]?.title ?? ev.work_id)
            : (ev.url ?? "");
        return (
          <a
            key={i}
            className="cite-link"
            href={href}
            target="_blank"
            rel="noreferrer"
            title={title}
          >
            {part}
          </a>
        );
      })}
    </>
  );
}

/** Consistent human label for a run everywhere: "field — id8 (status)". */
export function runLabel(r: {
  run_id: string;
  field_name: string;
  status?: string;
}): string {
  const status = r.status && r.status !== "completed" ? ` (${r.status})` : "";
  return `${r.field_name} — ${r.run_id.slice(0, 8)}${status}`;
}
