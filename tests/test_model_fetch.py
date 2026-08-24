"""ModelFetch: atomic install, checksum enforcement, progress accounting."""

import asyncio
import hashlib
import json

import pytest
import respx
from httpx import Response

from noosphere.pipeline.model_fetch import BASE_URL, FILES, ModelFetch

MODEL_BYTES = b"onnx-bytes-for-test" * 100


def _mock_repo(router: respx.MockRouter, model_sha: str) -> None:
    contents = {
        "recipe.json": json.dumps({"model_sha256": model_sha}).encode(),
        "tokenizer_config.json": b"{}",
        "tokenizer.json": b"{}",
        "model.onnx": MODEL_BYTES,
    }
    for name in FILES:
        body = contents[name]
        router.head(f"{BASE_URL}/{name}").mock(
            return_value=Response(200, headers={"content-length": str(len(body))})
        )
        router.get(f"{BASE_URL}/{name}").mock(return_value=Response(200, content=body))


@pytest.mark.asyncio
@respx.mock
async def test_download_installs_and_verifies(respx_mock, tmp_path):
    _mock_repo(respx_mock, hashlib.sha256(MODEL_BYTES).hexdigest())
    fetch = ModelFetch()
    assert fetch.start(tmp_path) is True
    assert fetch.start(tmp_path) is False  # no concurrent second download
    while fetch.status == "downloading":
        await asyncio.sleep(0.01)
    assert fetch.status == "done"
    assert (tmp_path / "model.onnx").read_bytes() == MODEL_BYTES
    assert not list(tmp_path.glob("*.part"))
    assert fetch.done_bytes == fetch.total_bytes > 0


@pytest.mark.asyncio
@respx.mock
async def test_checksum_mismatch_installs_nothing(respx_mock, tmp_path):
    _mock_repo(respx_mock, "0" * 64)  # wrong sha for the served bytes
    fetch = ModelFetch()
    fetch.start(tmp_path)
    while fetch.status == "downloading":
        await asyncio.sleep(0.01)
    assert fetch.status == "failed"
    assert "checksum" in (fetch.error or "")
    assert not (tmp_path / "model.onnx").exists()
