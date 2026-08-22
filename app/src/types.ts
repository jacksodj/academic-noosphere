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
  onboarded: boolean;
  aws_region: string;
  gateway_url: string | null;
  web_search_enabled: boolean;
  opus_model: string;
  haiku_model: string;
  coarse_corpus_target: number;
  relevance_threshold: number;
  ranking_weights: Record<string, number>;
}

/**
 * GET /api/credentials item — presence/source of one Keychain credential.
 * Values are write-only; `hint` is masked to the last 4 chars for secrets.
 */
export interface CredentialStatus {
  name: string;
  env_var: string;
  set: boolean;
  source: "env" | "keychain" | null;
  hint: string | null;
}

/** POST /api/aws/check result — STS identity probe. */
export interface AwsCheckResult {
  ok: boolean;
  profile: string | null;
  account?: string;
  arn?: string;
  error?: string;
}

/** POST /api/surveys request body (endpoint lands in wave 2). */
export interface NewSurveyRequest {
  field_name: string;
  seed_queries: string[];
}

/**
 * Citation-resolution entry in the report JSON `works` table: minimal metadata
 * for every work id cited anywhere in the report (evidence, ideas, communities).
 */
export interface WorkRef {
  work_id: string;
  title: string | null;
  year: number | null;
  doi: string | null;
}

/**
 * Community super-node summary in the report JSON — the Explorer's default
 * community-map lens is built from this (no dedicated graph endpoint in v1).
 */
export interface ReportCommunity {
  id: number;
  label: string | null;
  size: number; // member work count
  top_topics: string[];
  works: string[]; // member work ids (top-N by relevance is fine)
}

/** Inter-community edge, weight = normalized citation/coupling density (0..1). */
export interface CommunityEdge {
  source: number;
  target: number;
  weight: number;
}

/**
 * GET /api/runs/{zoom_run_id}/report — the interactive Gap Report payload.
 * `communities` + `community_edges` also feed the Graph Explorer lens.
 */
export interface GapReport {
  run_id: string;
  parent_run_id: string | null;
  field_name: string;
  generated_at: string;
  gaps: Gap[];
  examined_not_confirmed: WhitespaceCandidate[];
  works: Record<string, WorkRef>;
  communities: ReportCommunity[];
  community_edges: CommunityEdge[];
}

/** Per-model token counts + list-price estimate (core: SpendMeter.totals()). */
export interface SpendUsage {
  input: number;
  output: number;
  est_usd: number;
}

/** GET /api/spend — running LLM spend estimate (live meter, no auto-stop). */
export interface SpendSummary {
  models: Record<string, SpendUsage>;
  total: SpendUsage;
  note?: string;
}

/** POST /api/whitespace/{id}/zoom response: the created zoom Run + updated candidate. */
export interface ZoomResponse {
  run: Run;
  candidate: WhitespaceCandidate;
}
