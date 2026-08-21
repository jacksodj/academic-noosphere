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

## Open (blocked) decisions

Graph schema (#9, awaits AUP answer #6) · Ingestion & caching (#11, awaits keys
#8) · Gap-analysis design (#12, awaits spike #7 + #9) · UI & graph viz (#14,
awaits #9) · Ideonomy design (#15, awaits #12).

Vocabulary: see `CONTEXT.md`. Source-document constraints: `docs/reference-notes.md`.
