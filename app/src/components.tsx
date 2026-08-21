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
 * Citation chip for a work id: renders `[W…]` with a hover card carrying
 * title/year/doi when the report's `works` table resolves it.
 */
export function WorkChip({ workId, works }: { workId: string; works?: Record<string, WorkRef> }) {
  const ref = works?.[workId];
  return (
    <span className="chip chip-work">
      <span className="mono">[{workId}]</span>
      {ref && (ref.title || ref.year || ref.doi) && (
        <span className="chip-card">
          {ref.title && <span className="chip-card-title">{ref.title}</span>}
          <span className="chip-card-meta">
            {ref.year && <span>{ref.year}</span>}
            {ref.doi && <span className="mono">doi:{ref.doi}</span>}
          </span>
        </span>
      )}
    </span>
  );
}

/** Citation chip for a Web Search finding: domain + retrieval date (identifier only). */
export function WebChip({ url, retrievedAt }: { url: string; retrievedAt: string | null }) {
  return (
    <span className="chip chip-web" title={url}>
      {domainOf(url)}
      {retrievedAt && <span className="chip-date">retrieved {fmtDate(retrievedAt)}</span>}
    </span>
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
