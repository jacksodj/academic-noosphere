# Research: local performance baseline — hot paths, cache candidates, index safety

Resolves issue #17 (part of wayfinder map #21). Consumed by the performance
decisions ticket #20.

Measured 2026-08-23 against the **running app** (HTTP API only, handshake
token; DB files never opened directly) on the ~8,000-work coarse Run Snapshot
`e247eaac` ("Memory for AI agents") plus two ~1.7k-work zoom runs. Stack at
measurement time: duckdb **1.5.5** (uv.lock), LadybugDB graph, FastAPI/uvicorn.

---

## 1. Measurements

One cold call + 3 repeats per endpoint (`curl -w time_total`), payload =
response bytes. "Cold" is the first call of this session against a
long-running app process — OS file caches were likely already warm.

| Endpoint | Cold ms | Warm ms (3 repeats) | Payload |
|---|---:|---|---:|
| `GET /api/runs` | 6 | 2 · 2 · 2 | 1.3 KB |
| `GET /runs/{id}/insights` | **4142** | 3670 · 3703 · 3748 | 4.9 KB |
| `GET /runs/{id}/works` (no filter) | **3636** | 3774 · 3578 · 3768 | 15.9 KB |
| `…/works?topic_id=T10028` | 322 | 319 · 301 · 313 | 17.0 KB |
| `…/works?year_from=2023&year_to=2025` | **3908** | 3548 · 3592 · 3547 | 18.2 KB |
| `…/works?q=memory` | **3703** | 3722 · 3563 · 3668 | 17.5 KB |
| `…/works?topic+year+q` combined | 321 | 302 · 327 · 324 | 11.3 KB |
| `GET /runs/{id}/whitespace` | 3 | 2 · 2 · 2 | 9.8 KB |
| `GET /runs/{id}/report` (coarse, 8k) | 1048 | 1008 · 1002 · 1022 | 190.7 KB |
| `GET /runs/{id}/report.md` (coarse) | 1028 | 1032 · 1030 · 1021 | 40.6 KB |
| `GET /runs/{id}/report` (zoom, ~1.7k) | 290 | 253 (2nd run: 251 · 253) | 52.4 KB |
| `GET /gaps` | 2 | 2 · 2 · 2 | 12.9 KB |
| `GET /runs/{id}/progress` (Dashboard poll) | 9 | 3 · 3 · 3 | 0.2 KB |
| `GET /runs/{id}/activity` | 6 | 2 · 3 · 3 | 26.0 KB |
| `GET /api/spend` | 1 | 1 · 1 · 1 | 0.2 KB |

**No incidental caching anywhere**: repeats are flat (cold ≈ warm on every
endpoint). Every request recomputes from scratch.

The one seeming anomaly is the tell: `works?topic_id=` (322 ms) is **11×
faster** than `works` with *no* filter (3.6 s), despite doing strictly more
filtering. The topic filter builds an allow-set first and *skips the per-work
graph lookup* for the ~7,400 non-matching works. Per-work point lookups, not
filtering, dominate.

## 2. Cost analysis (what scans what, per request)

From `src/noosphere/analysis/insights.py`, `src/noosphere/reports/gaps.py`,
`src/noosphere/graph.py`, `src/noosphere/api/routes.py`:

- **`corpus_insights` and `list_works` issue one Cypher point-lookup per
  snapshot work** — `graph.get_work(wid)` in a Python loop over all 8,000 ids.
  Each lookup round-trips through ladybug and materializes the full Work row
  **including the `embedding DOUBLE[]` vector**, which none of these endpoints
  use. Arithmetic: 3.6 s / 8,000 ≈ **0.45 ms per lookup**, matching the
  topic-filtered case (≈610 lookups + fixed costs ≈ 0.32 s). Both also do a
  full `work_topic_rows()` scan of every ABOUT edge in the whole graph (not
  just the snapshot) — that plus `get_run_works` is the ~100–300 ms floor.
- **`assemble_report`** does `get_run_works`, `citation_edges(within=ids)`
  (a Cypher `WHERE a.openalex_id IN $ids AND b.openalex_id IN $ids` with an
  8,000-element list), **Louvain community detection per request** (igraph),
  a full ABOUT-edge scan, then per-work `get_work` for the works index
  (bounded: ≤30 works/community + evidence ids). ~1.0 s at 8k works, ~0.25 s
  at 1.7k.
- **Event-loop blocking**: `run_report`/`run_report_markdown` call
  `_assemble_report` **synchronously inside the async handler** (no
  `asyncio.to_thread`, unlike insights/works). Every report fetch freezes the
  entire API — including SSE `/events` — for the full second. Insights/works
  run off-loop, so they don't block others, but each burns a core for ~4 s.
- Sidecar-only endpoints (runs, gaps, whitespace, activity, progress, spend)
  are 1–9 ms — full DuckDB scans on index-free tables, and they are nowhere
  near being a problem.

### Projection to 3–5 folded-in fields (~25–40k works)

- **insights / unfiltered works / year / q filters are linear in snapshot
  size** (N point lookups): 8k → 3.7 s implies **~11–15 s at 25k and
  ~18–24 s at 40k, per request**. Unusable; the SPA would look hung.
- **report**: intra-snapshot citation edges grow faster than N; the
  `IN $ids` filter cost also grows with the id list. Extrapolating 1.7k→0.25 s
  / 8k→1.0 s gives roughly **4–8 s at 40k** — and today that whole time blocks
  the event loop, starving SSE heartbeats (15 s interval) and every poll.
- Payloads stay small (top-N truncation) except report JSON (~191 KB at 8k,
  growing with communities/gaps) — bandwidth is not the problem, compute is.

**The root fix is cheaper than any cache**: one batched query (`MATCH (w:Work)
WHERE w.openalex_id IN $ids RETURN …` *without* the embedding column, or a
bulk works fetch) replaces 8,000 round-trips. The DuckDB micro-benchmarks in
§4 show set-oriented scans at 40k rows are single-digit milliseconds; a single
batched graph read should land in the tens-of-ms range. Caching then becomes
an optimization on top, not a rescue.

## 3. Cache candidates and invalidation edges

Anchor invariant: a **Run Snapshot (`run_works`) is immutable once its run
completes**. Everything derived *only* from (snapshot × graph-at-completion)
is safely cacheable keyed by `run_id`. But the graph and sidecar are *not*
frozen after completion — the full list of mutation edges:

| # | Mutation edge | What it changes | Caches it invalidates |
|---|---|---|---|
| E1 | `POST /api/runs/{id}/redetect` | replaces pending whitespace rows | whitespace list; report (`examined_not_confirmed`) |
| E2 | `POST /api/runs/{id}/retry` | re-runs a failed job: may add `run_works` rows + graph writes | **everything** for that run — but only non-completed runs are retryable, so "cache only completed runs" sidesteps it |
| E3 | zoom completion / `confirm_candidate` | writes gaps, updates whitespace status | parent coarse run's report; `/gaps` |
| E4 | `POST /api/gaps/{id}/expand` (expansion write) | adds expansion rows | report (expansions blocks) |
| E5 | `scripts/backfill_titles.py` | **mutates `Work.title` in the graph** (runs with the app stopped) | insights `top_cited` titles, works listings, report works index — anything holding titles |
| E6 | `PUT /api/settings` (ranking weights) | report ranking order | report (weights are a query-input, not a corruption — key on them) |

Candidate matrix (placement: **A** in-process memo in FastAPI · **B**
precompute-at-persist into the sidecar · **C** HTTP cache headers to the SPA):

| Candidate | Keyed by | Safe when | A: in-process memo | B: precompute-at-persist | C: HTTP headers |
|---|---|---|---|---|---|
| Insights JSON (4.9 KB) | `run_id` | run completed | ✅ best fit — dict memo; E5 is safe because the script requires an app restart, which clears the memo | works; must store a version and regenerate after E5; adds a write to the crash-sensitive sidecar (use `_checkpoint()` like other rare writes) | `ETag: run_id`, `Cache-Control` for completed runs; helps SPA tab re-mounts only |
| Resolved works table per run (id/title/year/doi/cited_by, no embedding) | `run_id` | run completed | ✅ **highest leverage**: ~8k × ~100 B ≈ 1 MB/run; every `works` filter combination then evaluates in-memory in µs, killing the per-filter cache-key explosion | equivalent: a sidecar `run_works_resolved` table written at persist — makes works/insights sidecar-only reads | ETag per (run_id, filter tuple); server still computes on miss |
| Report: communities block (Louvain + edges) | `run_id` | run completed — depends only on snapshot × citation edges; E1–E4 don't touch either | ✅ memo; recompute is the expensive half (~0.7 s of the 1.0 s) | good fit: layout/communities at persist time was already the #17 hypothesis | via full-report caching only |
| Full report JSON | `run_id` + gaps/expansions/whitespace state + weights | never fully immutable (E1, E3, E4, E6) | memo keyed on a cheap content-version (counts/max-ids of gaps+expansions+whitespace + weights hash), or bus-event invalidation | ❌ too many edges | `ETag` = that same content-version — cheap 304s for the SPA |
| Sidecar-only endpoints (runs, gaps, whitespace, activity, spend, progress) | — | — | ❌ 1–9 ms; caching adds risk for nothing | ❌ | optional `no-cache` correctness headers only |

## 4. Index safety

### What happened here (local evidence)

Commit `8ee3916` (2026-08-22): after a SIGKILL, a crash-corrupted ART index on
`run_works` **answered `WHERE run_id = ?` with zero rows while a full scan
showed the 1,664-work snapshot intact** — silently, no error. It caused a
wrongly-refuted zoom candidate; the same failure class had earlier killed job
recovery via the `jobs.status` index. All secondary indexes were dropped
(`DROP INDEX IF EXISTS` repairs in `src/noosphere/sidecar.py` `_SCHEMA`);
primary-key ART indexes remain, mitigated by graceful shutdown + explicit
`CHECKPOINT` after high-value writes.

### Upstream state of DuckDB ART durability (all retrieved 2026-08-23)

- The project pins `duckdb>=1.5`; **uv.lock resolves 1.5.5** — i.e. the
  corruption was observed on a current-generation 1.5.x, not a stale build.
- ART indexes (including the implicit PK/UNIQUE ones) are **persisted to
  disk**, not rebuilt on open; docs recommend them only for "point and very
  highly selective (i.e., < 0.1%) queries" and warn they must fit in memory
  during creation. — https://duckdb.org/docs/current/sql/indexes.html
  (retrieved 2026-08-23)
- 1.5.x is still shipping ART correctness fixes: **1.5.1** "ships two fixes
  for ART indexes… updating to v1.5.1 is recommended" if using indexes or
  key/unique constraints (https://duckdb.org/2026/03/23/announcing-duckdb-151,
  retrieved 2026-08-23); **1.5.2** reorganized WAL replay, "correctly deal
  with empty checkpoint WAL files in WAL recovery", and fixed ART information
  loss on index-build cast (https://duckdb.org/2026/04/13/announcing-duckdb-152
  and https://github.com/duckdb/duckdb/releases/tag/v1.5.2, retrieved
  2026-08-23).
- Open heap-corruption report against 1.5.0's ART constraint path on
  file-backed DBs under `executemany`:
  https://github.com/duckdb/duckdb/issues/23046 (retrieved 2026-08-23).
- WAL recovery robustness under hard kills is itself an open question
  upstream: recovery trusts file size / checksums, and partial page-cache
  flushes can leave gaps — https://github.com/duckdb/duckdb/issues/19099
  (open, retrieved 2026-08-23). Historically CREATE/DROP INDEX were not even
  correctly serialized to WAL:
  https://github.com/duckdb/duckdb/issues/4891 (retrieved 2026-08-23).
- **Rebuild-on-open**: DuckDB has no native "don't persist, rebuild at open"
  index mode. The pattern is manual `DROP INDEX` + `CREATE INDEX` at
  connection open (our schema already runs the DROP half every open). At our
  row counts it would cost ~tens of ms — but it re-creates the persisted-ART
  exposure window until the next clean checkpoint, for zero measured benefit.

### Do we even need indexes? (measured)

Scratch DuckDB (fresh file in the scratchpad — the app's sidecar was never
touched), 40,000 synthetic rows mirroring `run_works` + a works table,
median of 10 runs, duckdb 1.5.5:

| Query shape @ 40k rows, no indexes | Median |
|---|---:|
| `run_works` scan `WHERE run_id = ?` (materializing all 40k ids) | 5.0 ms |
| Point lookup `WHERE work_id = ?` | 0.23 ms |
| Year-range filter + `ORDER BY cited DESC` | 1.6 ms |
| `title LIKE '%mem%'` + sort | 3.3 ms |
| 40k join snapshot→works + sort + `LIMIT 100` | 2.8 ms |

Sub-5 ms for every hot shape at 5× the current corpus. The live app agrees:
every sidecar-only endpoint answers in 1–9 ms today with zero secondary
indexes.

**Verdict: stay index-free.** Secondary ART indexes would buy ≤5 ms per query
and re-open a proven silent-wrong-answers failure mode that 1.5.x is still
patching. In-memory Python-side indexes / materialized sort orders are equally
unnecessary at DuckDB's end — the latency problem lives entirely in the
per-work *graph* round-trips, not in DuckDB. Re-evaluate only if a sidecar
table approaches ~10⁶ rows. Residual risk (PK ART indexes on `runs`, `gaps`,
etc.) stays mitigated by graceful shutdown + post-write `CHECKPOINT`; a cheap
belt-and-braces option is a startup sanity probe comparing an indexed PK
lookup against a full-scan count.

## 5. Viable options (for decisions ticket #20)

1. **Batch the graph reads** (fix before caching): replace the per-work
   `get_work` loop in `insights.py` with one `IN $ids` Cypher (or bulk read)
   that omits the embedding column. Turns 3.7 s → tens of ms and makes the
   40k projection a non-event. Highest value, no cache-coherency surface.
2. **Per-run resolved-works memo (in-process, completed runs only)**:
   ~1 MB/run; all works filters + insights aggregation drop to µs. Restart
   clears it, which makes the offline title-backfill edge (E5) safe for free.
3. **Insights memo keyed `run_id`** (subsumed by 2 if 2 is taken).
4. **Move report assembly off the event loop** (`asyncio.to_thread`, matching
   insights/works) — one-line fix for the API-freeze — and **memoize the
   communities/Louvain block per `run_id`** (safe: depends only on the
   immutable snapshot + citation edges). Optionally ETag the full report with
   a cheap content-version for SPA 304s.
5. **Keep DuckDB index-free**; no in-memory or materialized substitutes needed
   on the sidecar side. Revisit at ~10⁶ rows.
6. Optional, only if restart-cold latency ever matters: precompute insights +
   communities into the sidecar at run completion, versioned so backfill
   scripts can force regeneration; writes guarded by `_checkpoint()`.
