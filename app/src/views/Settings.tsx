import { useEffect, useState } from "react";
import { apiConfig } from "../api";
import { AWS_CRED_NAMES, AwsCheck, CredentialsPanel, SCHOLARLY_CRED_NAMES } from "../credentials";
import { BedrockCatalogPanel, GatewayPanel } from "../awssetup";
import { EmbeddingModelPanel } from "../embedmodel";
import { getSettings, saveSettings } from "../endpoints";
import type { Settings as SettingsModel } from "../types";

export default function Settings() {
  const [settings, setSettings] = useState<SettingsModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  function update(patch: Partial<SettingsModel>) {
    setSaved(false);
    setSettings((s) => (s ? { ...s, ...patch } : s));
  }

  function updateWeight(key: string, value: number) {
    setSaved(false);
    setSettings((s) =>
      s ? { ...s, ranking_weights: { ...s.ranking_weights, [key]: value } } : s,
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!settings) return;
    setSaving(true);
    setError(null);
    try {
      setSettings(await saveSettings(settings));
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <div className="view-head">
        <h1>Settings</h1>
      </div>

      {apiConfig.mock && <p className="badge mock-badge">mock data (VITE_MOCK=1)</p>}
      {error && <p className="error">{error}</p>}
      {settings === null && !error && <p className="muted">Loading settings…</p>}

      {settings && (
        <form className="card form" onSubmit={submit}>
          <GatewayPanel
            value={settings.gateway_url ?? ""}
            onChange={(url) => update({ gateway_url: url || null })}
          />
          <label>
            AWS region
            <input
              value={settings.aws_region}
              onChange={(e) => update({ aws_region: e.target.value })}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={settings.web_search_enabled}
              onChange={(e) => update({ web_search_enabled: e.target.checked })}
            />
            Web Search enabled (narrative-evidence booster)
          </label>

          <fieldset>
            <legend>Gap ranking weights</legend>
            {Object.entries(settings.ranking_weights).map(([key, value]) => (
              <label key={key}>
                {key.replace(/_/g, " ")}
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  value={value}
                  onChange={(e) => updateWeight(key, Number(e.target.value))}
                />
              </label>
            ))}
          </fieldset>

          <div className="form-actions">
            <button className="primary" type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
            {saved && <span className="muted">Saved.</span>}
          </div>
        </form>
      )}

      <div className="card form">
        <h2>API credentials</h2>
        <p className="muted">
          Stored in the macOS Keychain (service <code>academic-noosphere</code>) — never in
          files or this repo. The core reads them at survey time.
        </p>
        <CredentialsPanel names={SCHOLARLY_CRED_NAMES} />
      </div>

      <div className="card form">
        <h2>AWS</h2>
        <p className="muted">
          Bedrock synthesis and Web Search use your ambient AWS identity (SSO profile via{" "}
          <code>AWS_PROFILE</code>) — nothing AWS-shaped is stored by the app. Session
          expired? Run <code>aws sso login --profile &lt;profile&gt;</code> and relaunch.
        </p>
        <p className="muted">
          Or paste ephemeral keys (SSO portal → “Command line or programmatic access”) —
          Keychain-stored, they take precedence over a profile and expire with the session:
        </p>
        <CredentialsPanel names={AWS_CRED_NAMES} />
        <AwsCheck />
        <h2 style={{ marginTop: "1.25rem" }}>Bedrock model access</h2>
        <BedrockCatalogPanel />
      </div>

      <div className="card form">
        <h2>Embedding model</h2>
        <EmbeddingModelPanel />
      </div>
    </section>
  );
}
