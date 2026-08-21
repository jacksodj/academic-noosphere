/**
 * Typed client for the noosphere core API (localhost FastAPI, per-launch token).
 *
 * Connection resolution order:
 *   1. URL query params `?port=<n>&token=<t>` (Tauri shell injects these from
 *      the core's stdout handshake line).
 *   2. `VITE_API_PORT` / `VITE_API_TOKEN` env vars (dev: `npm run dev` against
 *      a hand-started core).
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

function resolveConfig(): ApiConfig {
  const params = new URLSearchParams(window.location.search);
  const port: string =
    params.get("port") ?? (import.meta.env.VITE_API_PORT as string | undefined) ?? "";
  const token: string =
    params.get("token") ?? (import.meta.env.VITE_API_TOKEN as string | undefined) ?? "";
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

async function request<T>(method: "GET" | "POST" | "PUT", path: string, body?: unknown): Promise<T> {
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
  const url = new URL(path, apiConfig.baseUrl);
  url.searchParams.set("token", apiConfig.token);
  const source = new EventSource(url);
  source.onmessage = onEvent;
  if (onError) source.onerror = onError;
  return () => source.close();
}
