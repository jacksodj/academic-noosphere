# Phase-0 spike results (ticket #7) — 2026-08-21

Executed via `infra/phase0-spike.yaml` (CloudFormation: Gateway + service role +
query-runner Lambda) with the `web-search` connector target pinned to `1.2.0`,
attached and torn down via the control-plane API. Account infra fully deleted
after the run. Cost: ~302 queries ≈ $2.11.

## Verdict: HEALTHY

The Amazon web index covers both sides of the Field (*memory for AI agents*)
at useful depth. **Web Search stays the discovery front door; the narrative-
mining booster in the gap-analysis design (#12) is switched ON.**

## Numbers

- 30/30 queries returned the full `maxResults` (10/10); **zero empty queries**;
  300 results total under a 7-domain scholarly include filter.
- Results per domain: `arxiv.org` 203 · `pubmed.ncbi.nlm.nih.gov` 45 ·
  `nature.com` 35 · `biorxiv.org` 14 · `openreview.net` 2 · `ar5iv.labs.arxiv.org` 1.
- Both intersection sides covered: agent-memory queries hit arXiv surveys and
  recent (2603–2605) preprint HTML pages; memory-science queries hit PubMed
  abstracts and Nature reviews; snippet quality was substantive (real abstract
  text, not boilerplate).

## Confirmed engineering facts

- Gateway tool name is prefixed: `web-search-tool___WebSearch` — discover via
  `tools/list`, don't hardcode `WebSearch`.
- `publishedDate` was a real date on only **144/300** results; the rest were
  `"unknown"` — the tolerant date parser is mandatory, missing dates =
  unknown, never defaulted.
- MCP handshake over the Gateway works with plain SigV4-signed JSON-RPC
  (initialize → notifications/initialized → tools/list → tools/call); session
  header optional in practice, handled if present.
- CloudFormation's `ConnectorSource` has **no `Version` property** — the
  1.2.0 pin must be applied via `CreateGatewayTarget` (control-plane), which
  is why the target lives outside the template.
