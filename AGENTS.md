# AGENTS.md

## What this project is

Academic Noosphere is a Mac-local, single-user app that surveys an academic
field, builds an author/topic/citation graph from scholarly APIs, and surfaces
grounded literature gaps as a Gap Report (with an optional, labeled-speculative
Ideonomy Expansion section). v1 is built and ships as a signed, notarized
`.dmg` on GitHub Releases. Current work (v1.1: multi-field growth, seed-paper
surveys, local performance, Sources filter and sort) is planned on the
wayfinder map, issue #21.

## Orientation, in order

1. `docs/CONTEXT.md`: the glossary. Use its terms exactly (Survey, Field, Gap
   Report, Whitespace Candidate, Grounded Claim, Run Snapshot, Discovery vs
   Resolution, Ideonomy Expansion), in code, tests, and issue titles.
2. The wayfinder maps. Issue #1 is the v1 map (closed; its decisions still
   bind). Issue #21 is the open v1.1 map. Tickets are its sub-issues; a ticket
   is on the frontier when it is open, unassigned, and every blocker is closed.
   Work tickets with the `/wayfinder` skill (`.claude/skills/wayfinder/`). The
   v1.1 map carries execution: a ticket session resolves its decision and lands
   the implementation and tests in the same session where feasible.
3. `docs/architecture.md`: the locked v1 architecture, with the ticket that
   decided each part.
4. `docs/reference-notes.md`: condensed source-document constraints (AgentCore
   Web Search limits and acceptable use, the scholarly API landscape).
5. `docs/getting-started.md`: install, credentials, AWS and Gateway setup, the
   first-Survey walkthrough, troubleshooting.
6. `docs/build-contracts.md`: ground rules the v1 build agents worked to.

## Layout

- `src/noosphere/`: the Python core, a localhost FastAPI server.
  - `server.py`: entry point (`noosphere-core`); prints a one-line
    `{"port":…,"token":…}` handshake on stdout.
  - `api/`: `/api` routes and app state. `app/src/endpoints.ts` is the
    client-side mirror of `api/routes.py`.
  - `pipeline/`: the two-phase Survey orchestrator, the resumable job queue,
    embeddings (SPECTER2, ONNX or sentence-transformers, stub fallback), and
    the first-run model download.
  - `sources/`: Resolution clients (OpenAlex is the system of record;
    Crossref and Semantic Scholar enrichment), rate limiting, and the Web
    Search Discovery client (AgentCore Gateway over MCP, connector 1.2.0).
  - `graph.py` (LadybugDB literature graph), `sidecar.py` (DuckDB: runs, Run
    Snapshots, response cache, jobs).
  - `analysis/`: whitespace detection, zoom confirmation, ranking, insights,
    narrative-gap mining, igraph algorithms.
  - `reports/`: Gap Report assembly, Markdown rendering, and the grounding
    linter.
  - `ideonomy/`: the seeded method-tuple picker and expansion.
  - `llm/`: the Bedrock (Mantle) client with spend metering, and the prompts.
  - `config.py`: settings and credentials (Keychain first, env overrides).
    `aws_setup.py`: in-app AWS checks. `models.py`: the domain types.
- `tests/`: the pytest suite. Hermetic: HTTP is mocked with respx, the
  Keychain with an in-memory fake, Bedrock with a fake transport.
- `app/`: the React/TS SPA (Vite). Views are in `app/src/views/`;
  `app/src/mock.ts` holds the fixtures served under `VITE_MOCK=1`.
- `app/src-tauri/`: the Tauri (Rust) shell. It spawns the core as a sidecar,
  reads the handshake, and injects it into the SPA as `window.__NOOSPHERE__`.
- `packaging/`: the PyInstaller spec for the frozen sidecar, entitlements, and
  `sign-and-dmg.sh` (sign, notarize, staple, build the DMG).
- `infra/phase0-spike.yaml` with `scripts/phase0_infra.py up|down`: the
  optional AgentCore Gateway (CloudFormation) that enables Web Search.
- `scripts/`: maintenance scripts (`sync_ideonomy.py`,
  `export_specter2_onnx.py`, `backfill_titles.py`, the Phase-0 spike), plus
  the standard `setup-hooks.sh` and `issue-field.sh`.
- `vendor/ideonomy/`: the vendored ideonomy method catalog (CC-BY-4.0),
  pinned in `vendor/ideonomy/UPSTREAM`; refresh with `scripts/sync_ideonomy.py`.
- `docs/`: architecture, glossary, getting started, reference notes, Phase-0
  results, an example Gap Report, the launch blog post source, and
  `docs/agents/` (issue tracker, triage labels, domain docs for the skills).

## Build, test, run

```bash
uv sync --group dev                     # core + test deps (add --extra embed / websearch / onnx as needed)
uv run --group dev pytest -q            # Python tests: about 200, under a minute, no credentials needed
cd app && npm ci && npm run build       # SPA type-check and production build
cd app && npm run lint                  # oxlint (warnings only today)
```

Run in dev mode: `uv run noosphere-core`, then `cd app && npm run dev` and open
the Vite URL with `?port=<port>&token=<token>` from the handshake. No backend:
`VITE_MOCK=1 npm run dev` serves the whole UI on fixtures. `.claude/launch.json`
has a `spa-dev` preview on port 5175. Only one core can run at a time (the
graph and DuckDB sidecar hold single-process file locks).

Mac app: `cd app && npx tauri dev` (hot reload) or `npx tauri build`. The
frozen sidecar for a release build:
`uv run --exact --no-dev --extra onnx --extra websearch --group pkg pyinstaller packaging/noosphere-core.spec`
(never `--extra embed`: torch stays out of the bundle). Release builds, signing
and notarization run only on the owner's Mac with his Developer ID; CI never
builds or signs a release.

CI: the `tests` job in `.github/workflows/pr-checks.yml` runs the pytest suite
and the SPA build. Run both locally before pushing.

## Hard rules

- **Grounding**: every factual claim in a generated report cites a DOI/OpenAlex
  ID or URL plus retrieval date; speculative output is segregated and labeled;
  an unlabeled statement that is not a Grounded Claim is a bug.
- **Discovery vs Resolution**: never persist Web Search result content, only
  identifiers (acceptable-use constraint). Scholarly APIs are the system of
  record; every node and edge traces to a DOI or OpenAlex ID.
- **Stack** (decided; don't relitigate in implementation sessions): Python core
  (uv), Tauri + React/TS SPA, LadybugDB graph with a DuckDB sidecar, Bedrock
  Mantle client (Opus 5 for synthesis, Haiku 4.5 for volume), AgentCore Web
  Search pinned to connector `1.2.0`.
- **Credentials are bring-your-own**: scholarly API keys and the AWS profile
  live in the Keychain or env at runtime; nothing credential-shaped goes in the
  repo or a build.
- **DuckDB indexes**: the sidecar stays index-free (crash-corrupted ART indexes
  once silently returned zero rows). Reintroducing any index must answer that
  history; keep graceful shutdown and CHECKPOINT after critical writes.

## Conventions

- `main` is the default branch and holds the latest code. Work on a branch in a
  worktree and merge to `main` through a PR (see Guardrails). The v1 build's
  long-lived development branch (`claude/mac-local-app-setup-p2kk2f`),
  fast-forwarded into `main`, is retired: don't push to `main` directly.
- Python is uv-managed; run things with `uv run`. Scripts may use PEP 723
  inline deps (see `scripts/phase0_spike.py`). Type-hint everything; no
  comments that narrate the obvious.
- Import domain types from `noosphere.models`; its names match
  `docs/CONTEXT.md`.
- An API change touches `src/noosphere/api/routes.py`, `app/src/endpoints.ts`,
  `app/src/types.ts`, and the `app/src/mock.ts` fixtures together.
- Record resolved decisions on their ticket (resolution comment, then close)
  and gist them into the map's "Decisions so far", never only in chat.
- The repo is public: no account IDs, personal paths, or private details in
  commits, issues, or docs.

## Guardrails

The same controls as Dennis's other repos. Work with them, not around them.

- After cloning, run `scripts/setup-hooks.sh`. It points git at `.githooks/` (gitleaks scans every commit and push) and installs the pinned gitleaks. The Claude Code hook in `.claude/settings.json` runs it automatically before any commit or push.
- For a gitleaks false positive, add a targeted allowlist entry to `.gitleaks.toml`; commits always go through the hooks (no `--no-verify`).
- Work in a worktree. The main checkout stays on `main`, clean, and read-only for sessions. Before your first edit, call `EnterWorktree` with a name for the work: it cuts a branch from `origin/main` under `.claude/worktrees/` (gitignored). Push that branch and open the PR from it; to rename the branch, use `git branch -m`, never `git switch -c`, which leaves an orphaned branch behind. After the merge, GitHub deletes the remote branch, the Code tab's auto-archive removes a session's worktree, and the next session in the repo sweeps the merged local branch; never use `--force`, `-D`, `reset --hard` or `clean -f` to tidy up.
- `main` is protected: the `tests`, `secrets`, and `vulnerabilities` jobs in `.github/workflows/pr-checks.yml` must pass. Push a branch, open a PR, and merge with `gh pr merge --auto --squash`, including tracker and docs edits. No `--admin`, no force-push to `main`.
- The workflow uses no repository secrets and never `pull_request_target`. Keep the three job names stable; add the project's build and test steps to `tests` with the first build work. The vulnerability steps switch on by themselves when Python requirements, Python code, or npm lockfiles appear; add an audit for any other package manager the project adopts.
- OpenTofu/Terraform: CI runs `tofu fmt`, `tofu validate`, and tflint on any `.tf` files. Destroys and state surgery (`destroy`, `-destroy`, `state rm/push`) never run locally; stateful resources carry `prevent_destroy = true`; real infrastructure with state adopts the owner's reusable OpenTofu deploy workflow (plan, destroy-guard, and the `allow-destroy` label that only Dennis adds).
- Anything Dennis must do is a `needs-human` issue with a `**Next:**` line, filed when it comes up rather than left in a reply, and closed or rewritten when the Next goes stale (see `docs/agents/issue-tracker.md`). If the effort spans repos, every repo is linked to the effort's private GitHub Project so the band shows the siblings' items. Give each one a Priority (`scripts/issue-field.sh <n> Priority High`). Decisions go to him one at a time through AskUserQuestion.
- Roadmap data lives on each issue as organization issue fields: set Horizon (`scripts/issue-field.sh <n> Horizon Now`) when charting a map or triaging a report, file tickets with `--parent` and `--blocked-by`, and leave Start date and Target date to the `roadmap-dates` workflow (see `docs/agents/issue-tracker.md`).
