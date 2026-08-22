/**
 * Typed endpoint helpers over the base client in ./api.ts.
 * Under VITE_MOCK=1 each helper serves fixture data from ./mock.ts instead of
 * hitting the core, so the SPA runs standalone (VITE_MOCK=1 exercises every
 * endpoint the wave-2 API exposes).
 */

import { apiConfig, del, get, getText, post, put, subscribe } from "./api";
import {
  mockCredentials,
  mockDelay,
  mockExpansions,
  mockReportMarkdown,
  mockReports,
  mockRuns,
  mockSettings,
  mockSpend,
  mockWhitespace,
} from "./mock";
import type {
  AwsCheckResult,
  CredentialStatus,
  Gap,
  RunActivity,
  RunProgress,
  GapReport,
  IdeonomyExpansion,
  NewSurveyRequest,
  Run,
  Settings,
  SpendSummary,
  WhitespaceCandidate,
  ZoomResponse,
} from "./types";

export function listRuns(): Promise<Run[]> {
  if (apiConfig.mock) return mockDelay(mockRuns);
  return get<Run[]>("/api/runs");
}

/**
 * Live event feed (SSE /api/events). Fires for progress, spend, completion
 * and requeue events; mock mode ticks a fake survey forward instead.
 */
export function subscribeEvents(onEvent: (event: Record<string, unknown>) => void): () => void {
  if (apiConfig.mock) {
    let step = 0;
    const stages = ["seeds", "expand", "relevance", "persist"];
    const timer = setInterval(() => {
      step = (step + 1) % 5;
      const runId = mockRuns.find((r) => r.status === "running")?.run_id ?? "run-0002";
      onEvent({
        type: "progress",
        run_id: runId,
        progress: {
          stages,
          done: stages.slice(0, step),
          current: stages[step] ?? null,
          counts: { seeds: 40, candidates: 40 + step * 900, kept: step > 2 ? 3100 : 0 },
          error: null,
        },
      });
      onEvent({
        type: "activity",
        run_id: runId,
        seq: 100 + step,
        ts: new Date().toISOString(),
        message: `Citation expansion: ${25 + step * 5}/48 seeds → ${1914 + step * 400} neighbors so far`,
      });
      onEvent({
        type: "stage_progress",
        run_id: runId,
        stage: "relevance",
        step: "embed",
        done: 2000 + step * 4000,
        total: 21293,
        eta_s: Math.max(0, 1500 - step * 300),
      });
    }, 4000);
    return () => clearInterval(timer);
  }
  return subscribe("/api/events", (ev) => {
    try {
      const data: unknown = JSON.parse(ev.data);
      if (data && typeof data === "object") onEvent(data as Record<string, unknown>);
    } catch {
      // non-JSON event; ignore
    }
  });
}

/** Persisted activity lines for a run, oldest-first (live tail via SSE). */
export function getRunActivity(
  runId: string,
): Promise<{ run_id: string; activities: RunActivity[] }> {
  if (apiConfig.mock) {
    const base = Date.now() - 90_000;
    return mockDelay({
      run_id: runId,
      activities: [
        "Survey started (coarse pass)",
        "OpenAlex search 'Agentic Memory Architecture' → 25 works",
        "OpenAlex search 'episodic memory consolidation' → 25 works",
        "Web Search discovery 'Agentic Memory Architecture' → 6 resolved works",
        "Seed stage complete: 48 unique works",
        "Citation expansion: walking references/citations of 48 seeds",
        "Citation expansion: 25/48 seeds → 1,914 neighbors so far",
      ].map((message, i) => ({
        run_id: runId,
        seq: i + 1,
        ts: new Date(base + i * 12_000).toISOString(),
        message,
      })),
    });
  }
  return get(`/api/runs/${runId}/activity`);
}

/** Stage progress for one run (poll fallback / initial fill). */
export function getRunProgress(
  runId: string,
): Promise<{ run_id: string; job_status: string; progress: RunProgress }> {
  if (apiConfig.mock) {
    return mockDelay({
      run_id: runId,
      job_status: "running",
      progress: {
        stages: ["seeds", "expand", "relevance", "persist"],
        done: ["seeds"],
        current: "expand",
        counts: { seeds: 40, candidates: 1240, kept: 0 },
        error: null,
      },
    });
  }
  return get(`/api/runs/${runId}/progress`);
}

/** Requeue a failed run's job; it resumes from its last checkpoint. */
export function retryRun(runId: string): Promise<Run> {
  if (apiConfig.mock) {
    const run = mockRuns.find((r) => r.run_id === runId);
    if (!run) throw new Error(`unknown run ${runId}`);
    run.status = "pending";
    return mockDelay({ ...run });
  }
  return post<Run>(`/api/runs/${runId}/retry`, {});
}

export function createSurvey(req: NewSurveyRequest): Promise<Run> {
  if (apiConfig.mock) {
    const run: Run = {
      run_id: `run-mock-${Date.now()}`,
      field_name: req.field_name,
      phase: "coarse",
      parent_run_id: null,
      whitespace_id: null,
      query_manifest_hash: null,
      status: "pending",
      started_at: null,
      finished_at: null,
    };
    mockRuns.unshift(run);
    return mockDelay(run);
  }
  return post<Run>("/api/surveys", req);
}

// -- credentials (Keychain-backed; values are write-only) --------------------

export function listCredentials(): Promise<CredentialStatus[]> {
  if (apiConfig.mock) return mockDelay(mockCredentials.map((c) => ({ ...c })));
  return get<CredentialStatus[]>("/api/credentials");
}

function mockSetCredential(name: string, set: boolean, hint: string | null): CredentialStatus {
  const cred = mockCredentials.find((c) => c.name === name);
  if (!cred) throw new Error(`unknown credential ${name}`);
  Object.assign(cred, { set, source: set ? "keychain" : null, hint });
  return { ...cred };
}

export function setCredential(name: string, value: string): Promise<CredentialStatus> {
  if (apiConfig.mock) {
    const hint = name === "crossref_mailto" ? value : `…${value.slice(-4)}`;
    return mockDelay(mockSetCredential(name, true, hint));
  }
  return put<CredentialStatus>(`/api/credentials/${name}`, { value });
}

export function clearCredential(name: string): Promise<CredentialStatus> {
  if (apiConfig.mock) return mockDelay(mockSetCredential(name, false, null));
  return del<CredentialStatus>(`/api/credentials/${name}`);
}

export function checkAws(): Promise<AwsCheckResult> {
  if (apiConfig.mock) {
    return mockDelay({
      ok: true,
      profile: "research",
      account: "123456789012",
      arn: "arn:aws:sts::123456789012:assumed-role/research/you",
    });
  }
  return post<AwsCheckResult>("/api/aws/check", {});
}

export function getSettings(): Promise<Settings> {
  if (apiConfig.mock) return mockDelay(mockSettings);
  return get<Settings>("/api/settings");
}

export function saveSettings(settings: Settings): Promise<Settings> {
  if (apiConfig.mock) {
    Object.assign(mockSettings, settings);
    return mockDelay(mockSettings);
  }
  return put<Settings>("/api/settings", settings);
}

/**
 * Queue whitespace re-detection over a coarse run (adaptive community
 * resolution; zoomed candidates preserved). Async: listen for the
 * "whitespace_updated" SSE event, then re-fetch getWhitespace.
 */
export function redetectWhitespace(runId: string): Promise<{ job_id: string; run_id: string }> {
  if (apiConfig.mock) return mockDelay({ job_id: "job-mock-redetect", run_id: runId });
  return post(`/api/runs/${encodeURIComponent(runId)}/redetect`, {});
}

/** Whitespace Candidates surfaced by a coarse run. */
export function getWhitespace(runId: string): Promise<WhitespaceCandidate[]> {
  if (apiConfig.mock) return mockDelay(mockWhitespace[runId] ?? []);
  return get<WhitespaceCandidate[]>(`/api/runs/${encodeURIComponent(runId)}/whitespace`);
}

/** Start a bounded zoom pass to confirm/refute a Whitespace Candidate. */
export function zoomWhitespace(whitespaceId: string, runId: string): Promise<ZoomResponse> {
  if (apiConfig.mock) {
    const candidates = mockWhitespace[runId] ?? [];
    const candidate = candidates.find((c) => c.whitespace_id === whitespaceId);
    if (!candidate) return Promise.reject(new Error(`unknown whitespace ${whitespaceId}`));
    candidate.status = "zooming";
    const run: Run = {
      run_id: `run-zoom-${Date.now()}`,
      field_name: mockRuns.find((r) => r.run_id === runId)?.field_name ?? "unknown",
      phase: "zoom",
      parent_run_id: runId,
      whitespace_id: whitespaceId,
      query_manifest_hash: null,
      status: "running",
      started_at: new Date().toISOString(),
      finished_at: null,
    };
    mockRuns.unshift(run);
    return mockDelay({ run, candidate });
  }
  return post<ZoomResponse>(`/api/whitespace/${encodeURIComponent(whitespaceId)}/zoom`, {
    run_id: runId,
  });
}

/** Confirmed gaps produced by a zoom run. */
export function listGaps(zoomRunId: string): Promise<Gap[]> {
  if (apiConfig.mock) return mockDelay(mockReports[zoomRunId]?.gaps ?? []);
  return get<Gap[]>(`/api/gaps?zoom_run_id=${encodeURIComponent(zoomRunId)}`);
}

/** Existing Ideonomy Expansions for a gap (may be empty; on-demand generation). */
export function getExpansions(gapId: string): Promise<IdeonomyExpansion[]> {
  if (apiConfig.mock) return mockDelay(mockExpansions[gapId] ?? []);
  return get<IdeonomyExpansion[]>(`/api/gaps/${encodeURIComponent(gapId)}/expansions`);
}

/**
 * Queue a new Ideonomy Expansion (Opus; attempt N+1 = Re-roll).
 *
 * The expansion is generated ASYNCHRONOUSLY by the job queue — the 202
 * response is only an acknowledgment. Listen for the "expansion_ready" /
 * "expansion_failed" SSE events (or re-poll getExpansions) for the result.
 */
export function expandGap(gapId: string): Promise<{ gap_id: string; attempt: number }> {
  if (apiConfig.mock) {
    const existing = mockExpansions[gapId] ?? (mockExpansions[gapId] = []);
    const attempt = existing.length + 1;
    // Simulate the async queue: the expansion "arrives" a few seconds later.
    setTimeout(() => {
      existing.push({
        gap_id: gapId,
        attempt,
        tuple: {
          operators: ["analogy", "extremization"],
          organon: "organon of relations",
          dimension_prompts: ["scale", "actors"],
          seed: `mock:${gapId}:${attempt}`,
        },
        ideas: [
          {
            text: `Mock idea (attempt ${attempt}): push the gap's mechanism to an extreme regime and study where it breaks.`,
            operators: ["extremization"],
            organon_position: "limit analysis",
            nearest_work_id: "W4322109876",
          },
          {
            text: `Mock idea (attempt ${attempt}): find the closest analogous mechanism in a neighboring community and port its formalism.`,
            operators: ["analogy"],
            organon_position: "relational mapping",
            nearest_work_id: "W4402998811",
          },
        ],
      });
    }, 4000);
    return mockDelay({ gap_id: gapId, attempt }, 300);
  }
  return post<{ gap_id: string; attempt: number }>(
    `/api/gaps/${encodeURIComponent(gapId)}/expand`,
    {},
  );
}

/** Full interactive Gap Report JSON for a completed zoom run. */
export function getReport(runId: string): Promise<GapReport> {
  if (apiConfig.mock) {
    const report = mockReports[runId];
    if (!report) return Promise.reject(new Error(`no report for ${runId}`));
    return mockDelay(report);
  }
  return get<GapReport>(`/api/runs/${encodeURIComponent(runId)}/report`);
}

/** Markdown export of a Gap Report. */
export function getReportMarkdown(runId: string): Promise<string> {
  if (apiConfig.mock) return mockDelay(mockReportMarkdown, 150);
  return getText(`/api/runs/${encodeURIComponent(runId)}/report.md`);
}

/** Current LLM spend estimate. */
export function getSpend(): Promise<SpendSummary> {
  if (apiConfig.mock) return mockDelay(mockSpend, 100);
  return get<SpendSummary>("/api/spend");
}

/**
 * Live spend meter: initial fetch + SSE (/api/events) + slow poll fallback.
 * Mock mode ticks on an interval instead. Returns an unsubscribe function.
 */
export function subscribeSpend(onSpend: (spend: SpendSummary) => void): () => void {
  let closed = false;
  const push = (s: SpendSummary) => {
    if (!closed) onSpend(s);
  };
  getSpend().then(push).catch(() => undefined);

  if (apiConfig.mock) {
    const timer = setInterval(() => {
      mockSpend.total.est_usd += 0.03;
      mockSpend.models["anthropic.claude-haiku-4-5-20251001-v1:0"].est_usd += 0.03;
      push(structuredClone(mockSpend));
    }, 5000);
    return () => {
      closed = true;
      clearInterval(timer);
    };
  }

  // SSE: any event that carries spend totals updates the meter; other event
  // types are ignored here (job progress etc. is consumed elsewhere).
  const unsubscribe = subscribe("/api/events", (ev) => {
    try {
      const data: unknown = JSON.parse(ev.data);
      if (data && typeof data === "object" && "total" in data && "models" in data) {
        push(data as SpendSummary);
      } else if (
        data &&
        typeof data === "object" &&
        "spend" in data &&
        (data as { spend: unknown }).spend &&
        typeof (data as { spend: unknown }).spend === "object"
      ) {
        push((data as { spend: SpendSummary }).spend);
      }
    } catch {
      // non-JSON event; ignore
    }
  });
  const poll = setInterval(() => {
    getSpend().then(push).catch(() => undefined);
  }, 30000);
  return () => {
    closed = true;
    unsubscribe();
    clearInterval(poll);
  };
}
