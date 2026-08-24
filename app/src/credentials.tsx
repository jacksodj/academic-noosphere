/**
 * Credential UI shared by Settings and the first-start Onboarding wizard.
 *
 * Values are write-only: the core stores them in the macOS Keychain (service
 * "academic-noosphere") and only ever reports presence/source/hint back.
 * Rows sourced from an env override are read-only here — they belong to the
 * launching shell, not the Keychain.
 */

import { useCallback, useEffect, useState } from "react";
import { checkAws, clearCredential, listCredentials, setCredential } from "./endpoints";
import type { AwsCheckResult, CredentialStatus } from "./types";

export interface CredentialMeta {
  name: string;
  label: string;
  required: boolean;
  secret: boolean;
  placeholder: string;
  /** One-line "how to get it". */
  howTo: string;
  link: string | null;
  linkLabel: string | null;
}

export const SCHOLARLY_CRED_NAMES = [
  "crossref_mailto",
  "openalex_api_key",
  "s2_api_key",
  "ncbi_api_key",
];

export const AWS_CRED_NAMES = [
  "aws_access_key_id",
  "aws_secret_access_key",
  "aws_session_token",
];

export const CREDENTIAL_META: CredentialMeta[] = [
  {
    name: "crossref_mailto",
    label: "Contact email (polite pool)",
    required: true,
    secret: false,
    placeholder: "you@example.com",
    howTo:
      "Your email, sent with OpenAlex/Crossref requests — it routes you to their faster “polite” pools. No signup.",
    link: null,
    linkLabel: null,
  },
  {
    name: "openalex_api_key",
    label: "OpenAlex API key",
    required: true,
    secret: true,
    placeholder: "paste key",
    howTo: "Free key, instant. OpenAlex is the primary corpus + citation-graph source.",
    link: "https://help.openalex.org/api/authentication",
    linkLabel: "get a key",
  },
  {
    name: "s2_api_key",
    label: "Semantic Scholar API key",
    required: false,
    secret: true,
    placeholder: "paste key (optional)",
    howTo: "Free, granted on request (~days). Used for lazy enrichment only.",
    link: "https://www.semanticscholar.org/product/api",
    linkLabel: "request a key",
  },
  {
    name: "ncbi_api_key",
    label: "NCBI API key",
    required: false,
    secret: true,
    placeholder: "paste key (optional)",
    howTo: "Free, from NCBI account settings. Raises PubMed limits to 10 req/s.",
    link: "https://account.ncbi.nlm.nih.gov/settings/",
    linkLabel: "account settings",
  },
  {
    name: "aws_access_key_id",
    label: "AWS access key ID",
    required: false,
    secret: false,
    placeholder: "ASIA…",
    howTo:
      "Alternative to an SSO profile: paste the three values from your SSO portal's “Command line or programmatic access” page.",
    link: null,
    linkLabel: null,
  },
  {
    name: "aws_secret_access_key",
    label: "AWS secret access key",
    required: false,
    secret: true,
    placeholder: "paste secret key",
    howTo: "Second of the three pasted values.",
    link: null,
    linkLabel: null,
  },
  {
    name: "aws_session_token",
    label: "AWS session token",
    required: false,
    secret: true,
    placeholder: "paste session token",
    howTo:
      "Third value. Ephemeral — typically expires after 1–12 h; when “Test AWS access” starts failing, paste a fresh set. Pasted keys take precedence over an ambient profile.",
    link: null,
    linkLabel: null,
  },
];

function sourceBadge(cred: CredentialStatus): { text: string; className: string } {
  if (!cred.set) return { text: "not set", className: "cred-badge cred-unset" };
  if (cred.source === "env")
    return { text: `env ${cred.env_var}`, className: "cred-badge cred-env" };
  return { text: "Keychain", className: "cred-badge cred-set" };
}

function CredentialRow({
  meta,
  cred,
  onChange,
}: {
  meta: CredentialMeta;
  cred: CredentialStatus;
  onChange: (next: CredentialStatus) => void;
}) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!draft.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      onChange(await setCredential(meta.name, draft.trim()));
      setDraft("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    setBusy(true);
    setError(null);
    try {
      onChange(await clearCredential(meta.name));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const badge = sourceBadge(cred);
  const fromEnv = cred.source === "env";

  return (
    <div className="cred-row">
      <div className="cred-head">
        <span className="cred-label">
          {meta.label}
          {meta.required && <span className="cred-required" title="needed for surveys"> *</span>}
        </span>
        <span className={badge.className}>{badge.text}</span>
        {cred.hint && <span className="cred-hint mono">{cred.hint}</span>}
      </div>
      <p className="cred-howto muted">
        {meta.howTo}{" "}
        {meta.link && (
          <a href={meta.link} target="_blank" rel="noreferrer">
            {meta.linkLabel} ↗
          </a>
        )}
      </p>
      {fromEnv ? (
        <p className="muted cred-env-note">
          Set by the environment — unset {cred.env_var} in your shell to manage it here.
        </p>
      ) : (
        <div className="cred-controls">
          <input
            type={meta.secret ? "password" : "email"}
            value={draft}
            placeholder={cred.set ? "replace…" : meta.placeholder}
            autoComplete="off"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void save();
              }
            }}
          />
          <button type="button" disabled={!draft.trim() || busy} onClick={() => void save()}>
            {cred.set ? "Replace" : "Save"}
          </button>
          {cred.set && (
            <button type="button" className="subtle" disabled={busy} onClick={() => void clear()}>
              Clear
            </button>
          )}
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

/**
 * The credential list. `names` limits which credentials show (wizard steps);
 * `onStatus` reports every refresh so callers can gate "Continue" buttons.
 */
export function CredentialsPanel({
  names,
  onStatus,
}: {
  names?: string[];
  onStatus?: (creds: CredentialStatus[]) => void;
}) {
  const [creds, setCreds] = useState<CredentialStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const publish = useCallback(
    (list: CredentialStatus[]) => {
      setCreds(list);
      onStatus?.(list);
    },
    [onStatus],
  );

  useEffect(() => {
    listCredentials()
      .then(publish)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [publish]);

  if (error) return <p className="error">{error}</p>;
  if (!creds) return <p className="muted">Loading credentials…</p>;

  const visible = CREDENTIAL_META.filter((m) => !names || names.includes(m.name));
  return (
    <div className="cred-panel">
      {visible.map((meta) => {
        const cred = creds.find((c) => c.name === meta.name);
        if (!cred) return null;
        return (
          <CredentialRow
            key={meta.name}
            meta={meta}
            cred={cred}
            onChange={(next) => publish(creds.map((c) => (c.name === next.name ? next : c)))}
          />
        );
      })}
    </div>
  );
}

/** "Test AWS access" button + result line (STS identity via the core). */
export function AwsCheck() {
  const [result, setResult] = useState<AwsCheckResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setResult(null);
    try {
      setResult(await checkAws());
    } catch (e) {
      setResult({ ok: false, profile: null, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="aws-check">
      <button type="button" disabled={busy} onClick={() => void run()}>
        {busy ? "Checking…" : "Test AWS access"}
      </button>
      {result &&
        (result.ok ? (
          <span className="cred-badge cred-set">
            OK — account {result.account}
            {result.profile ? ` (profile ${result.profile})` : ""}
          </span>
        ) : (
          <span className="cred-badge cred-unset" title={result.error}>
            not connected{result.error ? ` — ${result.error.slice(0, 120)}` : ""}
          </span>
        ))}
    </div>
  );
}
