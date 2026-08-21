/**
 * TS mirrors of the core domain models (src/noosphere/models.py).
 * Field names are snake_case as-is — the API serves pydantic JSON verbatim.
 * Datetimes arrive as ISO-8601 strings.
 */

export type SourceApi =
  | "openalex"
  | "s2"
  | "crossref"
  | "arxiv"
  | "pubmed"
  | "websearch";

export interface Provenance {
  source_api: SourceApi;
  source_id: string;
  retrieved_at: string;
}

export type RunPhase = "coarse" | "zoom";

export type RunStatus =
  | "pending"
  | "running"
  | "paused"
  | "completed"
  | "failed";

export interface Run {
  run_id: string;
  field_name: string;
  phase: RunPhase;
  parent_run_id: string | null;
  whitespace_id: string | null;
  query_manifest_hash: string | null;
  status: RunStatus;
  started_at: string | null;
  finished_at: string | null;
}

export type GapKind = "structural" | "narrative" | "temporal";

export interface EvidenceItem {
  kind: "work" | "web";
  work_id: string | null;
  url: string | null;
  retrieved_at: string | null;
  quote: string | null;
}

export type WhitespaceStatus =
  | "candidate"
  | "zooming"
  | "confirmed"
  | "not_confirmed";

export interface WhitespaceCandidate {
  whitespace_id: string;
  run_id: string;
  kind: "bridge" | "thin_cell";
  description: string;
  community_a: number | null;
  community_b: number | null;
  topic_id: string | null;
  sparsity_score: number;
  low_citedness_signal: number;
  evidence: EvidenceItem[];
  status: WhitespaceStatus;
  not_confirmed_reason: string | null;
}

export interface Gap {
  gap_id: string;
  whitespace_id: string;
  zoom_run_id: string;
  kinds: GapKind[];
  statement: string;
  evidence: EvidenceItem[];
  scores: Record<string, number>;
  composite_score: number;
}

export interface IdeonomyTuple {
  operators: string[];
  organon: string;
  dimension_prompts: string[];
  seed: string;
}

export interface IdeonomyIdea {
  text: string;
  operators: string[];
  organon_position: string;
  nearest_work_id: string;
}

export interface IdeonomyExpansion {
  gap_id: string;
  attempt: number;
  tuple: IdeonomyTuple;
  ideas: IdeonomyIdea[];
}

/** Mirror of noosphere.config.Settings, as served by /api/settings. */
export interface Settings {
  aws_region: string;
  gateway_url: string | null;
  web_search_enabled: boolean;
  opus_model: string;
  haiku_model: string;
  coarse_corpus_target: number;
  relevance_threshold: number;
  ranking_weights: Record<string, number>;
}

/** POST /api/surveys request body (endpoint lands in wave 2). */
export interface NewSurveyRequest {
  field_name: string;
  seed_queries: string[];
}
