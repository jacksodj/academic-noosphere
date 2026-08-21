# Build contracts — module ownership and interfaces

Ground rules for every build agent:

- **Own only your listed paths.** Never edit `pyproject.toml`, `uv.lock`,
  another module's files, or shared files (`models.py`, `config.py`,
  `server.py`) — if you need a change there, say so in your final report.
- All deps you need are already declared in `pyproject.toml` (groups:
  `embed`, `websearch` optional; dev group has pytest/pytest-asyncio/respx).
  Run tests with `uv run --group dev pytest tests/test_<yours>.py -q`.
- Import domain types from `noosphere.models` (terms match `CONTEXT.md`).
- Grounding rule: nothing from Web Search results is ever persisted except
  identifiers; every persisted record carries Provenance.
- Type-hint everything; no comments that narrate the obvious.
- This container: ladybug (Cypher) works; its ALGO extension **cannot
  download** here — all graph algorithms go through igraph. `sentence-
  transformers` is NOT installed — embedding code must lazy-import and tests
  must use the deterministic stub.

## Module map

| Module | Owner paths | Depends on |
|---|---|---|
| sidecar | `src/noosphere/sidecar.py`, `tests/test_sidecar.py` | duckdb |
| openalex | `src/noosphere/sources/openalex.py`, `sources/__init__.py`, `sources/ratelimit.py`, `tests/test_openalex.py` | httpx, sidecar cache API |
| graph | `src/noosphere/graph.py`, `src/noosphere/analysis/algos.py`, `analysis/__init__.py`, `tests/test_graph.py` | ladybug, igraph |
| queue | `src/noosphere/pipeline/queue.py`, `pipeline/__init__.py`, `tests/test_queue.py` | sidecar jobs API |
| ideonomy | `scripts/sync_ideonomy.py`, `vendor/ideonomy/**`, `src/noosphere/ideonomy/*`, `tests/test_ideonomy.py` | — |
| llm | `src/noosphere/llm/*`, `tests/test_llm.py` | anthropic (Bedrock client) |
| spa | `app/**` | — (npm; mock API) |
| survey (wave 2) | `src/noosphere/pipeline/survey.py`, `pipeline/embed.py`, `sources/websearch.py` | all of the above |
| analysis (wave 2) | `src/noosphere/analysis/whitespace.py`, `confirm.py`, `narrative.py`, `ranking.py` | graph, llm |
| reports (wave 2) | `src/noosphere/reports/*` | analysis, sidecar |
| api (wave 2) | `src/noosphere/api/*`, edits to `server.py` (integrator only) | everything |

## Key interfaces (implementations must match)

### sidecar.py (DuckDB, one file per data dir: `sidecar.duckdb`)

```python
class Sidecar:
    def __init__(self, db_path: Path): ...
    # runs & snapshots
    def create_run(self, run: Run) -> None
    def update_run(self, run_id: str, *, status: RunStatus | None = None, ...) -> None
    def get_run(self, run_id: str) -> Run | None
    def list_runs(self, field_name: str | None = None) -> list[Run]
    def add_run_works(self, run_id: str, work_ids: list[str]) -> None   # Run Snapshot
    def get_run_works(self, run_id: str) -> list[str]
    # immutable response cache (#11): key = sha256(f"{api}:{url_with_params}")
    def cache_get(self, key: str) -> str | None
    def cache_put(self, key: str, api: str, url: str, body: str) -> None
    # job state for the resumable queue
    def job_put(self, job_id: str, kind: str, payload: dict, status: str, run_id: str | None) -> None
    def job_update(self, job_id: str, *, status: str | None = None, checkpoint: dict | None = None) -> None
    def job_get(self, job_id: str) -> dict | None
    def jobs_pending(self) -> list[dict]
    # whitespace + gaps persistence
    def put_whitespace(self, w: WhitespaceCandidate) -> None
    def list_whitespace(self, run_id: str) -> list[WhitespaceCandidate]
    def put_gap(self, g: Gap) -> None
    def list_gaps(self, zoom_run_id: str | None = None) -> list[Gap]
    def put_expansion(self, e: IdeonomyExpansion) -> None
    def list_expansions(self, gap_id: str) -> list[IdeonomyExpansion]
```

### graph.py (ladybug store; igraph for algorithms)

```python
class GraphStore:
    def __init__(self, db_path: Path): ...
    def init_schema(self) -> None           # idempotent CREATE ... IF NOT EXISTS
    def upsert_works(self, works: list[Work]) -> None       # MERGE by openalex_id
    def upsert_authors(self, authors: list[Author]) -> None
    def upsert_topics(self, topics: list[Topic]) -> None
    def upsert_sources(self, sources: list[Source]) -> None
    def upsert_institutions(self, insts: list[Institution]) -> None
    def add_authorships(self, a: list[Authorship]) -> None  # AUTHORED with position
    def add_citations(self, c: list[Citation]) -> None      # CITES
    def add_work_topics(self, wt: list[WorkTopic]) -> None  # ABOUT with score
    def work_ids(self) -> set[str]
    def get_work(self, openalex_id: str) -> Work | None
    def citation_edges(self, within: set[str] | None = None) -> list[tuple[str, str]]
    def work_topic_rows(self) -> list[tuple[str, str, float]]  # (work, topic, score)
    def query(self, cypher: str) -> list[list]              # escape hatch
# analysis/algos.py — igraph over exported edges:
def louvain_communities(edges: list[tuple[str, str]], nodes: set[str]) -> dict[str, int]
def pagerank(edges: list[tuple[str, str]], nodes: set[str]) -> dict[str, float]
def community_centroid_similarity(embeddings: dict[str, list[float]], communities: dict[str, int]) -> dict[tuple[int, int], float]
def inter_community_edge_density(edges, communities) -> dict[tuple[int, int], float]
```

Schema DDL (ladybug Cypher, verified syntax): node tables Work, Author, Topic,
Source, Institution (STRING/INT64/DOUBLE/STRING[] props + PRIMARY KEY);
rel tables AUTHORED(FROM Author TO Work, position INT64), CITES(FROM Work TO
Work), ABOUT(FROM Work TO Topic, score DOUBLE), PUBLISHED_IN(FROM Work TO
Source), AFFILIATED_WITH(FROM Author TO Institution). Provenance fields
(source_api STRING, source_id STRING, retrieved_at STRING/TIMESTAMP) on every
table. Embeddings: `embedding DOUBLE[]` property on Work (HNSW index optional —
guard with try/except; not available without extensions here).

### sources/openalex.py

```python
class OpenAlexClient:
    def __init__(self, sidecar: Sidecar, api_key: str | None, rate: RateLimiter): ...
    async def works_search(self, query: str, per_page: int = 25, filters: dict | None = None) -> list[dict]
    async def work(self, openalex_id: str) -> dict | None
    async def works_batch(self, openalex_ids: list[str]) -> list[dict]   # filter=openalex_id:W1|W2 (max 50/req)
    async def referenced_and_citing(self, openalex_id: str) -> tuple[list[str], list[str]]
    @staticmethod
    def parse_work(raw: dict) -> tuple[Work, list[Author], list[Authorship], list[WorkTopic], list[Topic], Source | None, list[Institution]]
```
Every request goes through the sidecar cache first (immutable, explicit
refresh). Abstract reconstruction from `abstract_inverted_index`. api_key sent
as `api_key` query param; polite `mailto` if configured. `ratelimit.py`:
token-bucket `RateLimiter(rate_per_sec: float)` with `async acquire()`.

### pipeline/queue.py

```python
class JobQueue:
    def __init__(self, sidecar: Sidecar): ...
    def submit(self, kind: str, payload: dict, run_id: str | None = None) -> str
    async def worker(self, handlers: dict[str, Handler], poll_s: float = 0.5) -> None  # run until cancelled
    # Handler = Callable[[dict payload, Checkpoint], Awaitable[None]]
    # Checkpoint: .get() -> dict | None, .save(dict) -> None (persisted via sidecar.job_update)
```
Crash/restart resumability: pending+running jobs are re-picked-up; handlers
must be checkpoint-idempotent.

### ideonomy

`scripts/sync_ideonomy.py`: copies `methods/` (operators/organons/
dimension-prompts) from a local clone or GitHub into `vendor/ideonomy/`,
writes `vendor/ideonomy/UPSTREAM` (commit hash). Source clone available at
`/home/user/latentwill/ideonomy-skill` (ideonomy-rich/methods). Run it once so
vendor/ is populated and committed.

```python
# src/noosphere/ideonomy/picker.py
def pick_tuple(seed: str, catalog_dir: Path, n_operators: int = 2, n_dims: int = 3) -> IdeonomyTuple
def tuple_bodies(t: IdeonomyTuple, catalog_dir: Path) -> str   # concatenated method file bodies
```
Deterministic: same seed -> same tuple (use random.Random(seed)).

### llm/bedrock.py

```python
class SpendMeter:  # thread-safe accumulation
    def record(self, model: str, input_tokens: int, output_tokens: int) -> None
    def totals(self) -> dict  # {model: {input, output, est_usd}}

class LlmClient:
    def __init__(self, region: str, meter: SpendMeter, transport=None): ...  # transport injection for tests
    async def haiku_json(self, system: str, user: str, max_tokens: int = 2048) -> dict
    async def opus_json(self, system: str, user: str, max_tokens: int = 8192) -> dict
    async def opus_text(self, system: str, user: str, max_tokens: int = 8192) -> str
```
Use `anthropic.AnthropicBedrockMantle(aws_region=...)`; model IDs
`anthropic.claude-opus-5` / `anthropic.claude-haiku-4-5`; adaptive thinking
default (omit `thinking` param on Opus 5; Haiku 4.5 pre-4.6 rules — no
thinking needed for extraction). Streaming not required in v1. Estimated
pricing table for the meter: opus $5/$25 per MTok, haiku $1/$5 (list prices;
meter is an estimate and says so).

### SPA (app/)

Vite + React + TS. If `npm install` fails through the proxy, commit source
files anyway and report it. Views per #14: Dashboard, Whitespace Triage, Gap
Report reader, Graph Explorer (sigma + graphology), Settings/First-run. Wave 1
scaffolds routing + API client (`app/src/api.ts` reads `?port=&token=` or
`VITE_API_*`) + Dashboard/Settings with mocked data toggles.
```
