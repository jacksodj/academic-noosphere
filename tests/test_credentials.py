"""Credential storage + /api/credentials + /api/aws/check.

keyring is replaced with an in-memory fake so tests never touch the real
Keychain (and pass on Linux CI, where no backend exists).
"""

from __future__ import annotations

from pathlib import Path

import keyring
import keyring.errors
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noosphere import config
from noosphere.api.routes import router
from noosphere.api.state import AppState
from noosphere.config import CRED_KEYS, Settings


@pytest.fixture
def fake_keychain(monkeypatch: pytest.MonkeyPatch) -> dict:
    store: dict[tuple[str, str], str] = {}

    def set_password(service: str, name: str, value: str) -> None:
        store[(service, name)] = value

    def get_password(service: str, name: str) -> str | None:
        return store.get((service, name))

    def delete_password(service: str, name: str) -> None:
        if (service, name) not in store:
            raise keyring.errors.PasswordDeleteError(name)
        del store[(service, name)]

    monkeypatch.setattr(keyring, "set_password", set_password)
    monkeypatch.setattr(keyring, "get_password", get_password)
    monkeypatch.setattr(keyring, "delete_password", delete_password)
    # Env overrides from the developer's shell must not leak into assertions.
    for env in CRED_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    return store


@pytest.fixture
def client(tmp_path: Path, fake_keychain: dict) -> TestClient:
    state = AppState.build(tmp_path, Settings())
    app = FastAPI()
    app.include_router(router)
    app.state.noosphere = state
    with TestClient(app) as c:
        yield c
    state.close()


class TestConfigLayer:
    def test_set_get_delete_roundtrip(self, fake_keychain: dict) -> None:
        config.set_credential("openalex_api_key", "sk-test-1234abcd")
        assert config.get_credential("openalex_api_key") == "sk-test-1234abcd"
        config.delete_credential("openalex_api_key")
        assert config.get_credential("openalex_api_key") is None

    def test_delete_absent_is_noop(self, fake_keychain: dict) -> None:
        config.delete_credential("s2_api_key")  # no raise

    def test_unknown_name_raises(self, fake_keychain: dict) -> None:
        with pytest.raises(KeyError):
            config.set_credential("nonsense", "x")
        with pytest.raises(KeyError):
            config.delete_credential("nonsense")

    def test_env_override_wins(
        self, fake_keychain: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config.set_credential("openalex_api_key", "from-keychain")
        monkeypatch.setenv("NOOSPHERE_OPENALEX_KEY", "from-env")
        assert config.get_credential("openalex_api_key") == "from-env"
        status = config.credential_status("openalex_api_key")
        assert status["source"] == "env"

    def test_status_masks_secrets(self, fake_keychain: dict) -> None:
        config.set_credential("openalex_api_key", "sk-test-1234abcd")
        status = config.credential_status("openalex_api_key")
        assert status["set"] is True
        assert status["source"] == "keychain"
        assert status["hint"] == "…abcd"
        assert "sk-test" not in str(status)

    def test_status_shows_mailto_in_full(self, fake_keychain: dict) -> None:
        config.set_credential("crossref_mailto", "you@example.com")
        assert config.credential_status("crossref_mailto")["hint"] == "you@example.com"

    def test_status_unset(self, fake_keychain: dict) -> None:
        status = config.credential_status("ncbi_api_key")
        assert status == {
            "name": "ncbi_api_key",
            "env_var": "NOOSPHERE_NCBI_KEY",
            "set": False,
            "source": None,
            "hint": None,
        }


class TestCredentialRoutes:
    def test_list_covers_all_names(self, client: TestClient) -> None:
        body = client.get("/api/credentials").json()
        assert [c["name"] for c in body] == list(CRED_KEYS)
        assert all(c["set"] is False for c in body)

    def test_put_then_list_never_echoes_value(self, client: TestClient) -> None:
        res = client.put(
            "/api/credentials/openalex_api_key", json={"value": "sk-live-zzzz9999"}
        )
        assert res.status_code == 200
        assert res.json()["hint"] == "…9999"
        assert "sk-live" not in res.text
        listed = client.get("/api/credentials").json()
        assert "sk-live" not in str(listed)

    def test_put_strips_whitespace(self, client: TestClient) -> None:
        client.put("/api/credentials/crossref_mailto", json={"value": "  a@b.co  "})
        assert config.get_credential("crossref_mailto") == "a@b.co"

    def test_put_rejects_empty_and_unknown(self, client: TestClient) -> None:
        assert (
            client.put("/api/credentials/openalex_api_key", json={"value": "  "})
        ).status_code == 422
        assert (
            client.put("/api/credentials/nope", json={"value": "x"})
        ).status_code == 404

    def test_delete(self, client: TestClient) -> None:
        client.put("/api/credentials/s2_api_key", json={"value": "abc12345"})
        res = client.delete("/api/credentials/s2_api_key")
        assert res.status_code == 200
        assert res.json()["set"] is False
        assert client.delete("/api/credentials/nope").status_code == 404


class TestAwsCheck:
    def test_reports_failure_without_credentials(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for env in (
            "AWS_PROFILE",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_BEARER_TOKEN_BEDROCK",
        ):
            monkeypatch.delenv(env, raising=False)
        monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
        monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
        monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
        body = client.post("/api/aws/check").json()
        assert body["ok"] is False
        assert body["error"]

    def test_reports_identity(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import noosphere.api.routes as routes_mod

        async def fake_to_thread(fn, *a, **kw):
            if fn.__name__ == "_sts":
                return {"account": "123456789012", "arn": "arn:aws:iam::123456789012:user/x"}
            return {"models": 5}

        monkeypatch.setattr(routes_mod.asyncio, "to_thread", fake_to_thread)
        monkeypatch.setenv("AWS_PROFILE", "research")
        monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
        body = client.post("/api/aws/check").json()
        assert body["ok"] is True
        assert body["account"] == "123456789012"
        assert body["arn"] == "arn:aws:iam::123456789012:user/x"
        assert body["profile"] == "research"
        assert body["sigv4"]["ok"] is True
        assert body["bedrock"] == {"ok": True, "auth": "sigv4", "models": 5}

    def test_bedrock_probe_reports_bearer_auth(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import noosphere.api.routes as routes_mod

        async def fake_to_thread(fn, *a, **kw):
            if fn.__name__ == "_sts":
                raise RuntimeError("ExpiredToken: The security token included is expired")
            return {"models": 3}

        monkeypatch.setattr(routes_mod.asyncio, "to_thread", fake_to_thread)
        monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-api-key-value")
        body = client.post("/api/aws/check").json()
        # Expired SigV4 no longer means Bedrock is down: the bearer path holds.
        assert body["ok"] is False
        assert "ExpiredToken" in body["sigv4"]["error"]
        assert body["bedrock"] == {"ok": True, "auth": "bearer", "models": 3}


# -- ephemeral AWS credentials (issue #23) -----------------------------------


@pytest.fixture(autouse=False)
def clean_aws_env(monkeypatch: pytest.MonkeyPatch):
    for var in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_BEARER_TOKEN_BEDROCK",
    ):
        monkeypatch.delenv(var, raising=False)
    applied = config._APPLIED_AWS_ENV
    saved = set(applied)
    applied.clear()
    yield
    applied.clear()
    applied.update(saved)


def test_set_aws_credential_mirrors_into_env(fake_keychain, clean_aws_env) -> None:
    import os

    config.set_credential("aws_access_key_id", "ASIAEXAMPLE123456789")
    config.set_credential("aws_secret_access_key", "secret-value")
    config.set_credential("aws_session_token", "token-value")
    assert os.environ["AWS_ACCESS_KEY_ID"] == "ASIAEXAMPLE123456789"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "secret-value"
    assert os.environ["AWS_SESSION_TOKEN"] == "token-value"


def test_delete_aws_credential_pops_env(fake_keychain, clean_aws_env) -> None:
    import os

    config.set_credential("aws_session_token", "token-value")
    assert os.environ["AWS_SESSION_TOKEN"] == "token-value"
    config.delete_credential("aws_session_token")
    assert "AWS_SESSION_TOKEN" not in os.environ


def test_shell_env_never_clobbered_or_popped(
    fake_keychain, clean_aws_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "shell-owned")
    config.set_credential("aws_access_key_id", "keychain-value")
    assert os.environ["AWS_ACCESS_KEY_ID"] == "shell-owned"  # shell wins
    config.delete_credential("aws_access_key_id")
    assert os.environ["AWS_ACCESS_KEY_ID"] == "shell-owned"  # never popped


def test_aws_access_key_id_hint_unmasked(fake_keychain, clean_aws_env) -> None:
    config.set_credential("aws_access_key_id", "ASIAEXAMPLE123456789")
    status = config.credential_status("aws_access_key_id")
    assert status["hint"] == "ASIAEXAMPLE123456789"
    config.set_credential("aws_secret_access_key", "verysecretvalue")
    masked = config.credential_status("aws_secret_access_key")
    assert masked["hint"] == "…alue"


def test_bedrock_api_key_mirrors_and_pops(fake_keychain, clean_aws_env) -> None:
    import os

    config.set_credential("bedrock_api_key", "bedrock-long-lived-key")
    assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == "bedrock-long-lived-key"
    status = config.credential_status("bedrock_api_key")
    assert status["set"] is True
    assert status["hint"] == "…-key"  # masked: secret shows last 4 chars only
    config.delete_credential("bedrock_api_key")
    assert "AWS_BEARER_TOKEN_BEDROCK" not in os.environ


def test_bedrock_api_key_never_clobbers_shell_export(
    fake_keychain, clean_aws_env, monkeypatch
) -> None:
    import os

    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "from-shell")
    config.set_credential("bedrock_api_key", "from-keychain")
    assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == "from-shell"
