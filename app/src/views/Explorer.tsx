export interface ExplorerProps {
  /** Run whose corpus/community map to render; null = latest completed. */
  runId?: string | null;
}

/**
 * Graph Explorer (view 4 of 5, ticket #14).
 *
 * TODO(wave 2):
 * - Sigma.js + graphology render of the Field graph (do NOT install these deps
 *   until wave 2 — kept out of package.json on purpose).
 * - Community-map default lens with drill-in to works/authors/topics.
 * - Data via /api/graph endpoints; long exports streamed over SSE subscribe().
 */
export default function Explorer({ runId = null }: ExplorerProps) {
  return (
    <section>
      <div className="view-head">
        <h1>Graph Explorer</h1>
      </div>
      <div className="card empty-state">
        <h2>Graph Explorer coming in wave 2</h2>
        <p>
          The community map of the Field graph renders here (Sigma.js +
          graphology). {runId ? `Selected run: ${runId}` : "No run selected."}
        </p>
      </div>
    </section>
  );
}
