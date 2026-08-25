"""Settings and credentials.

BYO-credentials rule (#8): keys come from the macOS Keychain (service
"academic-noosphere") with environment-variable override for development.
Nothing credential-shaped is ever read from files in the repo.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

KEYCHAIN_SERVICE = "academic-noosphere"

# credential name -> env override
CRED_KEYS = {
    "openalex_api_key": "NOOSPHERE_OPENALEX_KEY",
    "s2_api_key": "NOOSPHERE_S2_KEY",
    "ncbi_api_key": "NOOSPHERE_NCBI_KEY",
    "crossref_mailto": "NOOSPHERE_CROSSREF_MAILTO",
    # Ephemeral AWS credentials (issue #23): pasted from an SSO portal's
    # "command line access" page. These map to the REAL AWS env names because
    # boto3 / anthropic / SigV4 all read them ambiently — sync_aws_env()
    # mirrors Keychain values into the process env.
    "aws_access_key_id": "AWS_ACCESS_KEY_ID",
    "aws_secret_access_key": "AWS_SECRET_ACCESS_KEY",
    "aws_session_token": "AWS_SESSION_TOKEN",
    # Long-lived Bedrock API key (issue #24): bearer auth for Bedrock only.
    # Both anthropic.AnthropicBedrock and botocore (>=1.39) read this env var
    # natively and switch to Authorization: Bearer for bedrock endpoints —
    # no expiry dance, but it covers model calls only (STS and the SigV4
    # Web Search Gateway still need the three SigV4 values or a profile).
    "bedrock_api_key": "AWS_BEARER_TOKEN_BEDROCK",
}

# Names whose values are secrets (status hints are masked to the last 4 chars).
# crossref_mailto is an email and aws_access_key_id is a public identifier
# (AWS's own consoles display it) — both shown in full.
CRED_SECRET = frozenset(
    {
        "openalex_api_key",
        "s2_api_key",
        "ncbi_api_key",
        "aws_secret_access_key",
        "aws_session_token",
        "bedrock_api_key",
    }
)

AWS_CRED_NAMES = (
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "bedrock_api_key",
)

# AWS env vars this process itself populated from the Keychain — so a var the
# launching shell exported is never clobbered or popped (shell wins, matching
# the env-first rule every credential already follows).
_APPLIED_AWS_ENV: set[str] = set()


def sync_aws_env() -> None:
    """Mirror Keychain AWS credentials into os.environ for ambient consumers.

    Called at core startup and after every set/delete of an aws_* credential.
    A GUI-launched app has no shell exports, so pasted ephemeral keys are the
    only way credentials reach boto3/anthropic there.
    """
    for name in AWS_CRED_NAMES:
        env = CRED_KEYS[name]
        shell_owned = env in os.environ and env not in _APPLIED_AWS_ENV
        if shell_owned:
            continue
        try:
            import keyring

            value = keyring.get_password(KEYCHAIN_SERVICE, name)
        except Exception:
            value = None
        if value:
            os.environ[env] = value
            _APPLIED_AWS_ENV.add(env)
        elif env in _APPLIED_AWS_ENV:
            os.environ.pop(env, None)
            _APPLIED_AWS_ENV.discard(env)


def get_credential(name: str) -> str | None:
    """Env override first (dev), then Keychain. Returns None if absent."""
    env = CRED_KEYS.get(name)
    if env and (v := os.environ.get(env)):
        return v
    try:
        import keyring
        return keyring.get_password(KEYCHAIN_SERVICE, name)
    except Exception:
        return None


def set_credential(name: str, value: str) -> None:
    """Store a credential in the Keychain. Raises KeyError for unknown names;
    Keychain backend errors propagate (the API layer turns them into 500s)."""
    if name not in CRED_KEYS:
        raise KeyError(name)
    import keyring

    keyring.set_password(KEYCHAIN_SERVICE, name, value)
    if name in AWS_CRED_NAMES:
        sync_aws_env()


def delete_credential(name: str) -> None:
    """Remove a credential from the Keychain (no-op if absent). Env overrides
    are not touched — they belong to the launching shell, not to us."""
    if name not in CRED_KEYS:
        raise KeyError(name)
    import keyring

    try:
        keyring.delete_password(KEYCHAIN_SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass
    if name in AWS_CRED_NAMES:
        sync_aws_env()


def credential_status(name: str) -> dict:
    """Presence/source of one credential — never the value itself.

    ``hint`` is the last 4 chars for secrets (enough to recognize which key),
    the full value for non-secrets (crossref_mailto is just an email).
    """
    env = CRED_KEYS[name]
    env_value = os.environ.get(env)
    keychain_value = None
    if not env_value:
        try:
            import keyring

            keychain_value = keyring.get_password(KEYCHAIN_SERVICE, name)
        except Exception:
            keychain_value = None
    value = env_value or keychain_value
    hint = None
    if value:
        hint = value if name not in CRED_SECRET else f"…{value[-4:]}"
    return {
        "name": name,
        "env_var": env,
        "set": bool(value),
        "source": "env" if env_value else ("keychain" if keychain_value else None),
        "hint": hint,
    }


def credentials_status() -> list[dict]:
    """Status for every known credential, in CRED_KEYS order."""
    return [credential_status(name) for name in CRED_KEYS]


def data_dir() -> Path:
    """Per-user data root: graph DB, sidecar DB, exports."""
    root = os.environ.get("NOOSPHERE_DATA_DIR")
    p = Path(root) if root else Path.home() / "Library" / "Application Support" / "academic-noosphere"
    if not p.parent.exists():  # non-macOS (dev/CI)
        p = Path(root) if root else Path.home() / ".local" / "share" / "academic-noosphere"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Settings:
    onboarded: bool = False  # first-start wizard completed/skipped
    aws_region: str = "us-east-1"
    gateway_url: str | None = None  # AgentCore Gateway MCP endpoint
    web_search_enabled: bool = True  # spike verdict HEALTHY -> narrative booster ON
    # Classic bedrock-runtime ids need the `us.` cross-region inference-profile
    # prefix. Opus 5 / Sonnet 5 / Opus 4.8 are not yet entitled on this account
    # (probed 2026-08-22) — Opus 4.6 is the strongest invocable synthesis model;
    # bump these in Settings once newer models are enabled in the Bedrock console.
    opus_model: str = "us.anthropic.claude-opus-4-6-v1"
    haiku_model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    coarse_corpus_target: int = 8000  # ~5-10k soft target for Phase 1
    relevance_threshold: float = 0.35
    ranking_weights: dict[str, float] = field(default_factory=lambda: {
        "sparsity": 1.0, "narrative_demand": 1.0, "recency": 0.5, "low_citedness": 0.5,
    })

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        if url := os.environ.get("NOOSPHERE_GATEWAY_URL"):
            s.gateway_url = url
        if region := os.environ.get("NOOSPHERE_AWS_REGION"):
            s.aws_region = region
        return s
