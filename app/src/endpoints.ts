/**
 * Typed endpoint helpers over the base client in ./api.ts.
 * Under VITE_MOCK=1 each helper serves fixture data from ./mock.ts instead of
 * hitting the core, so the SPA runs standalone today (endpoints land in wave 2).
 */

import { apiConfig, get, post, put } from "./api";
import { mockDelay, mockRuns, mockSettings } from "./mock";
import type { NewSurveyRequest, Run, Settings } from "./types";

export function listRuns(): Promise<Run[]> {
  if (apiConfig.mock) return mockDelay(mockRuns);
  return get<Run[]>("/api/runs");
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
