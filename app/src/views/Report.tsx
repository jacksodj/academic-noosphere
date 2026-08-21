import type { Gap, IdeonomyExpansion } from "../types";

export interface ReportProps {
  /** Confirmed gaps for the Gap Report, ranked by composite_score. */
  gaps?: Gap[];
  /** Labeled speculative sections, keyed off gap_id. */
  expansions?: IdeonomyExpansion[];
}

/**
 * Gap Report reader (view 3 of 5, ticket #14).
 *
 * TODO(wave 2):
 * - Fetch gaps (/api/gaps?zoom_run_id=…) and expansions (/api/gaps/{id}/expansions).
 * - Interactive reader: citation chips resolving EvidenceItems, evidence filters,
 *   visible component scores, Markdown export.
 * - Ideonomy Expansion rendered as a segregated, clearly-labeled speculative
 *   section with per-idea nearest-work citations and a Re-roll (attempt N+1) action.
 */
export default function Report({ gaps = [], expansions = [] }: ReportProps) {
  return (
    <section>
      <div className="view-head">
        <h1>Gap Report</h1>
      </div>
      <div className="card empty-state">
        <h2>No Gap Report yet</h2>
        <p>
          Confirmed gaps with grounded evidence will render here after a Survey
          completes its zoom passes. ({gaps.length} gaps, {expansions.length}{" "}
          expansions loaded)
        </p>
      </div>
    </section>
  );
}
