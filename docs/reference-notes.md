# Reference notes — AgentCore Web Search & scholarly data layer (August 2026)

Condensed from two source documents (2026-08-20): an account-team research note on
AgentCore Web Search capability fit for academic research mapping, and a technical
breakdown / scholarly-graph integration reference. These notes are the shared context
for every ticket on the wayfinder map (issue #1).

## AgentCore Web Search — what it is

- Fully managed, MCP-exposed **built-in connector target on AgentCore Gateway**
  (`connectorId: "web-search"`), GA June 2026. Not a standalone API — always invoked
  through a Gateway you own, tool name `WebSearch`, discovered via `tools/list`.
- Backed by an Amazon-operated web index (tens of billions of docs, refreshed within
  minutes) plus the Amazon Knowledge Graph. Zero data egress — queries never leave AWS.
- **Regions** (Aug 2026): us-east-1, eu-west-1, ap-northeast-1. **Pricing**: $7 per
  1,000 queries; Gateway invocations billed separately ($0.005 / 1,000 API invocations).
- **Input**: `query` ≤ 200 chars; `maxResults` 1–25 (default 10); connector `1.2.0`+
  adds `filters` — domain include/exclude (≤100 each, root domain matches subdomains)
  and published-date range (ISO-8601 UTC, web results only).
- **Version trap**: default connector version is `1.1.0`; the `filters` object exists
  only in `1.2.0`+. Pin `source.version = "1.2.0"`.
- **Response**: `text` (extractive snippet), `url`, `title`, `publishedDate` (format
  inconsistent — parse defensively). No author field, no citation edges, no relevance
  score, no full page text. Knowledge-graph observations arrive as key/value text in
  `text` with null `title`/`url`; there is no queryable graph API.
- **Quotas** (adjustable): Web Search request rate 10 TPS; search-based tool-call rate
  25/min; gateway invocation timeout 15 min; max payload 6 MB.
- **Acceptable use (the binding constraint)**: you may not "extract, store, or
  reproduce content from Search Results in bulk" or "build or populate a competing
  index or database", and must retain/display source citations. Web Search is a
  grounding/discovery tool, **not** a corpus-ingestion pipe.

## Capability fit (from the account-team note)

| Requirement | Web Search fit |
|---|---|
| 1. Survey a field | ✅ Right tool (domain + date filters, fresh index) |
| 2. Author/topic graph | ❌ Wrong tool (no authors/edges; AUP risk) — scholarly APIs |
| 3. Literature gaps | ⚠️ Split: narrative gaps via Web Search + LLM; structural gaps via the graph |
| 4. Co-author ranking | ❌ Wrong tool — graph algorithms; Web Search only enriches a shortlist |

## Scholarly data layer (system of record for the graph)

- **OpenAlex** (primary): ~322M works, 2.6B+ citation edges, disambiguated author IDs,
  ROR institutions, 4-level Topics taxonomy (~4,516 topics). CC0 data. API key required
  since Feb 2026 — free tier ≈ 100k credits/day, 10 req/s; bulk snapshot available.
- **Semantic Scholar S2AG**: 205M+ papers, citation *intent* classification,
  SPECTER2 embeddings, free Recommendations API. ODC-BY.
- **Crossref** (DOI reconciliation, polite pool via mailto), **arXiv** (1 req/3s,
  metadata CC0), **PubMed E-utilities** (3→10 req/s with free NCBI key, MeSH),
  **ORCID/DBLP** for author disambiguation.
- Google Scholar has no API and prohibits scraping — not a compliant source.
- Design rule: **every stored node/edge traces to a DOI or OpenAlex ID resolved
  through a scholarly API.** This is also the clean answer to the AUP constraint —
  Web Search discovers; scholarly APIs resolve and persist.

## Prior (cloud) candidate architecture — being re-evaluated for Mac-local

The source docs sketched: AgentCore Runtime agents (survey / graph-builder / gap /
ranker), one Gateway with two targets (web-search connector + a self-built scholarly
MCP server), Amazon Neptune Analytics as the graph store (PageRank, Louvain, vector
search), AgentCore Memory for session state. The Mac-local pivot re-opens which of
these stay cloud-side; see tickets #4 (local graph store) and #5 (cloud/local split).

## Key open questions carried onto the map

1. AUP written confirmation for the discover-then-resolve-via-OpenAlex pattern → ticket #6.
2. Does the Amazon index cover academic sources at useful depth? → Phase-0 spike, ticket #7.
3. Service-managed rate ceiling for the web-search target (docs list 10 TPS, adjustable).
4. Reproducibility requirement (index refresh makes Web Search non-reproducible;
   snapshot resolved DOI sets if citable output is required) → ticket #2.

## Engineering constraints to design around

- 200-char queries → query-planner decomposes a field into many short queries.
- No pagination cursor → paginate by query variation; dedup keyed on URL.
- `publishedDate` inconsistent → tolerant parser; missing dates = unknown.
- Author names must never be LLM-extracted from snippets (disambiguation fails
  silently) — OpenAlex author IDs are the only defensible node key.
- Cost shape: retrieval is cheap (~$28/mo for 10 fields refreshed monthly at 400
  queries each); model inference dominates.
