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
- Expected wave-2 endpoints: `GET /api/runs`, `POST /api/surveys`
  (`{field_name, seed_queries}`), `GET/PUT /api/settings` (mirror of
  `noosphere.config.Settings`).
- `src/types.ts` mirrors `src/noosphere/models.py` (snake_case as-is; datetimes
  as ISO strings).
- Sigma.js/graphology are intentionally not installed until wave 2 (Explorer).
