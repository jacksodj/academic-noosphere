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
}


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
    aws_region: str = "us-east-1"
    gateway_url: str | None = None  # AgentCore Gateway MCP endpoint
    web_search_enabled: bool = True  # spike verdict HEALTHY -> narrative booster ON
    opus_model: str = "anthropic.claude-opus-5"
    haiku_model: str = "anthropic.claude-haiku-4-5"
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
