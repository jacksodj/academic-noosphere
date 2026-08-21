# Academic Noosphere

A local macOS app for a single researcher that surveys an academic field, builds an
author/topic/citation graph from scholarly APIs, surfaces literature gaps, and
recommends co-authors.

## Status: building (v1)

Planning is complete — every decision ticket on the wayfinder map is resolved
(`docs/architecture.md` is the locked spec, `docs/phase0-results.md` the spike
evidence). The v1 implementation now lives in this repo:

- **Python core** (`src/noosphere/`): survey pipeline (two-phase, resumable),
  LadybugDB graph + DuckDB sidecar, gap analysis, ideonomy engine, Bedrock LLM
  layer, localhost FastAPI (`uv run noosphere-core` prints a
  `{"port":…,"token":…}` handshake line).
- **SPA** (`app/`): React/TS — dashboard, whitespace triage, gap-report reader,
  sigma community explorer, settings. Dev: `cd app && npm install && npm run
  dev` (connect with `?port=&token=` from the handshake, or `VITE_MOCK=1` for
  fixture mode).
- **Tests**: `uv run --group dev pytest` (136 tests).
- Optional extras: `uv sync --extra embed` (SPECTER2 local embeddings),
  `--extra websearch` (AgentCore Gateway discovery client).

The wayfinder map remains the decision record:

- **Map**: [Wayfinder map: Academic Noosphere — Mac-local research mapping app](https://github.com/jacksodj/academic-noosphere/issues/1)
- Open decision tickets are the map's child issues. A ticket is on the **frontier**
  when it is open, unassigned, and everything on its `Blocked by:` line is closed.
- Work a ticket by invoking `/wayfinder` with the map URL (mattpocock-skills plugin).

## Core architectural constraints (from the reference docs)

- **AgentCore Web Search is discovery, never the graph source.** It returns snippets
  only (no authors, no citation edges) and its acceptable-use terms prohibit bulk
  extraction and building a database from Search Results.
- **The graph comes from scholarly APIs.** OpenAlex is primary (CC0, disambiguated
  author IDs, citation edges, Topics taxonomy); Semantic Scholar, Crossref, arXiv,
  PubMed are secondary. Every stored node/edge must trace to a DOI or OpenAlex ID.
- **Local-first.** The app runs on the Mac; AWS is used only where unavoidable
  (the Web Search connector lives behind an AgentCore Gateway).

See [`docs/reference-notes.md`](docs/reference-notes.md) for the condensed reference
material every planning ticket should be read against.
