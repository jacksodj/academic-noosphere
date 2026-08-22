# Getting started — install, configure, run

Everything here reflects the code as shipped; where a value matters (credential
names, env vars) it is quoted from `src/noosphere/config.py`.

## 1. Prerequisites

- macOS on Apple Silicon (the graph store and embedder ship arm64 wheels;
  Linux works for development — this repo's test suite runs there).
- [uv](https://docs.astral.sh/uv/) (Python 3.11+ is resolved by uv).
- Node 20+ and npm (for the SPA).
- An AWS identity if you want Web Search discovery and Bedrock synthesis:
  an IAM Identity Center (SSO) profile is the recommended shape.

## 2. Install

```bash
git clone https://github.com/jacksodj/academic-noosphere.git
cd academic-noosphere

# Core + both optional extras (SPECTER2 embeddings, Gateway MCP client):
uv sync --extra embed --extra websearch

# SPA:
cd app && npm install && cd ..
```

Notes:
- `--extra embed` pulls sentence-transformers; the SPECTER2 model (~440 MB)
  downloads on first embedding call, not at install. Without this extra the
  app falls back to a deterministic stub embedder (fine for exploring the UI,
  useless for real relevance filtering — it logs a warning).
- `--extra websearch` is only needed when a Gateway URL is configured.

## 3. Configure

### 3.1 Scholarly API credentials (BYO — never stored in the repo)

**Easiest path: let the app do this.** On first start the app shows an
onboarding wizard that captures these (with links to where each key comes
from) straight into the Keychain; after that, Settings → API credentials
manages them. The rest of this section is the manual/headless alternative.

Credentials are read from the macOS Keychain (service `academic-noosphere`),
with environment variables as the dev override. Names:

| Keychain account | Env override | Where to get it |
|---|---|---|
| `openalex_api_key` | `NOOSPHERE_OPENALEX_KEY` | https://help.openalex.org/api/authentication (free key) |
| `s2_api_key` | `NOOSPHERE_S2_KEY` | https://www.semanticscholar.org/product/api (free, on request) |
| `ncbi_api_key` | `NOOSPHERE_NCBI_KEY` | NCBI account settings (free; raises PubMed to 10 req/s) |
| `crossref_mailto` | `NOOSPHERE_CROSSREF_MAILTO` | your email (Crossref/OpenAlex polite pool) |

Keychain setup, one line per credential:

```bash
security add-generic-password -s academic-noosphere -a openalex_api_key -w '<key>'
security add-generic-password -s academic-noosphere -a crossref_mailto -w 'you@example.com'
```

Only the OpenAlex key + mailto matter for v1's ingest; S2/NCBI are used by
lazy enrichment.

### 3.2 AWS (Bedrock synthesis + optional Web Search)

```bash
aws configure sso            # once; then per session:
aws sso login --profile <your-profile>
export AWS_PROFILE=<your-profile>
```

The identity needs `bedrock:InvokeModel` (Opus 5 + Haiku 4.5 via Bedrock) and,
for Web Search, `bedrock-agentcore:InvokeGateway` on your Gateway.

**Gateway (optional, enables Web Search discovery + the narrative booster):**
stand one up with the committed infra — `infra/phase0-spike.yaml` +
`scripts/phase0_infra.py up` creates Gateway + service role and attaches the
`web-search` connector pinned to 1.2.0, printing the Gateway MCP URL
(`scripts/phase0_infra.py down` removes it all). Costs ~nothing at rest;
searches are $7/1,000.

### 3.3 App settings

Runtime settings live in the Settings view (persisted, credential-free, to
`~/Library/Application Support/academic-noosphere/settings.json`): Gateway
URL, AWS region (default `us-east-1`), Web Search on/off, coarse corpus
target, relevance threshold, ranking weights. Env equivalents for headless
use: `NOOSPHERE_GATEWAY_URL`, `NOOSPHERE_AWS_REGION`, and
`NOOSPHERE_DATA_DIR` to relocate all local data (graph DB + sidecar +
settings).

## 4. Run

### As the Mac app (Tauri shell)

```bash
cd app && npx tauri build
open "src-tauri/target/release/bundle/macos/Academic Noosphere.app"
```

The shell spawns `uv run noosphere-core` itself (repo located via
`NOOSPHERE_REPO`, defaulting to the checkout the shell was compiled in — the
frozen-binary sidecar is deferred to packaging), reads the handshake off the
core's stdout, injects it into the SPA as `window.__NOOSPHERE__`, and kills the
core on quit. `npx tauri dev` gives the same shell with hot reload.

Only one core can run at a time — the graph + DuckDB sidecar hold single-process
file locks, so quit a hand-started core before launching the app (and note a
`kill` can take a while if a browser tab holds the SSE stream open; the shell
uses SIGKILL and is immune).

### Core + browser (dev mode)

```bash
uv run noosphere-core
# → one JSON handshake line on stdout: {"port": 51234, "token": "…"}
```

Then the UI:

```bash
cd app && npm run dev
# open the printed Vite URL with the handshake values appended:
#   http://localhost:5173/?port=51234&token=<token>
```

No backend handy? `VITE_MOCK=1 npm run dev` serves the whole UI on fixtures.

### First Survey walkthrough

1. **Dashboard → New Survey**: field name (e.g. `memory for AI agents`) and a
   few seed queries, one per line. The coarse run queues; progress and spend
   stream into the header meter.
2. **Triage** (`/triage`): when the coarse phase completes, Whitespace
   Candidates appear — bridges and thin cells with sparsity/low-citedness
   evidence. Click **Zoom** on the ones worth the spend.
3. **Report** (`/report`): each completed zoom run yields confirmed Gaps
   (ranked, component scores visible) and an "examined, not confirmed" list.
   **Expand (Opus)** on a gap generates a labeled-speculative Ideonomy
   Expansion; **Export Markdown** downloads the citable report.
4. **Explorer** (`/explorer`): the community map — topic-labeled clusters,
   whitespace highlighted between them, click to drill into member works.

### Tests

```bash
uv run --group dev pytest        # 136 tests
cd app && npm run build          # SPA type-check + build
```

## 5. Troubleshooting

- **"falling back to the deterministic StubEmbedder"** — install the `embed`
  extra; real relevance filtering needs SPECTER2.
- **Run status `failed`** — the error is recorded on the job:
  `SELECT kind, status, checkpoint FROM jobs` in
  `<data-dir>/sidecar.duckdb`. Most common cause: no network path to
  `api.openalex.org` or missing AWS credentials. Re-submitting the survey
  resumes from the last completed stage.
- **Report endpoint returns 501** — the reports module failed to import;
  run the test suite to see why.
- **Spend meter** shows estimates from list prices ($5/$25 Opus, $1/$5 Haiku
  per MTok); the AWS bill is authoritative.
