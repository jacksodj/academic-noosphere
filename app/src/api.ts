/**
 * Typed client for the noosphere core API (localhost FastAPI, per-launch token).
 *
 * Connection resolution order:
 *   1. URL query params `?port=<n>&token=<t>` (dev browser session against a
 *      hand-started core).
 *   2. `window.__NOOSPHERE__` (the Tauri shell injects the core's stdout
 *      handshake via an initialization script — see app/src-tauri/src/lib.rs).
 *   3. `VITE_API_PORT` / `VITE_API_TOKEN` env vars (dev fallback).
 *
 * Mock mode: `VITE_MOCK=1` makes callers in ./mock.ts serve fixture data so the
 * SPA runs standalone before the wave-2 API exists.
 *
 * NOTE FOR THE INTEGRATOR (SSE auth): EventSource cannot set request headers,
 * so `subscribe()` passes the bearer token as a `?token=` query parameter.
 * The core's auth middleware must accept `?token=` (in addition to the
 * Authorization header) on SSE endpoints.
 */

export interface ApiConfig {
  baseUrl: string;
  token: string;
  mock: boolean;
}

/** Handshake injected by the Tauri shell before the SPA loads. */
declare global {
  interface Window {
    __NOOSPHERE__?: { port: number; token: string };
  }
}

function resolveConfig(): ApiConfig {
  const params = new URLSearchParams(window.location.search);
  const injected = window.__NOOSPHERE__;
  const port: string =
    params.get("port") ??
    (injected ? String(injected.port) : undefined) ??
    (import.meta.env.VITE_API_PORT as string | undefined) ??
    "";
  const token: string =
    params.get("token") ??
    injected?.token ??
    (import.meta.env.VITE_API_TOKEN as string | undefined) ??
    "";
  const mock = import.meta.env.VITE_MOCK === "1";
  return {
    baseUrl: port ? `http://127.0.0.1:${port}` : "",
    token,
    mock,
  };
}

export const apiConfig: ApiConfig = resolveConfig();

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  if (!apiConfig.baseUrl) {
    throw new ApiError(
      0,
      "No core API configured: open with ?port=&token= or set VITE_API_PORT/VITE_API_TOKEN (or VITE_MOCK=1).",
    );
  }
  const res = await fetch(`${apiConfig.baseUrl}${path}`, {
    method,
    headers: {
      authorization: `Bearer ${apiConfig.token}`,
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const parsed: unknown = await res.json();
      if (parsed && typeof parsed === "object" && "detail" in parsed) {
        detail = String((parsed as { detail: unknown }).detail);
      }
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new ApiError(res.status, `${method} ${path}: ${detail}`);
  }
  return (await res.json()) as T;
}

export function get<T>(path: string): Promise<T> {
  return request<T>("GET", path);
}

export function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>("POST", path, body);
}

export function put<T>(path: string, body: unknown): Promise<T> {
  return request<T>("PUT", path, body);
}

export function del<T>(path: string): Promise<T> {
  return request<T>("DELETE", path);
}

/** GET a text/plain or text/markdown endpoint (e.g. /api/runs/{id}/report.md). */
export async function getText(path: string): Promise<string> {
  if (!apiConfig.baseUrl) {
    throw new ApiError(
      0,
      "No core API configured: open with ?port=&token= or set VITE_API_PORT/VITE_API_TOKEN (or VITE_MOCK=1).",
    );
  }
  const res = await fetch(`${apiConfig.baseUrl}${path}`, {
    headers: { authorization: `Bearer ${apiConfig.token}` },
  });
  if (!res.ok) {
    throw new ApiError(res.status, `GET ${path}: ${res.statusText}`);
  }
  return res.text();
}

/**
 * Subscribe to a server-sent-events endpoint. Returns an unsubscribe function.
 *
 * EventSource cannot set an Authorization header, so the token rides in the
 * `?token=` query param — see the integrator note at the top of this file.
 */
export function subscribe(
  path: string,
  onEvent: (event: MessageEvent<string>) => void,
  onError?: (event: Event) => void,
): () => void {
  if (!apiConfig.baseUrl) {
    // Unconfigured (no ?port= and no VITE_API_PORT): nothing to subscribe to.
    return () => undefined;
  }
  const url = new URL(path, apiConfig.baseUrl);
  url.searchParams.set("token", apiConfig.token);
  const source = new EventSource(url);
  source.onmessage = onEvent;
  if (onError) source.onerror = onError;
  return () => source.close();
}
