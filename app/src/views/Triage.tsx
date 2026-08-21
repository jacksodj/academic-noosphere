import type { WhitespaceCandidate } from "../types";

export interface TriageProps {
  /** Coarse-run candidates awaiting confirm/refute zoom passes. */
  candidates?: WhitespaceCandidate[];
}

/**
 * Whitespace Triage (view 2 of 5, ticket #14).
 *
 * TODO(wave 2):
 * - Fetch WhitespaceCandidates for the selected coarse run (/api/runs/{id}/whitespace).
 * - Card list with sparsity/low-citedness signals and evidence chips.
 * - Actions: start zoom pass, dismiss; live status via SSE subscribe().
 */
export default function Triage({ candidates = [] }: TriageProps) {
  return (
    <section>
      <div className="view-head">
        <h1>Whitespace Triage</h1>
      </div>
      <div className="card empty-state">
        <h2>Nothing to triage yet</h2>
        <p>
          Whitespace Candidates from a coarse Survey pass will appear here for
          confirm/refute zoom passes. ({candidates.length} loaded)
        </p>
      </div>
    </section>
  );
}
