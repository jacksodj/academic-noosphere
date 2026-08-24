"""First-run download of the ONNX SPECTER2 artifact (ticket #22).

Pulls the parity-gated export from the project's Hugging Face repo into
data_dir/models/specter2-onnx. recipe.json arrives first so model.onnx can be
sha256-verified against it while streaming; every file lands as .part and is
renamed only when complete, so a killed download never leaves a half-artifact
that OnnxSpecter2Embedder.available() would trust.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

HF_REPO = "jacksodj/specter2-base-onnx"
BASE_URL = f"https://huggingface.co/{HF_REPO}/resolve/main"
# recipe.json first (holds the checksum), model.onnx last (the big one).
FILES = ["recipe.json", "tokenizer_config.json", "tokenizer.json", "model.onnx"]
CHUNK = 1 << 20


@dataclass
class ModelFetch:
    """Single in-process download; the API polls `snapshot()` for progress."""

    status: str = "idle"  # idle | downloading | done | failed
    done_bytes: int = 0
    total_bytes: int = 0
    error: str | None = None
    _task: asyncio.Task | None = field(default=None, repr=False)

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "done_bytes": self.done_bytes,
            "total_bytes": self.total_bytes,
            "error": self.error,
        }

    def start(self, dest: Path) -> bool:
        """Begin downloading unless already running; True if a task started."""
        if self._task is not None and not self._task.done():
            return False
        self.status = "downloading"
        self.done_bytes = 0
        self.total_bytes = 0
        self.error = None
        self._task = asyncio.get_running_loop().create_task(self._run(dest))
        return True

    async def _run(self, dest: Path) -> None:
        try:
            dest.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                # Sizes up front so progress covers the whole artifact.
                sizes: dict[str, int] = {}
                for name in FILES:
                    head = await client.head(f"{BASE_URL}/{name}")
                    head.raise_for_status()
                    sizes[name] = int(head.headers.get("content-length", 0))
                self.total_bytes = sum(sizes.values())

                expected_sha: str | None = None
                for name in FILES:
                    part = dest / (name + ".part")
                    sha = hashlib.sha256()
                    async with client.stream("GET", f"{BASE_URL}/{name}") as resp:
                        resp.raise_for_status()
                        with part.open("wb") as fh:
                            async for chunk in resp.aiter_bytes(CHUNK):
                                fh.write(chunk)
                                sha.update(chunk)
                                self.done_bytes += len(chunk)
                    if name == "recipe.json":
                        expected_sha = json.loads(part.read_text()).get("model_sha256")
                    if name == "model.onnx":
                        if expected_sha and sha.hexdigest() != expected_sha:
                            part.unlink(missing_ok=True)
                            raise RuntimeError(
                                "model.onnx checksum mismatch against recipe.json — "
                                "download corrupted or repo inconsistent; not installing"
                            )
                    part.replace(dest / name)
            self.status = "done"
            logger.info("embedding model installed at %s", dest)
        except Exception as e:  # surfaced to the UI via snapshot()
            self.status = "failed"
            self.error = str(e)
            logger.warning("embedding model download failed: %s", e)


fetcher = ModelFetch()
