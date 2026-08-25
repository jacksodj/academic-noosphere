/**
 * Bedrock & Web Search setup panels (issue #28), shared by Onboarding and
 * Settings. BedrockCatalogPanel verifies the two configured models are
 * actually usable in this account; GatewayPanel detects existing AgentCore
 * gateways or creates one in-app (CFN stack + web-search connector pinned
 * 1.2.0). GatewayPanel is CONTROLLED: it reports the chosen URL via onChange
 * and never persists settings itself — the host view owns saving.
 */

import { useEffect, useRef, useState } from "react";
import { createWebSearchGateway, getBedrockCatalog, getWebSearch } from "./endpoints";
import type { BedrockCatalog, WebSearchStatus } from "./types";

export function BedrockCatalogPanel() {
  const [catalog, setCatalog] = useState<BedrockCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBedrockCatalog()
      .then(setCatalog)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (error) return <p className="error">Model catalog check failed — {error}</p>;
  if (!catalog) return <p className="muted">Checking Bedrock model access…</p>;

  const problems = catalog.models.filter((m) => m.authorized === false || !m.listed);
  return (
    <div className="bedrock-catalog">
      {catalog.models.map((m) => (
        <div key={m.role} className="cred-head">
          <span className="cred-label">
            {m.role === "opus" ? "Synthesis model" : "Volume model"}{" "}
            <span className="mono muted small">{m.model_id}</span>
          </span>
          {m.authorized === true || (m.authorized === null && m.listed) ? (
            <span className="cred-badge cred-set">
              {m.authorized === true ? "access granted" : "listed"}
            </span>
          ) : (
            <span className="cred-badge cred-unset">
              {m.listed ? "access not granted" : "not available in this region"}
            </span>
          )}
        </div>
      ))}
      {problems.length > 0 && (
        <p className="muted">
          Enable the missing model(s) under{" "}
          <a href={catalog.console_url} target="_blank" rel="noreferrer">
            Bedrock console → Model access ↗
          </a>{" "}
          (a few clicks; Anthropic models grant instantly), then re-open this step.
        </p>
      )}
    </div>
  );
}

export function GatewayPanel({
  value,
  onChange,
}: {
  value: string;
  onChange: (url: string) => void;
}) {
  const [status, setStatus] = useState<WebSearchStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [finding, setFinding] = useState(false);
  const timer = useRef<number | null>(null);
  const appliedRef = useRef(false);

  function refresh() {
    getWebSearch()
      .then(setStatus)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setFinding(false));
  }

  useEffect(() => {
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, []);

  // Poll while a creation is in flight; apply the URL exactly once on done.
  useEffect(() => {
    if (!status) return;
    if (status.create.status === "creating") {
      timer.current = window.setTimeout(refresh, 3000);
    } else if (
      status.create.status === "done" &&
      status.create.gateway_url &&
      !appliedRef.current
    ) {
      appliedRef.current = true;
      onChange(status.create.gateway_url);
    }
  }, [status, onChange]);

  const create = status?.create;
  return (
    <div className="gateway-panel">
      <label>
        AgentCore Gateway URL (optional — enables Web Search discovery)
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="https://…gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
        />
      </label>
      <div className="cred-controls">
        <button
          type="button"
          disabled={finding}
          onClick={() => {
            setFinding(true);
            setError(null);
            refresh();
          }}
        >
          {finding ? "Looking…" : "Find existing gateways"}
        </button>
        <button
          type="button"
          disabled={create?.status === "creating"}
          onClick={() => {
            setError(null);
            appliedRef.current = false;
            createWebSearchGateway()
              .then(() => refresh())
              .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
          }}
        >
          {create?.status === "creating" ? "Creating…" : "Create gateway"}
        </button>
      </div>
      {create?.status === "creating" && (
        <p className="muted">Creating (takes 1–3 minutes): {create.step}…</p>
      )}
      {create?.status === "failed" && (
        <p className="error">Gateway creation failed: {create.error}</p>
      )}
      {create?.status === "done" && create.gateway_url && (
        <p className="model-status ok">✓ Gateway ready — URL filled in above.</p>
      )}
      {status && status.gateways.length > 0 && (
        <ul className="gateway-list">
          {status.gateways.map((g) => (
            <li key={g.id}>
              <span className="mono small">{g.name ?? g.id}</span>{" "}
              <span className="muted small">
                {g.status ?? "?"}
                {g.web_search ? " · web-search target" : " · no web-search target"}
              </span>{" "}
              {g.url && (
                <button type="button" className="subtle" onClick={() => onChange(g.url!)}>
                  Use
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {status && status.gateways.length === 0 && !status.error && (
        <p className="muted small">No existing gateways found in this account/region.</p>
      )}
      {status?.error && <p className="muted small">Gateway listing unavailable: {status.error}</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
