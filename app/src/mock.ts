/**
 * Fixture data served when VITE_MOCK=1, so the SPA runs standalone before the
 * wave-2 API exists. Shapes mirror src/types.ts exactly.
 *
 * Fixture world: coarse run-0001 over "memory for AI agents" surfaced six
 * Whitespace Candidates; zoom run-0004 (ws-0004) completed and produced a Gap
 * Report; run-0002 (ws-0007) is still zooming; run-0003 (ws-0011) is pending.
 */

import type {
  GapReport,
  CredentialStatus,
  IdeonomyExpansion,
  Run,
  Settings,
  SpendSummary,
  WhitespaceCandidate,
} from "./types";

export const mockRuns: Run[] = [
  {
    run_id: "run-0001",
    field_name: "memory for AI agents",
    phase: "coarse",
    parent_run_id: null,
    whitespace_id: null,
    query_manifest_hash: "a3f9c1",
    status: "completed",
    started_at: "2026-08-18T14:02:11Z",
    finished_at: "2026-08-18T15:47:36Z",
  },
  {
    run_id: "run-0004",
    field_name: "memory for AI agents",
    phase: "zoom",
    parent_run_id: "run-0001",
    whitespace_id: "ws-0004",
    query_manifest_hash: "c44e19",
    status: "completed",
    started_at: "2026-08-19T10:05:00Z",
    finished_at: "2026-08-19T11:12:44Z",
  },
  {
    run_id: "run-0002",
    field_name: "memory for AI agents",
    phase: "zoom",
    parent_run_id: "run-0001",
    whitespace_id: "ws-0007",
    query_manifest_hash: "b81d02",
    status: "running",
    started_at: "2026-08-21T09:15:00Z",
    finished_at: null,
  },
  {
    run_id: "run-0003",
    field_name: "memory for AI agents",
    phase: "zoom",
    parent_run_id: "run-0001",
    whitespace_id: "ws-0011",
    query_manifest_hash: null,
    status: "pending",
    started_at: null,
    finished_at: null,
  },
  {
    // realistic uuid id: the dashboard table must fit these (retry button row)
    run_id: "e247eaac-8fe2-4f38-a583-2783e61bb9ee",
    field_name: "memory for AI agents",
    phase: "coarse",
    parent_run_id: null,
    whitespace_id: null,
    query_manifest_hash: null,
    status: "failed",
    started_at: "2026-08-22T13:37:33Z",
    finished_at: null,
  },
];

export const mockSettings: Settings = {
  onboarded: true,
  aws_region: "us-east-1",
  gateway_url: "https://example-gateway.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
  web_search_enabled: true,
  opus_model: "anthropic.claude-opus-5",
  haiku_model: "anthropic.claude-haiku-4-5-20251001-v1:0",
  coarse_corpus_target: 8000,
  relevance_threshold: 0.35,
  ranking_weights: {
    sparsity: 1.0,
    narrative_demand: 1.0,
    recency: 0.5,
    low_citedness: 0.5,
  },
};

/** Whitespace Candidates keyed by coarse run id (GET /api/runs/{id}/whitespace). */
export const mockWhitespace: Record<string, WhitespaceCandidate[]> = {
  "run-0001": [
    {
      whitespace_id: "ws-0004",
      run_id: "run-0001",
      kind: "bridge",
      description:
        "Almost no work connects episodic memory buffers in LLM agents with interference-based forgetting models from cognitive psychology.",
      community_a: 1,
      community_b: 4,
      topic_id: null,
      sparsity_score: 0.91,
      low_citedness_signal: 0.62,
      evidence: [
        { kind: "work", work_id: "W4390112345", url: null, retrieved_at: null, quote: null },
        { kind: "work", work_id: "W4377651209", url: null, retrieved_at: null, quote: null },
        {
          kind: "web",
          work_id: null,
          url: "https://arxiv.org/abs/2405.11111",
          retrieved_at: "2026-08-18T15:01:22Z",
          quote: null,
        },
      ],
      status: "confirmed",
      not_confirmed_reason: null,
    },
    {
      whitespace_id: "ws-0007",
      run_id: "run-0001",
      kind: "bridge",
      description:
        "Sparse coupling between agent memory architecture papers and the systems-consolidation literature (sleep replay, schema integration).",
      community_a: 0,
      community_b: 3,
      topic_id: null,
      sparsity_score: 0.84,
      low_citedness_signal: 0.48,
      evidence: [
        { kind: "work", work_id: "W4402998811", url: null, retrieved_at: null, quote: null },
        { kind: "work", work_id: "W4315820077", url: null, retrieved_at: null, quote: null },
      ],
      status: "zooming",
      not_confirmed_reason: null,
    },
    {
      whitespace_id: "ws-0011",
      run_id: "run-0001",
      kind: "thin_cell",
      description:
        "Thin coverage cell: evaluation benchmarks for long-horizon agent memory that control for retrieval-augmentation confounds.",
      community_a: null,
      community_b: null,
      topic_id: "T11636",
      sparsity_score: 0.77,
      low_citedness_signal: 0.71,
      evidence: [
        { kind: "work", work_id: "W4409120334", url: null, retrieved_at: null, quote: null },
      ],
      status: "zooming",
      not_confirmed_reason: null,
    },
    {
      whitespace_id: "ws-0002",
      run_id: "run-0001",
      kind: "thin_cell",
      description:
        "Thin coverage cell: privacy-preserving deletion (\"machine unlearning\") applied to persistent conversational agent memory stores.",
      community_a: null,
      community_b: null,
      topic_id: "T10883",
      sparsity_score: 0.69,
      low_citedness_signal: 0.55,
      evidence: [
        { kind: "work", work_id: "W4398220145", url: null, retrieved_at: null, quote: null },
        { kind: "work", work_id: "W4370015566", url: null, retrieved_at: null, quote: null },
      ],
      status: "candidate",
      not_confirmed_reason: null,
    },
    {
      whitespace_id: "ws-0009",
      run_id: "run-0001",
      kind: "bridge",
      description:
        "Apparent disconnect between vector-store retrieval papers and hippocampal indexing theory.",
      community_a: 2,
      community_b: 3,
      topic_id: null,
      sparsity_score: 0.58,
      low_citedness_signal: 0.33,
      evidence: [
        { kind: "work", work_id: "W4361447890", url: null, retrieved_at: null, quote: null },
      ],
      status: "not_confirmed",
      not_confirmed_reason:
        "Zoom pass found 47 works bridging these communities since 2024; coarse-pass sparsity was a sampling artifact.",
    },
    {
      whitespace_id: "ws-0013",
      run_id: "run-0001",
      kind: "thin_cell",
      description:
        "Thin coverage cell: developmental trajectories (curriculum effects) of agent memory — almost all work assumes a fixed adult-like store.",
      community_a: null,
      community_b: null,
      topic_id: "T12208",
      sparsity_score: 0.64,
      low_citedness_signal: 0.6,
      evidence: [],
      status: "candidate",
      not_confirmed_reason: null,
    },
  ],
};

/**
 * Gap Report JSON keyed by zoom run id (GET /api/runs/{id}/report).
 *
 * NOTE FOR THE API INTEGRATOR — the real report JSON must include:
 *   communities: [{id, label, size, top_topics, works: [...work ids]}]
 *   community_edges: [{source, target, weight}]  (weight = inter-community density, 0..1)
 *   works: {W…: {work_id, title, year, doi}}      (citation resolution table)
 * The Explorer builds its community-map lens from these (no dedicated graph
 * endpoint in v1) and the Report view resolves [W…] citation chips from `works`.
 */
export const mockReports: Record<string, GapReport> = {
  "run-0004": {
    run_id: "run-0004",
    parent_run_id: "run-0001",
    field_name: "memory for AI agents",
    generated_at: "2026-08-19T11:12:44Z",
    gaps: [
      {
        gap_id: "gap-0001",
        whitespace_id: "ws-0004",
        zoom_run_id: "run-0004",
        kinds: ["structural", "narrative"],
        statement:
          "No published agent memory architecture implements interference-based forgetting (proactive/retroactive) as a first-class mechanism; existing systems use recency- or capacity-based eviction only.",
        evidence: [
          { kind: "work", work_id: "W4390112345", url: null, retrieved_at: null, quote: null },
          { kind: "work", work_id: "W4377651209", url: null, retrieved_at: null, quote: null },
          {
            kind: "work",
            work_id: "W4322109876",
            url: null,
            retrieved_at: null,
            quote: "future work should examine psychologically-motivated forgetting policies",
          },
          {
            kind: "web",
            work_id: null,
            url: "https://arxiv.org/abs/2405.11111",
            retrieved_at: "2026-08-19T10:41:02Z",
            quote: null,
          },
        ],
        scores: {
          sparsity: 0.91,
          narrative_demand: 0.83,
          recency: 0.66,
          low_citedness: 0.62,
        },
        composite_score: 0.82,
      },
      {
        gap_id: "gap-0002",
        whitespace_id: "ws-0004",
        zoom_run_id: "run-0004",
        kinds: ["structural", "temporal"],
        statement:
          "Work on schema-consistent memory integration (fast cortical learning of schema-fitting items) has no computational counterpart in agent memory systems, despite a burst of relevant cognitive results in 2023–2025.",
        evidence: [
          { kind: "work", work_id: "W4315820077", url: null, retrieved_at: null, quote: null },
          { kind: "work", work_id: "W4388441123", url: null, retrieved_at: null, quote: null },
          { kind: "work", work_id: "W4402998811", url: null, retrieved_at: null, quote: null },
        ],
        scores: {
          sparsity: 0.78,
          narrative_demand: 0.51,
          recency: 0.9,
          low_citedness: 0.44,
        },
        composite_score: 0.71,
      },
      {
        gap_id: "gap-0003",
        whitespace_id: "ws-0004",
        zoom_run_id: "run-0004",
        kinds: ["narrative"],
        statement:
          "Multiple 2025 surveys call for unified evaluation of forgetting quality (what should be forgotten) rather than retention quantity, but no benchmark or metric exists.",
        evidence: [
          {
            kind: "work",
            work_id: "W4409120334",
            url: null,
            retrieved_at: null,
            quote: "the community lacks any principled measure of beneficial forgetting",
          },
          {
            kind: "web",
            work_id: null,
            url: "https://openreview.net/forum?id=agmem2025",
            retrieved_at: "2026-08-19T10:55:18Z",
            quote: null,
          },
        ],
        scores: {
          sparsity: 0.42,
          narrative_demand: 0.88,
          recency: 0.74,
          low_citedness: 0.39,
        },
        composite_score: 0.63,
      },
    ],
    examined_not_confirmed: [
      {
        whitespace_id: "ws-0009",
        run_id: "run-0001",
        kind: "bridge",
        description:
          "Apparent disconnect between vector-store retrieval papers and hippocampal indexing theory.",
        community_a: 2,
        community_b: 3,
        topic_id: null,
        sparsity_score: 0.58,
        low_citedness_signal: 0.33,
        evidence: [
          { kind: "work", work_id: "W4361447890", url: null, retrieved_at: null, quote: null },
        ],
        status: "not_confirmed",
        not_confirmed_reason:
          "Zoom pass found 47 works bridging these communities since 2024; coarse-pass sparsity was a sampling artifact.",
      },
    ],
    works: {
      W4390112345: {
        work_id: "W4390112345",
        title: "MemGPT-2: Towards Operating-System Memory for LLM Agents",
        year: 2025,
        doi: "10.48550/arXiv.2501.00001",
      },
      W4377651209: {
        work_id: "W4377651209",
        title: "Proactive Interference in Human Episodic Memory: A Meta-Analysis",
        year: 2023,
        doi: "10.1037/rev0000411",
      },
      W4322109876: {
        work_id: "W4322109876",
        title: "A Survey of Memory Mechanisms for Autonomous Agents",
        year: 2024,
        doi: "10.48550/arXiv.2404.13501",
      },
      W4315820077: {
        work_id: "W4315820077",
        title: "Schema-Dependent Consolidation During Sleep: Evidence from Targeted Reactivation",
        year: 2023,
        doi: "10.1016/j.neuron.2023.02.014",
      },
      W4388441123: {
        work_id: "W4388441123",
        title: "Fast Cortical Learning of Schema-Consistent Information",
        year: 2024,
        doi: "10.1126/science.adk9963",
      },
      W4402998811: {
        work_id: "W4402998811",
        title: "Complementary Learning Systems at Thirty",
        year: 2025,
        doi: "10.1146/annurev-psych-2025-0301",
      },
      W4409120334: {
        work_id: "W4409120334",
        title: "Benchmarking Long-Horizon Memory in Tool-Using Agents",
        year: 2025,
        doi: "10.48550/arXiv.2503.09912",
      },
      W4398220145: {
        work_id: "W4398220145",
        title: "Machine Unlearning for Conversational Stores",
        year: 2024,
        doi: "10.1145/3658644.3690222",
      },
      W4370015566: {
        work_id: "W4370015566",
        title: "The Right to be Forgotten by Your Assistant",
        year: 2023,
        doi: "10.1145/3593013.3594067",
      },
      W4361447890: {
        work_id: "W4361447890",
        title: "Hippocampal Indexing Theory Revisited",
        year: 2023,
        doi: "10.1002/hipo.23517",
      },
      W4402113344: {
        work_id: "W4402113344",
        title: "Retrieval-Augmented Generation: A Systematic Review",
        year: 2025,
        doi: "10.48550/arXiv.2502.04477",
      },
      W4391556677: {
        work_id: "W4391556677",
        title: "Sleep Replay Consolidates Reward Memories",
        year: 2024,
        doi: "10.1038/s41593-024-01611-9",
      },
    },
    communities: [
      {
        id: 0,
        label: "Agent memory architectures",
        size: 1420,
        top_topics: ["LLM agents", "memory-augmented transformers", "cognitive architectures"],
        works: ["W4390112345", "W4322109876"],
      },
      {
        id: 1,
        label: "Episodic memory in LLM agents",
        size: 610,
        top_topics: ["episodic buffers", "experience replay", "long-context"],
        works: ["W4390112345", "W4409120334"],
      },
      {
        id: 2,
        label: "Retrieval-augmented generation",
        size: 2310,
        top_topics: ["vector stores", "RAG", "dense retrieval"],
        works: ["W4402113344", "W4361447890"],
      },
      {
        id: 3,
        label: "Memory consolidation neuroscience",
        size: 1870,
        top_topics: ["systems consolidation", "sleep replay", "schema integration"],
        works: ["W4315820077", "W4388441123", "W4402998811", "W4391556677"],
      },
      {
        id: 4,
        label: "Forgetting & interference (cog. psych.)",
        size: 940,
        top_topics: ["proactive interference", "directed forgetting", "decay theory"],
        works: ["W4377651209"],
      },
      {
        id: 5,
        label: "Machine unlearning & memory privacy",
        size: 380,
        top_topics: ["machine unlearning", "GDPR erasure", "membership inference"],
        works: ["W4398220145", "W4370015566"],
      },
    ],
    community_edges: [
      { source: 0, target: 1, weight: 0.82 },
      { source: 0, target: 2, weight: 0.67 },
      { source: 1, target: 2, weight: 0.58 },
      { source: 2, target: 3, weight: 0.31 },
      { source: 3, target: 4, weight: 0.49 },
      { source: 0, target: 5, weight: 0.18 },
      { source: 2, target: 5, weight: 0.22 },
      { source: 0, target: 3, weight: 0.09 },
    ],
  },
};

/** Ideonomy Expansions keyed by gap id (GET /api/gaps/{id}/expansions). */
export const mockExpansions: Record<string, IdeonomyExpansion[]> = {
  "gap-0001": [
    {
      gap_id: "gap-0001",
      attempt: 1,
      tuple: {
        operators: ["inversion", "transposition", "hybridization"],
        organon: "organon of processes",
        dimension_prompts: ["temporal scale", "failure modes"],
        seed: "run-0004:gap-0001:1",
      },
      ideas: [
        {
          text: "Invert eviction: treat interference itself as the retention signal — memories that interfere most with new task traces are candidates for consolidation into a slower semantic store rather than deletion.",
          operators: ["inversion"],
          organon_position: "process reversal",
          nearest_work_id: "W4377651209",
        },
        {
          text: "Transpose targeted memory reactivation from sleep research into agent downtime: replay high-interference episodes against the semantic store during idle cycles and measure downstream task retention.",
          operators: ["transposition"],
          organon_position: "cross-domain mapping",
          nearest_work_id: "W4391556677",
        },
        {
          text: "Hybridize capacity-based eviction with a proactive-interference predictor: a small model scores each incoming trace for expected interference and routes it to buffer, store, or discard.",
          operators: ["hybridization"],
          organon_position: "mechanism composition",
          nearest_work_id: "W4390112345",
        },
      ],
    },
  ],
};

/** GET /api/credentials fixture — mutated by mock set/clear in endpoints.ts. */
export const mockCredentials: CredentialStatus[] = [
  {
    name: "openalex_api_key",
    env_var: "NOOSPHERE_OPENALEX_KEY",
    set: true,
    source: "keychain",
    hint: "…7f2a",
  },
  {
    name: "s2_api_key",
    env_var: "NOOSPHERE_S2_KEY",
    set: false,
    source: null,
    hint: null,
  },
  {
    name: "ncbi_api_key",
    env_var: "NOOSPHERE_NCBI_KEY",
    set: false,
    source: null,
    hint: null,
  },
  {
    name: "crossref_mailto",
    env_var: "NOOSPHERE_CROSSREF_MAILTO",
    set: true,
    source: "env",
    hint: "you@example.com",
  },
  {
    name: "aws_access_key_id",
    env_var: "AWS_ACCESS_KEY_ID",
    set: false,
    source: null,
    hint: null,
  },
  {
    name: "aws_secret_access_key",
    env_var: "AWS_SECRET_ACCESS_KEY",
    set: false,
    source: null,
    hint: null,
  },
  {
    name: "aws_session_token",
    env_var: "AWS_SESSION_TOKEN",
    set: false,
    source: null,
    hint: null,
  },
];

/** GET /api/spend fixture — mutated by the mock SSE ticker in endpoints.ts. */
export const mockSpend: SpendSummary = {
  models: {
    "anthropic.claude-opus-5": { input: 1_310_000, output: 130_400, est_usd: 9.81 },
    "anthropic.claude-haiku-4-5-20251001-v1:0": { input: 1_820_000, output: 168_000, est_usd: 2.66 },
  },
  total: { input: 3_130_000, output: 298_400, est_usd: 12.47 },
};

/** GET /api/runs/{id}/report.md fixture (Markdown export). */
export const mockReportMarkdown = `# Gap Report — memory for AI agents

Zoom run \`run-0004\` (whitespace ws-0004), generated 2026-08-19.

## Gap 1 (composite 0.82) — structural, narrative

No published agent memory architecture implements interference-based forgetting
(proactive/retroactive) as a first-class mechanism [W4390112345] [W4377651209]
[W4322109876] (https://arxiv.org/abs/2405.11111, retrieved 2026-08-19).

## Gap 2 (composite 0.71) — structural, temporal

Schema-consistent memory integration has no computational counterpart in agent
memory systems [W4315820077] [W4388441123] [W4402998811].

## Gap 3 (composite 0.63) — narrative

No benchmark or metric exists for forgetting *quality* [W4409120334]
(https://openreview.net/forum?id=agmem2025, retrieved 2026-08-19).

## Examined, not confirmed

- ws-0009 — vector-store retrieval x hippocampal indexing: 47 bridging works
  found since 2024; coarse-pass sparsity was a sampling artifact.

## Ideonomy Expansion (SPECULATIVE)

> This section is generated speculation, segregated per the grounding rule.
> Each idea cites its nearest existing work.

- Invert eviction: interference as retention signal [W4377651209]
- Transpose targeted memory reactivation into agent downtime [W4391556677]
- Hybridize capacity eviction with an interference predictor [W4390112345]
`;

/** Small delay so mock mode exercises loading states. */
export function mockDelay<T>(value: T, ms = 250): Promise<T> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(structuredClone(value)), ms);
  });
}
