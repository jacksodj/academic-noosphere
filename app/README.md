# Academic Noosphere SPA

Vite + React + TypeScript front end for the noosphere core (localhost FastAPI).

## Run

```sh
npm install
VITE_MOCK=1 npm run dev        # standalone, fixture data (no core needed)
npm run dev                    # against a running core: set VITE_API_PORT / VITE_API_TOKEN
npm run build                  # tsc -b && vite build
```

In the Tauri shell the core's stdout handshake (`{"port":…,"token":…}`) is passed
to the SPA as `?port=&token=` URL params; env vars are the dev fallback
(`src/api.ts`).

## Notes for the integrator

- **SSE auth**: `subscribe()` in `src/api.ts` sends the bearer token as a
  `?token=` query param because EventSource cannot set headers — the core's
  auth middleware must accept `?token=` on SSE endpoints.
- Wired endpoints (see `src/endpoints.ts`; all mocked under `VITE_MOCK=1`):
  `GET /api/runs` · `POST /api/surveys` · `GET/PUT /api/settings` ·
  `GET /api/runs/{id}/whitespace` · `POST /api/whitespace/{id}/zoom {run_id}` ·
  `GET /api/gaps?zoom_run_id=` · `GET /api/gaps/{id}/expansions` ·
  `POST /api/gaps/{id}/expand` · `GET /api/runs/{id}/report` (+`/report.md`) ·
  `GET /api/spend` · `GET /api/events` (SSE).
- **Report JSON contract** (`GET /api/runs/{zoom_run_id}/report`, mirrored by
  `GapReport` in `src/types.ts`): besides `gaps` + `examined_not_confirmed`, it
  must include `works: {W…: {work_id, title, year, doi}}` (citation-chip
  resolution), `communities: [{id, label, size, top_topics, works: [...ids]}]`
  and `community_edges: [{source, target, weight}]` — the Graph Explorer builds
  its community-map lens from these (no dedicated graph endpoint in v1).
- `POST /api/whitespace/{id}/zoom` is expected to return
  `{run: Run, candidate: WhitespaceCandidate}` (`ZoomResponse`).
- `src/types.ts` mirrors `src/noosphere/models.py` (snake_case as-is; datetimes
  as ISO strings).
- Explorer renders with Sigma.js + graphology (installed); the work-level full
  graph is intentionally not rendered in v1 — drill-in is a member-work list.
