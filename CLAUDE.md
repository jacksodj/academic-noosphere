# CLAUDE.md

## What this project is

Academic Noosphere: a Mac-local app (personal, single-user) that surveys an
academic field and surfaces grounded literature gaps. Currently in the
**planning/wayfinding phase** — most decisions live on the issue tracker, not in
code.

## Orientation, in order

1. `CONTEXT.md` — the glossary. Use its terms exactly (Survey, Field, Gap
   Report, Grounded Claim, Run Snapshot, Discovery vs Resolution, Ideonomy
   Expansion).
2. Wayfinder map — issue #1 (label `wayfinder:map`). Child tickets carry
   `Blocked by:` lines; a ticket is on the frontier when open, unassigned, and
   all blockers are closed. Work tickets via the `/wayfinder` skill
   (mattpocock-skills plugin); one decision ticket per session.
3. `docs/architecture.md` — the locked v1 architecture with ticket references.
4. `docs/reference-notes.md` — condensed source-document constraints
   (AgentCore Web Search limits + acceptable use, scholarly API landscape).

## Hard rules

- **Grounding**: every factual claim in generated reports must cite a
  DOI/OpenAlex ID or URL+retrieval-date; speculative output is segregated and
  labeled. Never persist Web Search result content — identifiers only
  (acceptable-use constraint); scholarly APIs are the system of record.
- **Stack** (decided; don't relitigate in implementation sessions): Python core
  (uv) · Tauri + React/TS SPA · LadybugDB graph · Bedrock Mantle client
  (Opus 5 synthesis / Haiku 4.5 volume) · AgentCore Web Search pinned to
  connector `1.2.0`.

## Conventions

- Development branch: `claude/mac-local-app-setup-p2kk2f`; `main` receives
  merges/PRs.
- Python: uv-managed; run things with `uv run`. Scripts may use PEP 723 inline
  deps (see `scripts/phase0_spike.py`).
- Record resolved decisions on their ticket (resolution comment + close) and
  gist them into the map's "Decisions so far" — never only in chat.
