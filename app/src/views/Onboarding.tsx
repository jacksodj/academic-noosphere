/**
 * First-start wizard. Shown by App when settings.onboarded is false; every
 * step is skippable and finishing (or skipping out) persists onboarded=true
 * via PUT /api/settings, so it never reappears. Credentials go straight to
 * the Keychain through the same write-only API the Settings view uses.
 */

import { useState } from "react";
import { AWS_CRED_NAMES, AwsCheck, CredentialsPanel, SCHOLARLY_CRED_NAMES } from "../credentials";
import { EmbeddingModelPanel } from "../embedmodel";
import { saveSettings } from "../endpoints";
import type { CredentialStatus, Settings } from "../types";

const STEPS = ["Welcome", "Scholarly APIs", "AWS", "Embedding model", "Done"] as const;

export default function Onboarding({
  settings,
  onDone,
}: {
  settings: Settings;
  onDone: (settings: Settings) => void;
}) {
  const [step, setStep] = useState(0);
  const [creds, setCreds] = useState<CredentialStatus[]>([]);
  const [gatewayUrl, setGatewayUrl] = useState(settings.gateway_url ?? "");
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function finish() {
    setFinishing(true);
    setError(null);
    try {
      const next = await saveSettings({
        ...settings,
        onboarded: true,
        gateway_url: gatewayUrl.trim() || null,
      });
      onDone(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setFinishing(false);
    }
  }

  const requiredSet = ["crossref_mailto", "openalex_api_key"].every(
    (name) => creds.find((c) => c.name === name)?.set,
  );

  return (
    <div className="onboarding">
      <div className="card form onboarding-card">
        <div className="onboarding-steps">
          {STEPS.map((label, i) => (
            <span key={label} className={`onboarding-step${i === step ? " active" : ""}`}>
              {i + 1}. {label}
            </span>
          ))}
        </div>

        {step === 0 && (
          <>
            <h1>Welcome to Academic Noosphere</h1>
            <p>
              This app surveys an academic field, builds an author/topic/citation graph from
              scholarly APIs, and surfaces grounded literature gaps.
            </p>
            <p>Two minutes of setup, all optional and changeable later in Settings:</p>
            <ul className="muted">
              <li>
                <strong>Scholarly APIs</strong> — an email + a free OpenAlex key (the corpus
                source).
              </li>
              <li>
                <strong>AWS</strong> — an SSO profile for Bedrock synthesis + Web Search.
                Without it you can still ingest and explore; gap synthesis needs it.
              </li>
            </ul>
            <p className="muted">
              Keys are stored in your macOS Keychain (service <code>academic-noosphere</code>),
              never in files.
            </p>
          </>
        )}

        {step === 1 && (
          <>
            <h1>Scholarly APIs</h1>
            <CredentialsPanel names={SCHOLARLY_CRED_NAMES} onStatus={setCreds} />
            {!requiredSet && (
              <p className="muted">
                The two starred entries are what v1 ingest actually uses — the rest can wait.
              </p>
            )}
          </>
        )}

        {step === 2 && (
          <>
            <h1>AWS (Bedrock + Web Search)</h1>
            <p className="muted">
              One-time:{" "}
              <a
                href="https://docs.aws.amazon.com/cli/latest/userguide/sso-configure-profile-token.html"
                target="_blank"
                rel="noreferrer"
              >
                configure an SSO profile ↗
              </a>{" "}
              — then per session, before launching the app:
            </p>
            <pre className="mono onboarding-shell">
              {"aws configure sso                      # once\n" +
                "aws sso login --profile <profile>      # per session\n" +
                "export AWS_PROFILE=<profile>"}
            </pre>
            <p className="muted">
              The identity needs <code>bedrock:InvokeModel</code> (Opus 5 + Haiku 4.5) and,
              for Web Search, <code>bedrock-agentcore:InvokeGateway</code>.
            </p>
            <p className="muted" style={{ marginTop: "1rem" }}>
              <strong>No SSO / launching from Finder?</strong> Paste ephemeral keys instead —
              they're stored in the Keychain and take precedence over a profile:
            </p>
            <CredentialsPanel names={AWS_CRED_NAMES} />
            <AwsCheck />
            <label style={{ marginTop: "1rem" }}>
              AgentCore Gateway URL (optional — enables Web Search discovery)
              <input
                value={gatewayUrl}
                onChange={(e) => setGatewayUrl(e.target.value)}
                placeholder="https://…gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
              />
            </label>
            <p className="muted">
              No Gateway yet? <code>uv run scripts/phase0_infra.py up</code> creates one and
              prints this URL (see docs/getting-started.md §3.2).
            </p>
          </>
        )}

        {step === 3 && (
          <>
            <h1>Embedding model</h1>
            <p className="muted">
              Surveys score relevance with SPECTER2, a scientific-paper embedding model. It
              downloads once (~420 MB) and runs entirely on this Mac.
            </p>
            <EmbeddingModelPanel />
          </>
        )}

        {step === 4 && (
          <>
            <h1>Ready</h1>
            <p>
              {requiredSet
                ? "Scholarly credentials are in place. Start your first Survey from the Dashboard — a field name plus a few seed queries."
                : "You can start exploring now; add credentials any time under Settings before your first Survey."}
            </p>
            {error && <p className="error">{error}</p>}
          </>
        )}

        <div className="form-actions onboarding-actions">
          {step > 0 && (
            <button type="button" className="subtle" onClick={() => setStep(step - 1)}>
              Back
            </button>
          )}
          {step < STEPS.length - 1 ? (
            <>
              <button type="button" className="primary" onClick={() => setStep(step + 1)}>
                Continue
              </button>
              <button type="button" className="subtle" disabled={finishing} onClick={() => void finish()}>
                Skip setup
              </button>
            </>
          ) : (
            <button type="button" className="primary" disabled={finishing} onClick={() => void finish()}>
              {finishing ? "Saving…" : "Open Dashboard"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
