# Academic Noosphere — locked v1 architecture

Assembled from the resolved wayfinder tickets (map: issue #1). Each section links
the ticket that decided it; the ticket's resolution comment is the authoritative
record.

## Shape

```
┌─────────────────────────── macOS ────────────────────────────┐
│  Tauri shell                                                 │
│  └─ React + TS SPA ── HTTP/SSE (localhost, per-launch token) │
│                              │                               │
│  Python core (uv-managed sidecar, FastAPI)                   │
│  ├─ Survey job queue (async, checkpointed, resumable)        │
│  ├─ Discovery client ── MCP/SigV4 ──► AgentCore Gateway ──►  │
│  │                                    web-search v1.2.0 (AWS)│
│  ├─ Resolution clients: OpenAlex · S2 · Crossref · arXiv ·   │
│  │                      PubMed                               │
│  ├─ LadybugDB graph (in-process Cypher, PageRank/Louvain,    │
│  │   HNSW)  ·  igraph escape hatch (eigenvector/Leiden)      │
│  ├─ Synthesis: Bedrock (Mantle client)                       │
│  │   Opus 5 = gap synthesis + ideonomy · Haiku 4.5 = volume  │
│  └─ Ideonomy engine (vendored latentwill/ideonomy-skill      │
│      catalog + picker)                                       │
└──────────────────────────────────────────────────────────────┘
```

Cloud footprint: **one AgentCore Gateway + one connector target + one IAM service
role** (us-east-1). No AgentCore Runtime, no server-side agent. AWS auth is the
local credential chain (IAM Identity Center SSO profile). [#5]

## Decisions (with owning tickets)

- **Gaps-first v1** — survey + graph are substrate for the grounded Gap Report;
  co-author recommendation is v2 (#13 closed out of scope). First Field:
  *memory for AI agents* (agent memory architectures × human memory science), so
  PubMed/MeSH is load-bearing alongside arXiv. [#2]
- **Grounding rule** — every factual claim cites a DOI/OpenAlex ID or
  URL+retrieval-date; every run persists a Run Snapshot (resolved ID set);
  speculative content is segregated and labeled; ungrounded-unlabeled text is a
  bug. [#2]
- **Discovery vs Resolution split** — Web Search discovers identifiers only
  (snippets are never persisted; acceptable-use constraint); scholarly APIs are
  the system of record; every node/edge traces to a DOI/OpenAlex ID. [#2, refs]
- **Graph store** — LadybugDB embedded (MIT Kùzu fork), Parquet-exportable;
  DuckDB + python-igraph as complement/fallback; Neptune Analytics documented
  only as a scale-out path. [#4]
- **Form factor** — Python core (uv) in a Tauri shell; React+TS SPA over
  localhost FastAPI+SSE with a per-launch token; in-app resumable job queue,
  checkpoint-and-resume across sleep/restarts; no launchd. Frozen-binary
  sidecar deferred to packaging. [#3]
- **LLM layer** — Bedrock via `AnthropicBedrockMantle` on the same SigV4 chain;
  `anthropic.claude-opus-5` for gap synthesis + Ideonomy Expansion, Haiku 4.5
  for query planning/triage/extraction; live spend meter, no auto-stop;
  aggressive prompt caching. [#10]
- **Ideonomy Expansion** — in-app: vendored method catalog from
  latentwill/ideonomy-skill, applied by the app's LLM to selected gaps,
  rendered as a labeled speculative section of the Gap Report. Design in #15.

- **Two-phase Survey** — coarse core (~5–10k works, seed-and-expand, relevance =
  topic + embedding similarity only; low-citedness is a gap *signal*, never a
  filter) surfaces Whitespace Candidates; bounded zoom passes confirm each via
  three checks (sparsity-at-depth, narrative demand, temporal profile), with
  unconfirmed candidates reported as "examined, not confirmed". Composite gap
  ranking with visible component scores. [#11, #12]
- **Narrative evidence** — corpus-first (abstracts + S2 citation contexts);
  Web Search snippet mining is an additive stream toggled by the Phase-0 spike
  verdict. [#12]
- **UI** — five views: dashboard, whitespace triage, interactive Gap Report
  reader (citation chips, evidence filters, Markdown export), graph explorer
  (Sigma.js + graphology, community-map default lens with drill-in),
  settings/first-run. [#14]
- **Ideonomy engine** — vendored catalog (pinned upstream commit, sync script);
  seeded Python picker (run+gap+attempt — reproducible; Re-roll = attempt N+1);
  structured-JSON expansion contract with per-idea operator provenance and
  nearest-work citation; on-demand per confirmed Gap. [#15]
- **BYO credentials** — AWS profile + scholarly API keys are per-user runtime
  config in the Keychain; nothing credential-shaped in the repo or a build.
  AUP: narrow reading adopted, risk accepted. [#6, #8]

## Status

**All decision tickets resolved (2026-08-21) — the spec is build-ready.** The
Phase-0 spike (#7, `scripts/phase0_infra.py` + `scripts/phase0_spike.py`) runs
as a config-toggle input for Web Search narrative mining. Remaining fog items
on the map are implementation-time detail.

Vocabulary: see `CONTEXT.md`. Source-document constraints: `docs/reference-notes.md`.
