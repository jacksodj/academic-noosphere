/**
 * Fixture data served when VITE_MOCK=1, so the SPA runs standalone before the
 * wave-2 API exists. Shapes mirror src/types.ts exactly.
 */

import type { Run, Settings } from "./types";

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
];

export const mockSettings: Settings = {
  aws_region: "us-east-1",
  gateway_url: "https://example-gateway.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
  web_search_enabled: true,
  opus_model: "anthropic.claude-opus-5",
  haiku_model: "anthropic.claude-haiku-4-5",
  coarse_corpus_target: 8000,
  relevance_threshold: 0.35,
  ranking_weights: {
    sparsity: 1.0,
    narrative_demand: 1.0,
    recency: 0.5,
    low_citedness: 0.5,
  },
};

/** Small delay so mock mode exercises loading states. */
export function mockDelay<T>(value: T, ms = 250): Promise<T> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(structuredClone(value)), ms);
  });
}
